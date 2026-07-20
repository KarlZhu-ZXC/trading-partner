"""StockTwits public symbol stream with explicit user sentiment labels."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Final
from urllib.parse import quote

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

_PREFIX: Final[str] = "https://api.stocktwits.com/api/2/streams/symbol/"


class StockTwitsSentimentAdapter:
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

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.STOCKTWITS

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.SENTIMENT

    def is_configured(self) -> bool:
        return self._enabled

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
            raise ProviderNotConfigured("StockTwits adapter is disabled")
        if as_of > self._clock.now():
            raise DataContractError("as_of must not be in the future", details={"field": "as_of"})
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=f"{_PREFIX}{quote(instrument.symbol.upper(), safe='.-')}.json",
                params={},
                headers={"Accept": "application/json", "User-Agent": "TradingPartner/1.0"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        fetched_at = self._clock.now()
        if response.status_code == 429:
            raise ProviderRateLimitError("StockTwits rate limited")
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError("StockTwits HTTP failure")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            raise DataContractError("StockTwits response is not valid JSON") from None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("messages", []), list):
            raise DataContractError("StockTwits payload has invalid shape")
        samples: list[USSentimentSample] = []
        for row in payload.get("messages", []):
            if not isinstance(row, Mapping):
                continue
            sample = self._sample(row, instrument.instrument_id)
            if sample is None or sample.published_at > as_of:
                continue
            day = sample.published_at.date()
            if (start is not None and day < start) or (end is not None and day > end):
                continue
            samples.append(sample)
        value = tuple(sorted(samples, key=lambda item: item.published_at, reverse=True)[:limit])
        meta = ProviderResultMeta(
            self.vendor_id,
            DataCategory.SENTIMENT,
            SourceRole.PRIMARY,
            as_of,
            fetched_at,
            Freshness.FRESH,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            None,
            (),
        )
        return ProviderSuccess(value, meta)

    @staticmethod
    def _sample(row: Mapping[str, object], instrument_id: str) -> USSentimentSample | None:
        entities = row.get("entities")
        sentiment = entities.get("sentiment") if isinstance(entities, Mapping) else None
        label = sentiment.get("basic") if isinstance(sentiment, Mapping) else None
        if label == "Bullish":
            direction, score = USSentimentDirection.BULLISH, Decimal(1)
        elif label == "Bearish":
            direction, score = USSentimentDirection.BEARISH, Decimal(-1)
        else:
            return None
        body = row.get("body")
        created = row.get("created_at")
        if not isinstance(body, str) or not body.strip() or not isinstance(created, str):
            return None
        try:
            published_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return None
        likes_obj = row.get("likes")
        likes = likes_obj.get("total") if isinstance(likes_obj, Mapping) else None
        likes = likes if type(likes) is int and likes >= 0 else None
        raw_id = row.get("id")
        url = f"https://stocktwits.com/message/{raw_id}" if type(raw_id) is int else None
        return USSentimentSample(
            instrument_id=instrument_id,
            source=USSentimentSource.STOCKTWITS,
            published_at=published_at,
            text=" ".join(body.split())[:1_000],
            direction=direction,
            label_origin=USSentimentLabelOrigin.USER_LABEL,
            score=score,
            likes=likes,
            comments=None,
            url=url,
            classifier_version=None,
        )
