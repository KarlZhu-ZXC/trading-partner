"""Bounded Apify browser adapter for the separately formed IG weekend gold CFD."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.commodity_spot_provider import CommoditySpotProvider
from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
from domain.common.enums import (
    AdjustmentMethod,
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
    ProviderTimeoutError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from domain.common.time import require_aware_datetime
from domain.cross_asset.enums import OfferSide, SpotVenueBasis
from domain.cross_asset.spot_models import CommoditySpotBarSeries, SpotObservation
from domain.cross_asset.weekend_gold_hours import ig_weekend_gold_window
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval

_API_ROOT = "https://api.apify.com/v2"
_ACTOR_ID = "apify/web-scraper"
_ACTOR_PATH_ID = "apify~web-scraper"
_PAGE_URL = "https://www.ig.com/en/indices/markets-indices/weekend-gold"
_XAUUSD = "commodity_spot:OTC:XAUUSD"
_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"})
_DECIMAL_RE = re.compile(r"^[0-9][0-9,]*(?:\.[0-9]+)?$")
_WARNINGS = (
    "IG_WEEKEND_GOLD_CFD_FALLBACK",
    "WEEKEND_PROXY_NOT_SPOT",
    "IG_BROWSER_SCRAPE",
    "PRICE_TIME_IS_SCRAPE_TIME",
    "IG_WEEKEND_PRICE_SEPARATE_FROM_WEEKDAY_SPOT",
)


def _json_object(response: HttpResponse, *, operation: str) -> Mapping[str, object]:
    try:
        value = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DataContractError(f"Apify {operation} response is not valid JSON") from None
    if not isinstance(value, Mapping):
        raise DataContractError(f"Apify {operation} response must be an object")
    return value


def _data_object(response: HttpResponse, *, operation: str) -> Mapping[str, object]:
    value = _json_object(response, operation=operation).get("data")
    if not isinstance(value, Mapping):
        raise DataContractError(f"Apify {operation} response is missing data")
    return value


def _decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value):
        raise DataContractError(
            "IG Weekend Gold price is malformed",
            details={"field": field, "rule": "decimal_string"},
        )
    try:
        parsed = Decimal(value.replace(",", ""))
    except InvalidOperation:
        raise DataContractError(
            "IG Weekend Gold price is malformed",
            details={"field": field, "rule": "decimal_string"},
        ) from None
    if not parsed.is_finite() or parsed <= 0:
        raise DataContractError(
            "IG Weekend Gold price must be positive",
            details={"field": field, "rule": "positive"},
        )
    return parsed


class IGWeekendGoldApifyAdapter:
    """Fetch one current IG weekend CFD observation through an Apify browser."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock,
        enabled: bool,
        api_token: str | None,
        actor_id: str = _ACTOR_ID,
        cache_ttl_seconds: int = 600,
        max_charge_usd: Decimal = Decimal("0.03"),
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if actor_id != _ACTOR_ID:
            raise DataContractError("unsupported IG Weekend Gold Apify actor")
        if cache_ttl_seconds < 60 or max_charge_usd <= 0:
            raise DataContractError("IG Weekend Gold cache and charge bounds are invalid")
        if timeout_seconds < 30 or poll_interval_seconds <= 0:
            raise DataContractError("IG Weekend Gold runtime bounds are invalid")
        self._transport = transport
        self._clock = clock
        self._enabled = bool(enabled)
        self._api_token = api_token.strip() if api_token and api_token.strip() else None
        self._cache_ttl = int(cache_ttl_seconds)
        self._max_charge = max_charge_usd
        self._timeout = float(timeout_seconds)
        self._poll_interval = float(poll_interval_seconds)
        self._sleep = sleep
        self._cache: tuple[datetime, ProviderSuccess[SpotObservation]] | None = None
        self._lock = asyncio.Lock()

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.IG_WEEKEND_GOLD

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.OTC and category is DataCategory.MARKET_QUOTE

    def is_configured(self) -> bool:
        return self._enabled and self._api_token is not None

    def _require_quote(self, instrument: Instrument, as_of: datetime) -> datetime:
        if not self._enabled:
            raise ProviderNotConfigured("IG Weekend Gold fallback is disabled")
        if self._api_token is None:
            raise ProviderNotConfigured("IG Weekend Gold fallback requires APIFY_API_TOKEN")
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if instrument.instrument_id != _XAUUSD:
            raise NoMarketData("IG Weekend Gold fallback supports XAUUSD only")
        if abs((now - as_of).total_seconds()) > 300:
            raise NoMarketData("IG Weekend Gold fallback is current-only")
        if not ig_weekend_gold_window(now).is_open:
            raise NoMarketData("IG Weekend Gold market is closed")
        return now

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpRequest:
        assert self._api_token is not None
        return HttpRequest(
            method=method,  # type: ignore[arg-type]
            url=url,
            params=params or {},
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
            body=body,
            timeout_seconds=min(30.0, self._timeout),
        )

    async def _send(self, request: HttpRequest, *, operation: str) -> HttpResponse:
        response = await self._transport.send(request)
        if response.status_code == 429:
            raise ProviderRateLimitError(f"Apify {operation} rate limited")
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError(f"Apify {operation} HTTP failure")
        return response

    @staticmethod
    def _payload() -> bytes:
        page_function = """async function pageFunction(context) {
  await new Promise((resolve) => setTimeout(resolve, 8000));
  const text = document.body ? document.body.innerText : '';
  const capture = (pattern) => {
    const match = text.match(pattern);
    return match ? match[1] : null;
  };
  return {
    url: context.request.url,
    title: document.title,
    upstreamStatus: context.response && context.response.status,
    identity: text.includes('Weekend Gold'),
    marketCode: text.includes('FFIH5'),
    sell: capture(/SELL\\s+([0-9][0-9,]*(?:\\.[0-9]+)?)/i),
    buy: capture(/BUY\\s+([0-9][0-9,]*(?:\\.[0-9]+)?)/i)
  };
}"""
        value = {
            "runMode": "PRODUCTION",
            "startUrls": [{"url": _PAGE_URL}],
            "linkSelector": "",
            "maxPagesPerCrawl": 1,
            "maxResultsPerCrawl": 1,
            "maxRequestRetries": 0,
            "maxConcurrency": 1,
            "pageLoadTimeoutSecs": 45,
            "pageFunctionTimeoutSecs": 30,
            "waitUntil": ["domcontentloaded"],
            "proxyConfiguration": {"useApifyProxy": True},
            "proxyRotation": "PER_REQUEST",
            "useChrome": True,
            "headless": False,
            "ignoreSslErrors": True,
            "downloadMedia": False,
            "downloadCss": False,
            "closeCookieModals": True,
            "maxScrollHeightPixels": 0,
            "injectJQuery": False,
            "pageFunction": page_function,
        }
        return json.dumps(value, separators=(",", ":")).encode()

    async def _run_actor(self) -> Mapping[str, object]:
        started = await self._send(
            self._request(
                "POST",
                f"{_API_ROOT}/acts/{_ACTOR_PATH_ID}/runs",
                params={
                    "build": "version-3",
                    "memory": "2048",
                    "timeout": "120",
                    "maxTotalChargeUsd": format(self._max_charge, "f"),
                },
                body=self._payload(),
            ),
            operation="IG Weekend Gold run start",
        )
        run = _data_object(started, operation="run start")
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise DataContractError("Apify run start response is missing id")
        deadline = monotonic() + self._timeout
        while run.get("status") not in _TERMINAL_STATUSES:
            if monotonic() >= deadline:
                raise ProviderTimeoutError("Apify IG Weekend Gold Actor timed out")
            await self._sleep(self._poll_interval)
            polled = await self._send(
                self._request("GET", f"{_API_ROOT}/actor-runs/{run_id}"),
                operation="IG Weekend Gold run poll",
            )
            run = _data_object(polled, operation="run poll")
        if run.get("status") == "TIMED-OUT":
            raise ProviderTimeoutError("Apify IG Weekend Gold Actor timed out")
        if run.get("status") != "SUCCEEDED":
            raise ProviderUnavailableError("Apify IG Weekend Gold Actor failed")
        dataset_id = run.get("defaultDatasetId")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise DataContractError("Apify successful run is missing dataset id")
        dataset = await self._send(
            self._request(
                "GET",
                f"{_API_ROOT}/datasets/{dataset_id}/items",
                params={"clean": "true", "format": "json", "limit": "1"},
            ),
            operation="IG Weekend Gold dataset read",
        )
        try:
            items = json.loads(dataset.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DataContractError("Apify IG dataset is not valid JSON") from None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping):
            raise NoMarketData("Apify IG dataset did not contain exactly one result")
        return items[0]

    @staticmethod
    def _observation(raw: Mapping[str, object], fetched_at: datetime) -> SpotObservation:
        if raw.get("identity") is not True or raw.get("marketCode") is not True:
            raise DataContractError("IG Weekend Gold page identity could not be verified")
        if raw.get("upstreamStatus") != 200:
            raise ProviderUnavailableError("IG Weekend Gold page HTTP status was not successful")
        if raw.get("url") != _PAGE_URL or raw.get("title") != "Weekend Gold | IG International":
            raise DataContractError("IG Weekend Gold page contract changed")
        bid = _decimal(raw.get("sell"), field="sell")
        ask = _decimal(raw.get("buy"), field="buy")
        if bid > ask or ask - bid > max(Decimal("100"), bid * Decimal("0.05")):
            raise DataContractError("IG Weekend Gold bid/ask spread is invalid")
        return SpotObservation(
            instrument_id=_XAUUSD,
            currency="USD",
            unit="IG Weekend Gold CFD points",
            quote_at=fetched_at,
            venue_basis=SpotVenueBasis.IG_WEEKEND_CFD,
            source=VendorId.IG_WEEKEND_GOLD.value,
            bid=bid,
            ask=ask,
            mid=(bid + ask) / Decimal("2"),
        )

    @staticmethod
    def _meta(
        *,
        as_of: datetime,
        fetched_at: datetime,
        cache_disposition: CacheDisposition,
        data_delay_seconds: int,
    ) -> ProviderResultMeta:
        return ProviderResultMeta(
            vendor=VendorId.IG_WEEKEND_GOLD,
            category=DataCategory.MARKET_QUOTE,
            role=SourceRole.FALLBACK,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=Freshness.FRESH,
            session=TradingSession.UNKNOWN,
            latency_ms=None,
            cache_disposition=cache_disposition,
            adjustment=None,
            data_delay_seconds=data_delay_seconds,
            warnings=_WARNINGS,
        )

    async def get_quote(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[SpotObservation]:
        now = self._require_quote(instrument, as_of)
        async with self._lock:
            if self._cache is not None and now < self._cache[0]:
                cached = self._cache[1]
                age = max(0, int((now - cached.meta.fetched_at).total_seconds()))
                return ProviderSuccess(
                    value=cached.value,
                    meta=self._meta(
                        as_of=as_of,
                        fetched_at=cached.meta.fetched_at,
                        cache_disposition=CacheDisposition.HIT,
                        data_delay_seconds=age,
                    ),
                )
            raw = await self._run_actor()
            fetched_at = self._clock.now().astimezone(UTC)
            observation = self._observation(raw, fetched_at)
            success = ProviderSuccess(
                value=observation,
                meta=self._meta(
                    as_of=as_of,
                    fetched_at=fetched_at,
                    cache_disposition=CacheDisposition.MISS,
                    data_delay_seconds=0,
                ),
            )
            self._cache = (fetched_at + timedelta(seconds=self._cache_ttl), success)
            return success

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
        offer_side: OfferSide = OfferSide.BID,
    ) -> ProviderSuccess[CommoditySpotBarSeries]:
        raise NoMarketData("IG Weekend Gold fallback does not provide OHLCV bars")


