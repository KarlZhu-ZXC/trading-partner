"""Polymarket Gamma current-only prediction market context."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
from domain.us_context.models import USPredictionMarket, USPredictionMarketContext
from infrastructure.system.clock import SystemClock

_URL: Final[str] = "https://gamma-api.polymarket.com/public-search"


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if type(value) is Decimal:
        return value if value.is_finite() else None
    if type(value) is int:
        return Decimal(value)
    if isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value, parse_float=Decimal, parse_int=int)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


class PolymarketPredictionAdapter:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        current_window_seconds: int = 300,
    ) -> None:
        self._transport = transport
        self._clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._timeout = float(timeout_seconds)
        self._current_window = current_window_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.POLYMARKET

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.US and category is DataCategory.PREDICTION_MARKET

    def is_configured(self) -> bool:
        return self._enabled

    async def get_prediction_market_context(
        self,
        *,
        topic: str,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[USPredictionMarketContext]:
        require_aware_datetime(as_of, field_name="as_of")
        if not self.is_configured():
            raise ProviderNotConfigured("Polymarket adapter is disabled")
        now = self._clock.now()
        if as_of > now:
            raise DataContractError("as_of must not be in the future", details={"field": "as_of"})
        if (now - as_of).total_seconds() > self._current_window:
            raise NoMarketData(
                "Polymarket historical odds are unavailable",
                details={"vendor": self.vendor_id.value, "rule": "current_only"},
            )
        response = await self._transport.send(
            HttpRequest(
                method="GET",
                url=_URL,
                params={"q": topic, "limit_per_type": "20"},
                headers={"Accept": "application/json"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        fetched_at = self._clock.now()
        if response.status_code == 429:
            raise ProviderRateLimitError("Polymarket rate limited")
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError("Polymarket HTTP failure")
        try:
            payload = json.loads(response.body.decode("utf-8"), parse_float=Decimal, parse_int=int)
        except (UnicodeDecodeError, ValueError, TypeError):
            raise DataContractError("Polymarket response is not valid JSON") from None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("events", []), list):
            raise DataContractError("Polymarket payload has invalid shape")
        markets: list[USPredictionMarket] = []
        for event in payload.get("events", []):
            if not isinstance(event, Mapping) or not isinstance(event.get("markets", []), list):
                continue
            event_slug = event.get("slug")
            for row in event.get("markets", []):
                if not isinstance(row, Mapping):
                    continue
                market = self._market(row, event_slug=event_slug, now=now)
                if market is not None:
                    markets.append(market)
        ordered = tuple(
            sorted(markets, key=lambda item: item.volume or Decimal(0), reverse=True)[:limit]
        )
        context = USPredictionMarketContext(topic, as_of, ordered, False, ())
        meta = ProviderResultMeta(
            self.vendor_id,
            DataCategory.PREDICTION_MARKET,
            SourceRole.SUPPLEMENTAL,
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
        return ProviderSuccess(context, meta)

    @staticmethod
    def _market(
        row: Mapping[str, object], *, event_slug: object, now: datetime
    ) -> USPredictionMarket | None:
        if row.get("closed") is True:
            return None
        question = row.get("question")
        market_id = row.get("id") or row.get("conditionId")
        if not isinstance(question, str) or not question.strip() or market_id is None:
            return None
        resolution_at: datetime | None = None
        raw_end = row.get("endDate")
        if isinstance(raw_end, str) and raw_end:
            try:
                resolution_at = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
            except ValueError:
                return None
            if resolution_at.tzinfo is None or resolution_at <= now:
                return None
        labels = _list(row.get("outcomes"))
        prices = _list(row.get("outcomePrices"))
        if len(labels) != len(prices) or not labels:
            return None
        outcomes: list[tuple[str, Decimal]] = []
        for label, raw_probability in zip(labels, prices, strict=True):
            probability = _decimal(raw_probability)
            if not isinstance(label, str) or probability is None or not 0 <= probability <= 1:
                return None
            outcomes.append((label[:128], probability))
        slug = event_slug if isinstance(event_slug, str) else None
        url = f"https://polymarket.com/event/{slug}" if slug else None
        return USPredictionMarket(
            market_id=str(market_id)[:256],
            question=question.strip()[:500],
            outcomes=tuple(outcomes),
            volume=_decimal(row.get("volumeNum") or row.get("volume")),
            resolution_at=resolution_at,
            weekly_change=_decimal(row.get("oneWeekPriceChange")),
            url=url[:2_000] if url else None,
        )
