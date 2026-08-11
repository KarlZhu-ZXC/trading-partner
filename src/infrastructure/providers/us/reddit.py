"""Reddit RSS discussion sentiment with versioned deterministic inference."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Final
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_xml_fromstring

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.reddit_state import RedditSampleCacheEntry
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from application.ports.reddit_state_store import RedditStateStore
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
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_context.enums import (
    USSentimentDirection,
    USSentimentLabelOrigin,
    USSentimentSource,
)
from domain.us_context.models import USSentimentSample
from infrastructure.persistence.reddit_state_store import InMemoryRedditStateStore
from infrastructure.providers.us.reddit_apify import ApifyRedditClient, ApifyRedditPost
from infrastructure.system.clock import SystemClock

_ATOM: Final = "{http://www.w3.org/2005/Atom}"
_CLASSIFIER_VERSION: Final[str] = "reddit_lexicon_v1"
_BULLISH: Final = frozenset({"bull", "bullish", "buy", "calls", "growth", "long", "moon", "upside"})
_BEARISH: Final = frozenset(
    {"bear", "bearish", "puts", "sell", "short", "downside", "overvalued", "crash"}
)
_TAG_RE: Final = re.compile(r"<[^>]+>")
_WORD_RE: Final = re.compile(r"[a-z]+")


@dataclass(frozen=True, slots=True)
class _LoadedSamples:
    samples: tuple[USSentimentSample, ...]
    fetched_at: datetime
    freshness: Freshness
    warnings: tuple[str, ...]
    data_delay_seconds: int | None


class RedditSentimentAdapter:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        user_agent: str,
        subreddits: tuple[str, ...],
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        min_interval_seconds: float = 6.0,
        cache_ttl_seconds: int = 3600,
        cooldown_default_seconds: int = 900,
        cooldown_max_seconds: int = 3600,
        apify_enabled: bool = False,
        apify_api_token: str | None = None,
        apify_actor_id: str = "harshmaur/reddit-scraper",
        apify_subreddits: tuple[str, ...] = (),
        apify_lookback_days: Mapping[str, int] | None = None,
        apify_max_charge_usd: Decimal = Decimal("0.20"),
        apify_batch_window_seconds: float = 0.05,
        state_store: RedditStateStore | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise DataContractError("reddit user_agent is required")
        self._transport = transport
        self._user_agent = user_agent.strip()
        if not isinstance(subreddits, tuple) or not subreddits:
            raise DataContractError("reddit subreddits must be a non-empty tuple")
        normalized = []
        for subreddit in subreddits:
            if not isinstance(subreddit, str):
                raise DataContractError("reddit subreddit must be a string")
            normalized.append(subreddit.strip().lower())
        if any(not subreddit for subreddit in normalized):
            raise DataContractError("reddit subreddit must be non-empty")
        self._subreddits = tuple(normalized)
        self._clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._timeout = float(timeout_seconds)
        if min_interval_seconds < 0:
            raise DataContractError("reddit min_interval_seconds must be nonnegative")
        self._min_interval = float(min_interval_seconds)
        if cache_ttl_seconds <= 0:
            raise DataContractError("reddit cache_ttl_seconds must be positive")
        if cooldown_default_seconds <= 0:
            raise DataContractError("reddit cooldown_default_seconds must be positive")
        if cooldown_max_seconds < cooldown_default_seconds:
            raise DataContractError(
                "reddit cooldown_max_seconds must be >= cooldown_default_seconds"
            )
        self._cache_ttl = int(cache_ttl_seconds)
        self._cooldown_default = int(cooldown_default_seconds)
        self._cooldown_max = int(cooldown_max_seconds)
        self._state_store = state_store or InMemoryRedditStateStore()
        if sleep is None:
            sleep = asyncio.sleep
        self._sleep = sleep
        if apify_batch_window_seconds < 0:
            raise DataContractError("Apify batch window must be nonnegative")
        self._apify_batch_window = float(apify_batch_window_seconds)
        normalized_apify_subreddits = tuple(item.strip().lower() for item in apify_subreddits)
        lookbacks = dict(apify_lookback_days or {})
        self._apify: ApifyRedditClient | None = None
        if apify_enabled:
            if apify_api_token is None or not apify_api_token.strip():
                raise DataContractError("Apify Reddit fallback requires an API token")
            self._apify = ApifyRedditClient(
                transport,
                api_token=apify_api_token,
                actor_id=apify_actor_id,
                subreddits=normalized_apify_subreddits,
                lookback_days=lookbacks,
                max_charge_usd=apify_max_charge_usd,
                clock=self._clock,
                sleep=self._sleep,
            )
        apify_config = ",".join(
            f"{name}:{lookbacks.get(name, 0)}" for name in normalized_apify_subreddits
        )
        config_text = (
            ",".join(self._subreddits)
            + f"|apify={bool(self._apify)}:{apify_config}|{_CLASSIFIER_VERSION}"
        )
        self._config_key = hashlib.sha256(config_text.encode("utf-8")).hexdigest()[:16]
        self._inflight: dict[str, asyncio.Task[_LoadedSamples]] = {}
        self._inflight_lock = asyncio.Lock()
        self._apify_pending: dict[
            str, tuple[Instrument, list[asyncio.Future[tuple[ApifyRedditPost, ...]]]]
        ] = {}
        self._apify_batch_task: asyncio.Task[None] | None = None
        self._apify_batch_lock = asyncio.Lock()

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.REDDIT

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.SENTIMENT

    def is_configured(self) -> bool:
        return self._enabled or self._apify is not None

    async def _subreddit(
        self, subreddit: str, instrument: Instrument
    ) -> tuple[USSentimentSample, ...]:
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=f"https://www.reddit.com/r/{subreddit}/search.rss",
                params={
                    "q": instrument.symbol,
                    "restrict_sr": "on",
                    "sort": "new",
                    "t": "week",
                },
                headers={
                    "Accept": "application/atom+xml,application/xml",
                    "User-Agent": self._user_agent,
                },
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        if response.status_code == 429:
            retry_at = self._record_cooldown(response.headers)
            raise ProviderRateLimitError(
                "Reddit RSS rate limited",
                details={"retry_at": retry_at.isoformat()},
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError("Reddit RSS HTTP failure")
        try:
            root = safe_xml_fromstring(response.body)
        except (ParseError, DefusedXmlException):
            raise DataContractError("Reddit RSS is not valid XML") from None
        samples: list[USSentimentSample] = []
        for entry in root.findall(f"{_ATOM}entry"):
            sample = self._entry(entry, instrument.instrument_id)
            if sample is not None:
                samples.append(sample)
        return tuple(samples)

    @staticmethod
    def _header(headers: object, name: str) -> str | None:
        if not isinstance(headers, Mapping):
            return None
        wanted = name.casefold()
        for key, value in headers.items():
            if isinstance(key, str) and key.casefold() == wanted and isinstance(value, str):
                return value.strip() or None
        return None

    def _cooldown_seconds(self, headers: object, now: datetime) -> int:
        candidates: list[float] = []
        retry_after = self._header(headers, "Retry-After")
        if retry_after is not None:
            try:
                candidates.append(float(retry_after))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(retry_after)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    candidates.append((parsed - now).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        reset = self._header(headers, "X-Ratelimit-Reset")
        if reset is not None:
            with contextlib.suppress(ValueError):
                candidates.append(float(reset))
        positive = [value for value in candidates if value > 0]
        requested = max(positive, default=float(self._cooldown_default))
        return max(1, min(self._cooldown_max, int(requested + 0.999)))

    def _record_cooldown(self, headers: object) -> datetime:
        now = self._clock.now().astimezone(UTC)
        until = now + timedelta(seconds=self._cooldown_seconds(headers, now))
        with contextlib.suppress(Exception):
            self._state_store.set_cooldown_until(until, updated_at=now)
        return until

    def _cached(self, instrument_id: str) -> RedditSampleCacheEntry | None:
        try:
            return self._state_store.get_samples(instrument_id, self._config_key)
        except Exception:  # noqa: BLE001 - provider can still serve live RSS
            return None

    def _cooldown_until(self) -> datetime | None:
        try:
            return self._state_store.get_cooldown_until()
        except Exception:  # noqa: BLE001 - provider can still attempt live RSS
            return None

    async def _drain_apify_batches(self) -> None:
        if self._apify_batch_window > 0:
            await self._sleep(self._apify_batch_window)
        while True:
            async with self._apify_batch_lock:
                selected_ids = tuple(self._apify_pending)[:10]
                selected = [self._apify_pending.pop(item_id) for item_id in selected_ids]
                if not selected:
                    self._apify_batch_task = None
                    return
            instruments = tuple(item[0] for item in selected)
            try:
                assert self._apify is not None
                results = await self._apify.fetch(instruments)
            except asyncio.CancelledError:
                for _, futures in selected:
                    for future in futures:
                        future.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 - fan out one typed provider failure
                for _, futures in selected:
                    for future in futures:
                        if not future.done():
                            future.set_exception(exc)
            else:
                for instrument, futures in selected:
                    posts = results.get(instrument.instrument_id, ())
                    for future in futures:
                        if not future.done():
                            future.set_result(posts)

    async def _apify_posts(self, instrument: Instrument) -> tuple[ApifyRedditPost, ...]:
        if self._apify is None:
            raise ProviderNotConfigured("Apify Reddit fallback is disabled")
        future: asyncio.Future[tuple[ApifyRedditPost, ...]] = (
            asyncio.get_running_loop().create_future()
        )
        async with self._apify_batch_lock:
            pending = self._apify_pending.get(instrument.instrument_id)
            if pending is None:
                self._apify_pending[instrument.instrument_id] = (instrument, [future])
            else:
                pending[1].append(future)
            if self._apify_batch_task is None or self._apify_batch_task.done():
                self._apify_batch_task = asyncio.create_task(self._drain_apify_batches())
        return await asyncio.shield(future)

    @staticmethod
    def _apify_sample(post: ApifyRedditPost, instrument_id: str) -> USSentimentSample:
        text = " ".join(f"{post.title} {post.body}".split())[:1_000]
        direction, score = RedditSentimentAdapter._classify(text)
        return USSentimentSample(
            instrument_id=instrument_id,
            source=USSentimentSource.REDDIT,
            published_at=post.published_at,
            text=text,
            direction=direction,
            label_origin=USSentimentLabelOrigin.DETERMINISTIC_INFERENCE,
            score=score,
            likes=post.score,
            comments=post.comments,
            url=post.url,
            classifier_version=_CLASSIFIER_VERSION,
        )

    async def _apify_loaded(
        self,
        instrument: Instrument,
        *,
        warnings: tuple[str, ...],
    ) -> _LoadedSamples:
        posts = await self._apify_posts(instrument)
        samples = tuple(self._apify_sample(post, instrument.instrument_id) for post in posts)
        return self._successful_loaded(instrument.instrument_id, samples, warnings=warnings)

    def _successful_loaded(
        self,
        instrument_id: str,
        samples: tuple[USSentimentSample, ...],
        *,
        warnings: tuple[str, ...] = (),
    ) -> _LoadedSamples:
        fetched_at = self._clock.now()
        entry = RedditSampleCacheEntry(
            instrument_id=instrument_id,
            config_key=self._config_key,
            samples=samples,
            fetched_at=fetched_at,
            expires_at=fetched_at + timedelta(seconds=self._cache_ttl),
        )
        final_warnings = warnings
        try:
            self._state_store.set_samples(entry)
        except Exception:  # noqa: BLE001 - live data remains usable
            final_warnings = tuple(dict.fromkeys((*warnings, "REDDIT_STATE_UNAVAILABLE")))
        return _LoadedSamples(samples, fetched_at, Freshness.FRESH, final_warnings, None)

    @staticmethod
    def _from_cache(
        entry: RedditSampleCacheEntry,
        *,
        now: datetime,
        stale: bool,
    ) -> _LoadedSamples:
        delay = max(0, int((now - entry.fetched_at).total_seconds()))
        return _LoadedSamples(
            samples=entry.samples,
            fetched_at=entry.fetched_at,
            freshness=Freshness.STALE if stale else Freshness.FRESH,
            warnings=("REDDIT_RATE_LIMITED",) if stale else (),
            data_delay_seconds=delay,
        )

    async def _refresh(
        self,
        instrument: Instrument,
        stale_entry: RedditSampleCacheEntry | None,
    ) -> _LoadedSamples:
        now = self._clock.now()
        # A preceding coalesced caller may have populated state while this caller
        # was waiting for ownership of the refresh task.
        current = self._cached(instrument.instrument_id)
        if current is not None and now < current.expires_at:
            return self._from_cache(current, now=now, stale=False)
        cooldown_until = self._cooldown_until()
        if cooldown_until is not None and now < cooldown_until:
            cached = current or stale_entry
            if cached is not None:
                return self._from_cache(cached, now=now, stale=True)
            if self._apify is not None:
                return await self._apify_loaded(
                    instrument,
                    warnings=("REDDIT_RATE_LIMITED", "REDDIT_APIFY_FALLBACK"),
                )
            raise ProviderRateLimitError(
                "Reddit RSS provider cooldown is active",
                details={"retry_at": cooldown_until.isoformat()},
            )

        if not self._enabled:
            return await self._apify_loaded(
                instrument,
                warnings=("REDDIT_APIFY_SOURCE",),
            )

        groups: list[tuple[USSentimentSample, ...]] = []
        failures: list[Exception] = []
        rate_limited = False
        for index, subreddit in enumerate(self._subreddits):
            if index > 0 and self._min_interval > 0:
                await self._sleep(self._min_interval)
            try:
                groups.append(await self._subreddit(subreddit, instrument))
            except ProviderRateLimitError as exc:
                failures.append(exc)
                rate_limited = True
                # Transports used by tests or alternate hosts may raise directly
                # without exposing headers; still establish the bounded default.
                recorded_until = self._cooldown_until()
                if recorded_until is None or recorded_until <= self._clock.now():
                    self._record_cooldown({})
                break
            except TradingPartnerError as exc:
                failures.append(exc)
            except Exception as exc:  # noqa: BLE001
                failures.append(exc)

        if rate_limited:
            cached = self._cached(instrument.instrument_id) or stale_entry
            if cached is not None:
                return self._from_cache(cached, now=self._clock.now(), stale=True)
        should_fallback = self._apify is not None and (rate_limited or (not groups and failures))
        if should_fallback:
            try:
                return await self._apify_loaded(
                    instrument,
                    warnings=(
                        ("REDDIT_RATE_LIMITED", "REDDIT_APIFY_FALLBACK")
                        if rate_limited
                        else ("REDDIT_APIFY_FALLBACK",)
                    ),
                )
            except TradingPartnerError as exc:
                failures.append(exc)
            except Exception as exc:  # noqa: BLE001 - preserve usable partial RSS
                failures.append(exc)
        if not groups and failures:
            first = failures[0]
            if isinstance(first, TradingPartnerError):
                raise first
            raise ProviderUnavailableError("Reddit RSS unavailable")

        samples = tuple(
            sorted(
                {
                    (item.published_at, item.url, item.text): item
                    for group in groups
                    for item in group
                }.values(),
                key=lambda item: item.published_at,
                reverse=True,
            )
        )
        warnings: tuple[str, ...] = ()
        if failures:
            warnings = (
                ("REDDIT_PARTIAL", "REDDIT_RATE_LIMITED") if rate_limited else ("REDDIT_PARTIAL",)
            )
        return self._successful_loaded(instrument.instrument_id, samples, warnings=warnings)

    async def _load_samples(self, instrument: Instrument) -> _LoadedSamples:
        now = self._clock.now()
        cached = self._cached(instrument.instrument_id)
        if cached is not None and now < cached.expires_at:
            return self._from_cache(cached, now=now, stale=False)
        cooldown_until = self._cooldown_until()
        if cooldown_until is not None and now < cooldown_until:
            if cached is not None:
                return self._from_cache(cached, now=now, stale=True)
            if self._apify is None:
                raise ProviderRateLimitError(
                    "Reddit RSS provider cooldown is active",
                    details={"retry_at": cooldown_until.isoformat()},
                )

        key = f"{instrument.instrument_id}|{self._config_key}"
        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._refresh(instrument, cached))
                self._inflight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._inflight_lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    @staticmethod
    def _entry(entry: Element, instrument_id: str) -> USSentimentSample | None:
        title = entry.findtext(f"{_ATOM}title") or ""
        content = entry.findtext(f"{_ATOM}content") or ""
        published = entry.findtext(f"{_ATOM}published")
        if not published:
            return None
        try:
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return None
        plain = " ".join(html.unescape(_TAG_RE.sub(" ", content)).split())
        text = " ".join(f"{title} {plain}".split())[:1_000]
        if not text:
            return None
        direction, score = RedditSentimentAdapter._classify(text)
        link = entry.find(f"{_ATOM}link")
        url = link.get("href") if link is not None else None
        return USSentimentSample(
            instrument_id=instrument_id,
            source=USSentimentSource.REDDIT,
            published_at=published_at,
            text=text,
            direction=direction,
            label_origin=USSentimentLabelOrigin.DETERMINISTIC_INFERENCE,
            score=score,
            likes=None,
            comments=None,
            url=url[:2_000] if isinstance(url, str) and url else None,
            classifier_version=_CLASSIFIER_VERSION,
        )

    @staticmethod
    def _classify(text: str) -> tuple[USSentimentDirection, Decimal]:
        words = _WORD_RE.findall(text.casefold())
        bullish = sum(word in _BULLISH for word in words)
        bearish = sum(word in _BEARISH for word in words)
        total = bullish + bearish
        if total == 0 or bullish == bearish:
            return USSentimentDirection.NEUTRAL, Decimal(0)
        score = Decimal(bullish - bearish) / Decimal(total)
        direction = USSentimentDirection.BULLISH if score > 0 else USSentimentDirection.BEARISH
        return direction, score

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
            raise ProviderNotConfigured("Reddit adapter is disabled")
        if as_of > self._clock.now():
            raise DataContractError("as_of must not be in the future", details={"field": "as_of"})
        loaded = await self._load_samples(instrument)
        samples: list[USSentimentSample] = []
        for sample in loaded.samples:
            if sample.published_at > as_of:
                continue
            day = sample.published_at.date()
            if (start is not None and day < start) or (end is not None and day > end):
                continue
            samples.append(sample)
        unique = {(item.published_at, item.url, item.text): item for item in samples}
        value = tuple(
            sorted(unique.values(), key=lambda item: item.published_at, reverse=True)[:limit]
        )
        meta = ProviderResultMeta(
            self.vendor_id,
            DataCategory.SENTIMENT,
            SourceRole.SUPPLEMENTAL,
            as_of,
            loaded.fetched_at,
            loaded.freshness,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            loaded.data_delay_seconds,
            loaded.warnings,
        )
        return ProviderSuccess(value, meta)