class WeekendGoldFallbackSpotAdapter:
    """Route current weekend XAU quotes to IG; keep all bars on Dukascopy."""

    def __init__(
        self,
        primary: CommoditySpotProvider,
        fallback: IGWeekendGoldApifyAdapter,
        *,
        clock: Clock,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._clock = clock

    @property
    def vendor_id(self) -> VendorId:
        return self._primary.vendor_id

    @property
    def provider_name(self) -> str:
        return self._primary.provider_name

    def supports(self, market: Market, category: DataCategory) -> bool:
        return self._primary.supports(market, category)

    def is_configured(self) -> bool:
        return self._primary.is_configured()

    async def get_quote(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[SpotObservation]:
        now = self._clock.now()
        use_weekend = (
            instrument.instrument_id == _XAUUSD
            and self._fallback.is_configured()
            and abs((now - as_of).total_seconds()) <= 300
            and ig_weekend_gold_window(now).is_open
        )
        if not use_weekend:
            return await self._primary.get_quote(instrument, as_of)
        try:
            return await self._fallback.get_quote(instrument, as_of)
        except TradingPartnerError:
            primary = await self._primary.get_quote(instrument, as_of)
            return ProviderSuccess(
                value=primary.value,
                meta=replace(
                    primary.meta,
                    warnings=(*primary.meta.warnings, "IG_WEEKEND_GOLD_FALLBACK_UNAVAILABLE"),
                ),
            )

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
        offer_side: OfferSide = OfferSide.BID,
    ) -> ProviderSuccess[CommoditySpotBarSeries]:
        return await self._primary.get_bars(
            instrument,
            start=start,
            end=end,
            interval=interval,
            adjustment=adjustment,
            as_of=as_of,
            offer_side=offer_side,
        )


__all__ = ["IGWeekendGoldApifyAdapter", "WeekendGoldFallbackSpotAdapter"]
