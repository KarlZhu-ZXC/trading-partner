"""Keyless weekend reference quotes for closed Dukascopy instruments.

The requested identities remain XAUUSD and LIGHT.CMD-USD.  Binance PAXG/USDC
spot and Hyperliquid XYZ CL/USDC perpetual prices are deliberately exposed only
as current weekend proxies with explicit venue bases and warnings.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.commodity_spot_provider import CommoditySpotProvider
from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
from domain.common.diagnostics import ProviderFailureDiagnostic
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
    ProviderRateLimitError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from domain.common.time import require_aware_datetime
from domain.cross_asset.enums import OfferSide, SpotVenueBasis
from domain.cross_asset.spot_models import CommoditySpotBarSeries, SpotObservation
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval

_XAUUSD = "commodity_spot:OTC:XAUUSD"
_LIGHT_OIL = "cfd:OTC:LIGHT_CMD_USD"
_BINANCE_URL = "https://api.binance.com/api/v3/ticker/bookTicker"
_HYPERLIQUID_URL = "https://api.hyperliquid.xyz/info"
_NEW_YORK = ZoneInfo("America/New_York")


def _positive_decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise DataContractError(
            "weekend reference price must be a decimal string",
            details={"field": field},
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise DataContractError(
            "weekend reference price is malformed",
            details={"field": field},
        ) from None
    if not parsed.is_finite() or parsed <= 0:
        raise DataContractError(
            "weekend reference price must be positive",
            details={"field": field},
        )
    return parsed


def _json(response: HttpResponse, *, provider: str) -> object:
    try:
        return json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DataContractError(f"{provider} response is not valid JSON") from None


def _failure_diagnostic(
    provider: str,
    stage: str,
    error: TradingPartnerError,
) -> ProviderFailureDiagnostic:
    details = error.details
    raw_attempt_count = details.get("attempt_count")
    raw_status_code = details.get("status_code")
    return ProviderFailureDiagnostic(
        provider=str(details.get("provider") or provider),
        stage=str(details.get("stage") or stage),
        error_code=error.code,
        retryable=error.retryable,
        attempt_count=raw_attempt_count if isinstance(raw_attempt_count, int) else 1,
        error_type=(
            str(details["error_type"]) if isinstance(details.get("error_type"), str) else None
        ),
        status_class=(
            str(details["status_class"]) if isinstance(details.get("status_class"), str) else None
        ),
        status_code=raw_status_code if isinstance(raw_status_code, int) else None,
    )


def _diagnostic_payload(item: ProviderFailureDiagnostic) -> dict[str, object]:
    return {
        "provider": item.provider,
        "stage": item.stage,
        "error_code": item.error_code,
        "retryable": item.retryable,
        "attempt_count": item.attempt_count,
        "error_type": item.error_type,
        "status_class": item.status_class,
        "status_code": item.status_code,
    }


def _dukascopy_weekend_closed(now: datetime) -> bool:
    local = now.astimezone(_NEW_YORK)
    local_time = local.timetz().replace(tzinfo=None)
    return (
        (local.weekday() == 4 and local_time >= time(17))
        or local.weekday() == 5
        or (local.weekday() == 6 and local_time < time(18))
    )


class _CurrentWeekendReference:
    instrument_id: str
    vendor_id: VendorId
    venue_basis: SpotVenueBasis
    warnings: tuple[str, ...]

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock,
        enabled: bool,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 60,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.25,
    ) -> None:
        if (
            timeout_seconds <= 0
            or cache_ttl_seconds < 10
            or not 1 <= retry_attempts <= 5
            or retry_backoff_seconds < 0
            or retry_backoff_seconds > 5
        ):
            raise DataContractError("weekend reference runtime bounds are invalid")
        self._transport = transport
        self._clock = clock
        self._enabled = bool(enabled)
        self._timeout = float(timeout_seconds)
        self._cache_ttl = int(cache_ttl_seconds)
        self._retry_attempts = int(retry_attempts)
        self._retry_backoff = float(retry_backoff_seconds)
        self._cache: tuple[datetime, ProviderSuccess[SpotObservation]] | None = None
        self._lock = asyncio.Lock()

    @property
    def provider_name(self) -> str:
        return self.vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.OTC and category is DataCategory.MARKET_QUOTE

    def is_configured(self) -> bool:
        return self._enabled

    def _require_current_weekend(self, instrument: Instrument, as_of: datetime) -> datetime:
        if not self._enabled:
            raise NoMarketData("weekend reference fallback is disabled")
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if instrument.instrument_id != self.instrument_id:
            raise NoMarketData("weekend reference does not support this instrument")
        if abs((now - as_of).total_seconds()) > 300:
            raise NoMarketData("weekend reference is current-only")
        if not _dukascopy_weekend_closed(now):
            raise NoMarketData("weekend reference is available only during the venue weekend")
        return now

    async def _send(self, request: HttpRequest) -> HttpResponse:
        last_error: TradingPartnerError | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                response = await self._transport.send(request)
                if response.status_code == 429:
                    raise ProviderRateLimitError(
                        f"{self.provider_name} weekend reference rate limited",
                        details={
                            "error_type": "rate_limit",
                            "status_class": "4xx",
                            "status_code": 429,
                        },
                    )
                if response.status_code < 200 or response.status_code >= 300:
                    raise ProviderUnavailableError(
                        f"{self.provider_name} weekend reference HTTP failure",
                        details={
                            "error_type": "http_failure",
                            "status_class": f"{response.status_code // 100}xx",
                            "status_code": response.status_code,
                        },
                    )
                return response
            except TradingPartnerError as error:
                last_error = error
                if not error.retryable or attempt >= self._retry_attempts:
                    details = dict(error.details)
                    details.update(
                        {
                            "provider": self.provider_name,
                            "stage": "weekend_quote_request",
                            "attempt_count": attempt,
                        }
                    )
                    raise type(error)(
                        error.message,
                        details=details,
                        retryable=error.retryable,
                        code=error.code,
                    ) from None
                if self._retry_backoff:
                    await asyncio.sleep(self._retry_backoff * attempt)
        assert last_error is not None  # pragma: no cover - loop invariant
        raise last_error

    def _meta(
        self,
        *,
        as_of: datetime,
        fetched_at: datetime,
        cache_disposition: CacheDisposition,
        data_delay_seconds: int,
    ) -> ProviderResultMeta:
        return ProviderResultMeta(
            vendor=self.vendor_id,
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
            warnings=self.warnings,
        )

    async def get_quote(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[SpotObservation]:
        now = self._require_current_weekend(instrument, as_of)
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
            fetched_at = self._clock.now().astimezone(UTC)
            observation = await self._fetch_observation(fetched_at)
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

    async def _fetch_observation(self, fetched_at: datetime) -> SpotObservation:
        raise NotImplementedError

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
        raise NoMarketData("weekend reference does not provide OHLCV bars")


class BinancePaxgUsdcWeekendAdapter(_CurrentWeekendReference):
    """Map Binance PAXG/USDC book midpoint to an XAUUSD weekend proxy."""

    instrument_id = _XAUUSD
    vendor_id = VendorId.BINANCE
    venue_basis = SpotVenueBasis.PAXG_USDC_SPOT_PROXY
    warnings = (
        "PAXG_USDC_WEEKEND_PROXY",
        "WEEKEND_PROXY_NOT_XAUUSD_SPOT",
        "TOKENIZED_GOLD_BASIS_RISK",
        "USDC_PEG_RISK",
        "PRICE_TIME_IS_FETCH_TIME",
    )

    async def _fetch_observation(self, fetched_at: datetime) -> SpotObservation:
        response = await self._send(
            HttpRequest(
                method="GET",
                url=_BINANCE_URL,
                params={"symbol": "PAXGUSDC"},
                headers={"Accept": "application/json"},
                body=None,
                timeout_seconds=self._timeout,
            )
        )
        raw = _json(response, provider="Binance")
        if not isinstance(raw, Mapping) or raw.get("symbol") != "PAXGUSDC":
            raise DataContractError("Binance PAXG/USDC response identity mismatch")
        bid = _positive_decimal(raw.get("bidPrice"), field="bidPrice")
        ask = _positive_decimal(raw.get("askPrice"), field="askPrice")
        if bid > ask:
            raise DataContractError("Binance PAXG/USDC spread is invalid")
        return SpotObservation(
            instrument_id=self.instrument_id,
            currency="USDC",
            unit="PAXG token per USDC",
            quote_at=fetched_at,
            venue_basis=self.venue_basis,
            source=self.vendor_id.value,
            bid=bid,
            ask=ask,
            mid=(bid + ask) / Decimal("2"),
        )


class HyperliquidClUsdcWeekendAdapter(_CurrentWeekendReference):
    """Map Hyperliquid XYZ CL/USDC midpoint to a light-oil weekend proxy."""

    instrument_id = _LIGHT_OIL
    vendor_id = VendorId.HYPERLIQUID
    venue_basis = SpotVenueBasis.CL_USDC_PERPETUAL_PROXY
    warnings = (
        "CL_USDC_WEEKEND_PROXY",
        "WEEKEND_PROXY_NOT_WTI_SPOT",
        "HIP3_PERPETUAL_BASIS_RISK",
        "USDC_PEG_RISK",
        "PRICE_TIME_IS_FETCH_TIME",
    )

    async def _fetch_observation(self, fetched_at: datetime) -> SpotObservation:
        response = await self._send(
            HttpRequest(
                method="POST",
                url=_HYPERLIQUID_URL,
                params={},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                body=b'{"type":"allMids","dex":"xyz"}',
                timeout_seconds=self._timeout,
            )
        )
        raw = _json(response, provider="Hyperliquid")
        if not isinstance(raw, Mapping):
            raise DataContractError("Hyperliquid allMids response must be an object")
        midpoint = _positive_decimal(raw.get("xyz:CL"), field="xyz:CL")
        return SpotObservation(
            instrument_id=self.instrument_id,
            currency="USDC",
            unit="Hyperliquid xyz:CL perpetual per USDC",
            quote_at=fetched_at,
            venue_basis=self.venue_basis,
            source=self.vendor_id.value,
            mid=midpoint,
        )


class WeekendReferenceFallbackSpotAdapter:
    """Use labelled weekend references for current quotes; keep bars upstream."""

    def __init__(
        self,
        primary: CommoditySpotProvider,
        *,
        gold_proxy: CommoditySpotProvider,
        oil_proxy: CommoditySpotProvider,
        legacy_gold_fallback: CommoditySpotProvider | None,
        clock: Clock,
    ) -> None:
        self._primary = primary
        self._gold_proxy = gold_proxy
        self._oil_proxy = oil_proxy
        self._legacy_gold = legacy_gold_fallback
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
        current_weekend = abs((now - as_of).total_seconds()) <= 300 and _dukascopy_weekend_closed(
            now
        )
        proxies: tuple[CommoditySpotProvider, ...] = ()
        unavailable_code = "WEEKEND_REFERENCE_FALLBACK_UNAVAILABLE"
        if current_weekend and instrument.instrument_id == _XAUUSD:
            proxies = tuple(
                item
                for item in (self._gold_proxy, self._legacy_gold)
                if item is not None and item.is_configured()
            )
            unavailable_code = "GOLD_WEEKEND_REFERENCE_UNAVAILABLE"
        elif current_weekend and instrument.instrument_id == _LIGHT_OIL:
            proxies = (self._oil_proxy,) if self._oil_proxy.is_configured() else ()
            unavailable_code = "OIL_WEEKEND_REFERENCE_UNAVAILABLE"
        proxy_error_codes: list[str] = []
        proxy_diagnostics: list[ProviderFailureDiagnostic] = []
        for proxy in proxies:
            try:
                return await proxy.get_quote(instrument, as_of)
            except TradingPartnerError as error:
                proxy_error_codes.append(error.code)
                proxy_diagnostics.append(
                    _failure_diagnostic(proxy.provider_name, "weekend_quote", error)
                )
                continue
        try:
            primary = await self._primary.get_quote(instrument, as_of)
        except TradingPartnerError as error:
            primary_diagnostic = _failure_diagnostic(
                self._primary.provider_name,
                "primary_quote",
                error,
            )
            details = dict(error.details)
            details["provider_diagnostics"] = [
                _diagnostic_payload(item) for item in (*proxy_diagnostics, primary_diagnostic)
            ]
            raise type(error)(
                error.message,
                details=details,
                retryable=error.retryable,
                code=error.code,
            ) from None
        if proxies:
            return ProviderSuccess(
                value=primary.value,
                meta=replace(
                    primary.meta,
                    warnings=tuple(
                        dict.fromkeys(
                            (*primary.meta.warnings, unavailable_code, *proxy_error_codes)
                        )
                    ),
                    diagnostics=tuple((*primary.meta.diagnostics, *proxy_diagnostics)),
                ),
            )
        return primary

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


__all__ = [
    "BinancePaxgUsdcWeekendAdapter",
    "HyperliquidClUsdcWeekendAdapter",
    "WeekendReferenceFallbackSpotAdapter",
]
