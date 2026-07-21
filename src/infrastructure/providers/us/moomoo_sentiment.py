"""Deterministic Moomoo community-feed sentiment mining.

The provider retrieves and normalizes public feed items. It does not invoke a
Skill or an LLM. Interpretation and narrative synthesis remain host concerns.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from typing import Final

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_context.enums import (
    USSentimentDirection,
    USSentimentLabelOrigin,
    USSentimentSource,
)
from domain.us_context.models import USSentimentSample
from infrastructure.system.clock import SystemClock

_ENDPOINT: Final = "https://ai-news-search.moomoo.com/stock_feed"
_CLASSIFIER_VERSION: Final = "moomoo_rules_v1"
_CACHE_TTL_SECONDS: Final = 900
_CURRENT_WINDOW_SECONDS: Final = 300
_DEFAULT_FETCH_SIZE: Final = 30
_MAX_FETCH_SIZE: Final = 50
_WARNINGS: Final = (
    "MOOMOO_SENTIMENT_DETERMINISTIC_RULES",
    "MOOMOO_SENTIMENT_RECENT_SNAPSHOT_ONLY",
    "MOOMOO_SENTIMENT_ENGAGEMENT_UNAVAILABLE",
)

_CASHTAG_RE: Final = re.compile(r"\$([A-Z][A-Z0-9.-]{0,14})\b", re.IGNORECASE)
_DOTTED_SYMBOL_RE: Final = re.compile(r"\b([A-Z][A-Z0-9.-]{0,14})\.US\b", re.IGNORECASE)
_STOCK_DISPLAY_RE: Final = re.compile(r"\$[^$]{1,160}\$", re.IGNORECASE)
_INFORMATIVE_RE: Final = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")
_SPACE_RE: Final = re.compile(r"\s+")
_SPAM_RE: Final = re.compile(
    r"\b(?:referral|promo\s*code|whatsapp|telegram|join\s+my|guaranteed\s+profit)\b|"
    r"加群|荐股群|稳赚|保本",
    re.IGNORECASE,
)
_FILLERS: Final = frozenset(
    {
        "nice",
        "wow",
        "lol",
        "moon",
        "to the moon",
        "buy buy buy",
        "sell sell sell",
        "涨",
        "跌",
        "冲",
        "起飞",
    }
)

_BULLISH_CUES: Final = (
    "bullish",
    "bull case",
    "buy the dip",
    "upside",
    "breakout",
    "rebound",
    "rally",
    "undervalued",
    "earnings beat",
    "beat expectations",
    "strong demand",
    "raise guidance",
    "margin expansion",
    "record high",
    "看多",
    "利好",
    "买入",
    "加仓",
    "抄底",
    "上涨",
    "反弹",
    "突破",
    "超预期",
    "低估",
    "需求强劲",
    "利润率扩张",
    "创新高",
    "长期持有",
)
_BEARISH_CUES: Final = (
    "bearish",
    "bear case",
    "sell the rip",
    "downside",
    "breakdown",
    "overvalued",
    "earnings miss",
    "miss expectations",
    "weak demand",
    "cut guidance",
    "margin pressure",
    "dilution",
    "keep dropping",
    "crash",
    "看空",
    "利空",
    "卖出",
    "减仓",
    "下跌",
    "回调",
    "跌破",
    "不及预期",
    "高估",
    "需求疲软",
    "利润率承压",
    "稀释",
    "崩盘",
)


class _MarkupExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.symbols: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"br", "p", "div", "li"}:
            self.parts.append(" ")
        if tag.casefold() != "nnstock":
            return
        values = {key.casefold(): value for key, value in attrs}
        for key in ("stockcode", "stocksymbol"):
            value = values.get(key)
            if isinstance(value, str) and value.strip():
                self.symbols.add(value.strip().upper().split(".", 1)[0])

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


@dataclass(frozen=True, slots=True)
class _Loaded:
    samples: tuple[USSentimentSample, ...]
    fetched_at: datetime
    expires_at: datetime


def _markup(value: object) -> tuple[str, set[str]]:
    if not isinstance(value, str) or not value.strip():
        return "", set()
    parser = _MarkupExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        raise DataContractError("Moomoo sentiment markup is invalid") from None
    text = _SPACE_RE.sub(" ", html.unescape(" ".join(parser.parts))).strip()
    for pattern in (_CASHTAG_RE, _DOTTED_SYMBOL_RE):
        parser.symbols.update(match.upper().split(".", 1)[0] for match in pattern.findall(text))
    return text, parser.symbols


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    try:
        raw = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if raw > 1_000_000_000_000:
        raw //= 1_000
    try:
        return datetime.fromtimestamp(raw, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _quality_text(text: str) -> str | None:
    semantic = _SPACE_RE.sub(" ", _STOCK_DISPLAY_RE.sub(" ", text)).strip()
    normalized = semantic.casefold().strip(" .,!?:;-$")
    if (
        normalized in _FILLERS
        or len(semantic) < 20
        or len(_INFORMATIVE_RE.findall(semantic)) < 12
        or _SPAM_RE.search(semantic) is not None
    ):
        return None
    return text[:1_000]


def _classify(text: str) -> tuple[USSentimentDirection, Decimal]:
    normalized = text.casefold()
    bullish = sum(cue in normalized for cue in _BULLISH_CUES)
    bearish = sum(cue in normalized for cue in _BEARISH_CUES)
    total = bullish + bearish
    if total == 0 or bullish == bearish:
        return USSentimentDirection.NEUTRAL, Decimal(0)
    score = Decimal(bullish - bearish) / Decimal(total)
    if abs(score) < Decimal("0.25"):
        return USSentimentDirection.NEUTRAL, score
    direction = USSentimentDirection.BULLISH if score > 0 else USSentimentDirection.BEARISH
    return direction, score


class MoomooSentimentAdapter:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._transport = transport
        self._clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._timeout = float(timeout_seconds)
        self._cache: dict[str, _Loaded] = {}
        self._inflight: dict[str, asyncio.Task[_Loaded]] = {}
        self._lock = asyncio.Lock()

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.MOOMOO_FEED

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.SENTIMENT

    def is_configured(self) -> bool:
        return self._enabled

    async def _fetch(self, instrument: Instrument, fetch_size: int) -> _Loaded:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_ENDPOINT,
                params={"keyword": instrument.symbol.upper(), "size": str(fetch_size)},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "TradingPartner/1.0",
                },
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        if response.status_code == 429:
            raise ProviderRateLimitError("Moomoo sentiment feed rate limited")
        if not 200 <= response.status_code < 300:
            raise ProviderUnavailableError("Moomoo sentiment feed HTTP failure")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError):
            raise DataContractError("Moomoo sentiment response is not valid JSON") from None
        if not isinstance(payload, Mapping) or payload.get("code") != 0:
            raise ProviderUnavailableError("Moomoo sentiment feed returned an error")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise DataContractError("Moomoo sentiment payload has invalid shape")

        samples: dict[str, USSentimentSample] = {}
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            sample = self._sample(raw, instrument)
            if sample is not None:
                key = sample.text.casefold()
                current = samples.get(key)
                if current is None or sample.published_at > current.published_at:
                    samples[key] = sample
        fetched_at = self._clock.now()
        return _Loaded(
            samples=tuple(
                sorted(samples.values(), key=lambda item: item.published_at, reverse=True)
            ),
            fetched_at=fetched_at,
            expires_at=fetched_at + timedelta(seconds=_CACHE_TTL_SECONDS),
        )

    async def _load(self, instrument: Instrument, fetch_size: int) -> tuple[_Loaded, bool]:
        now = self._clock.now()
        cached = self._cache.get(instrument.instrument_id)
        if cached is not None and now < cached.expires_at:
            return cached, True
        key = instrument.instrument_id
        async with self._lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._fetch(instrument, fetch_size))
                self._inflight[key] = task
        try:
            loaded = await asyncio.shield(task)
            self._cache[key] = loaded
            return loaded, False
        finally:
            if task.done():
                async with self._lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    @staticmethod
    def _sample(raw: Mapping[str, object], instrument: Instrument) -> USSentimentSample | None:
        title, title_symbols = _markup(raw.get("title"))
        description, description_symbols = _markup(raw.get("desc"))
        if instrument.symbol.upper() not in title_symbols | description_symbols:
            return None
        if title and description and title.casefold() == description.casefold():
            combined = title
        else:
            combined = _SPACE_RE.sub(" ", f"{title} {description}").strip()
        text = _quality_text(combined)
        published_at = _timestamp(raw.get("publish_time"))
        if text is None or published_at is None:
            return None
        raw_id = raw.get("id")
        if not isinstance(raw_id, (str, int)) or not str(raw_id).strip():
            return None
        url = raw.get("url")
        safe_url = url[:2_000] if isinstance(url, str) and url.strip() else None
        direction, score = _classify(text)
        return USSentimentSample(
            instrument_id=instrument.instrument_id,
            source=USSentimentSource.MOOMOO,
            published_at=published_at,
            text=text,
            direction=direction,
            label_origin=USSentimentLabelOrigin.DETERMINISTIC_INFERENCE,
            score=score,
            likes=None,
            comments=None,
            url=safe_url,
            classifier_version=_CLASSIFIER_VERSION,
        )

    async def get_sentiment_samples(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USSentimentSample, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        if not self.is_configured():
            raise ProviderNotConfigured("Moomoo sentiment adapter is disabled")
        now = self._clock.now()
        if as_of > now:
            raise DataContractError("as_of must not be in the future", details={"field": "as_of"})
        if now - as_of > timedelta(seconds=_CURRENT_WINDOW_SECONDS):
            raise NoMarketData("Moomoo sentiment feed is current-only")
        if type(limit) is not int or limit <= 0:
            raise DataContractError("limit must be a positive integer")
        fetch_size = min(_MAX_FETCH_SIZE, max(_DEFAULT_FETCH_SIZE, limit))
        loaded, cache_hit = await self._load(instrument, fetch_size)
        selected = []
        for sample in loaded.samples:
            if sample.published_at > as_of:
                continue
            day = sample.published_at.date()
            if (start is not None and day < start) or (end is not None and day > end):
                continue
            selected.append(sample)
        value = tuple(selected[:limit])
        delay = max(0, int((now - loaded.fetched_at).total_seconds())) if cache_hit else None
        return ProviderSuccess(
            value,
            ProviderResultMeta(
                self.vendor_id,
                DataCategory.SENTIMENT,
                SourceRole.SUPPLEMENTAL,
                as_of,
                loaded.fetched_at,
                Freshness.FRESH,
                TradingSession.UNKNOWN,
                None,
                CacheDisposition.HIT if cache_hit else CacheDisposition.MISS,
                None,
                delay,
                _WARNINGS,
            ),
        )
