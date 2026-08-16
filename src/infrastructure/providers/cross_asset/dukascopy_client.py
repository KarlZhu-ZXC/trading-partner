"""Dukascopy Jetta adapter (OTC metals / rolling CFDs).

Primary transport follows the current ``dukascopy-node`` Jetta bucket API.
The older key-backed Trading Tools API remains an optional compatibility
fallback when ``DUKASCOPY_API_KEY`` is configured.

Deterministic only: no skill, LLM, browser, or scraping dependency.
The optional key is never logged or fingerprinted.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpTransport
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
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
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from domain.common.time import require_aware_datetime
from domain.cross_asset.enums import OfferSide, SpotVenueBasis, SpotVolumeBasis
from domain.cross_asset.spot_models import CommoditySpotBarSeries, SpotObservation
from domain.instruments.models import Instrument
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from infrastructure.providers.common.adapter_guards import require_as_of
from infrastructure.providers.cross_asset.dukascopy_codecs import (
    clamp_historical_count,
    decode_current_prices,
    decode_historical_prices,
    decode_instrument_list,
    decode_jetta_candles,
    dukascopy_instrument_code,
    dukascopy_jetta_instrument_code,
    loads_dukascopy_json,
    require_offer_side,
    supported_bar_intervals,
    supported_jetta_instrument_codes,
    timeframe_for_interval,
)
from infrastructure.providers.cross_asset.dukascopy_jetta import (
    JETTA_BATCH_PAUSE_SECONDS,
    JETTA_BATCH_SIZE,
    JETTA_ROOT,
    DukascopyBucketRequest,
    generate_jetta_bucket_requests,
)
from infrastructure.system.clock import SystemClock

_HOST = "https://freeserv.dukascopy.com"
_PATH = "/2.0/"
_SOURCE = VendorId.DUKASCOPY.value
_JSON_CONTENT = ("application/json", "text/json", "text/javascript", "text/plain", "*/*")

_OTC_BROKER_WARNINGS = (
    "OTC_BROKER_FEED",
    "VOLUME_BEST_BID_ASK_NOT_EXCHANGE",
)
_PRECIOUS_SPOT_WARNINGS = (
    "DUKASCOPY_SWFX_NOT_LBMA",
    *_OTC_BROKER_WARNINGS,
)
_CFD_WARNINGS = (
    "ROLLING_CFD_NOT_SPOT",
    *_OTC_BROKER_WARNINGS,
)

_UNIT_BY_INSTRUMENT: dict[str, str] = {
    "commodity_spot:OTC:XAUUSD": "USD/oz",
    "commodity_spot:OTC:XAGUSD": "USD/oz",
    "cfd:OTC:COPPER_CMD_USD": "USD/lb",
    "cfd:OTC:LIGHT_CMD_USD": "USD/bbl",
}


def _meta(
    *,
    category: DataCategory,
    as_of: datetime,
    fetched_at: datetime,
    freshness: Freshness,
    session: TradingSession,
    warnings: tuple[str, ...],
    adjustment: AdjustmentMethod | None = None,
    cache_disposition: CacheDisposition = CacheDisposition.MISS,
    data_delay_seconds: int | None = None,
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.DUKASCOPY,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=as_of,
        fetched_at=fetched_at,
        freshness=freshness,
        session=session,
        latency_ms=None,
        cache_disposition=cache_disposition,
        adjustment=adjustment,
        data_delay_seconds=data_delay_seconds,
        warnings=warnings,
    )


def _content_type_ok(headers: dict[str, str] | object) -> bool:
    if not isinstance(headers, dict):
        return False
    raw = headers.get("content-type") or headers.get("Content-Type")
    if not isinstance(raw, str) or not raw.strip():
        return False
    lowered = raw.split(";", 1)[0].strip().casefold()
    return any(token in lowered for token in _JSON_CONTENT)


def _warnings_for(instrument: Instrument) -> tuple[str, ...]:
    if instrument.asset_type is AssetType.CFD:
        return _CFD_WARNINGS
    if instrument.asset_type is AssetType.COMMODITY_SPOT and instrument.symbol in {
        "XAUUSD",
        "XAGUSD",
    }:
        return _PRECIOUS_SPOT_WARNINGS
    return _OTC_BROKER_WARNINGS


def _unit_for(instrument_id: str) -> str:
    return _UNIT_BY_INSTRUMENT.get(instrument_id, "USD")


def _day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC)


def _day_end_exclusive_ms(day: date) -> int:
    next_day = day + timedelta(days=1)
    return int(_day_start_utc(next_day).timestamp() * 1000)


def _estimate_bar_count(start: date, end: date, interval: USBarInterval) -> int:
    days = (end - start).days + 1
    if days < 1:
        return 1
    if interval is USBarInterval.ONE_DAY:
        return clamp_historical_count(days)
    if interval is USBarInterval.SIXTY_MINUTES:
        return clamp_historical_count(days * 24)
    if interval is USBarInterval.ONE_MINUTE:
        return clamp_historical_count(days * 24 * 60)
    # Unreachable when callers use supported intervals only.
    return clamp_historical_count(days)


class _LegacyDukascopySpotAdapter:
    """Optional key-backed compatibility adapter for the old Trading Tools API."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        api_key: str | None = None,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        proxy_configured: bool = False,
    ) -> None:
        if (
            not isinstance(timeout_seconds, int | float)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be positive",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        # Normalize empty/whitespace key to None; never store fingerprints.
        key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._api_key = key
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._proxy_configured = bool(proxy_configured)
        self._instrument_ids: dict[str, int] = {}

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.DUKASCOPY

    @property
    def provider_name(self) -> str:
        return VendorId.DUKASCOPY.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.OTC and category in {
            DataCategory.MARKET_QUOTE,
            DataCategory.MARKET_OHLCV,
        }

    def is_configured(self) -> bool:
        return self._enabled and self._api_key is not None

    def _require_configured(self) -> None:
        if not self._enabled:
            raise ProviderNotConfigured(
                "Dukascopy adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )
        if self._api_key is None:
            raise ProviderNotConfigured(
                "Dukascopy Trading Tools API key is not configured",
                details={
                    "vendor": self.vendor_id.value,
                    "configuration_field": "DUKASCOPY_API_KEY",
                },
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        return require_as_of(as_of=as_of, clock_now=self._clock.now())

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/javascript,text/plain,*/*",
            "User-Agent": self._user_agent,
        }

    def _params(self, path: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        params: dict[str, str] = {"path": path}
        if extra:
            params.update(extra)
        # Attach free key only when present; never log the value.
        if self._api_key is not None:
            params["key"] = self._api_key
        return params

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Dukascopy endpoint rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                },
            )
        if status_code in {401, 403}:
            raise ProviderAuthenticationError(
                "Dukascopy endpoint requires authentication or free key",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "auth",
                    # Do not disclose whether a key was present.
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Dukascopy endpoint HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    async def _get_json(
        self,
        *,
        path: str,
        params: dict[str, str],
        operation: str,
    ) -> tuple[object, datetime]:
        request = HttpRequest(
            method="GET",
            url=f"{_HOST}{_PATH}",
            params=self._params(path, params),
            headers=self._headers(),
            body=None,
            timeout_seconds=self._timeout_seconds,
        )
        try:
            response = await self._transport.send(request)
        except ProviderUnavailableError as exc:
            details: dict[str, object] = {
                "vendor": self.vendor_id.value,
                "operation": operation,
                "error_type": exc.details.get("error_type", "transport_failure"),
                "network_route": "proxy" if self._proxy_configured else "direct",
            }
            status_class = exc.details.get("status_class")
            if isinstance(status_class, str):
                details["status_class"] = status_class
            raise ProviderUnavailableError(
                "Dukascopy network request failed",
                details=details,
                retryable=exc.retryable,
            ) from None
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        self._raise_for_http_status(response.status_code, operation=operation)
        if not _content_type_ok(dict(response.headers)):
            raise DataContractError(
                "Dukascopy response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "content_type",
                },
            )
        return loads_dukascopy_json(response.body, operation=operation), fetched_at

    def _validate_instrument(self, instrument: Instrument) -> str:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument"},
            )
        if instrument.market is not Market.OTC:
            raise DataContractError(
                "Dukascopy adapter only serves Market.OTC instruments",
                details={"field": "market", "rule": "otc_only"},
            )
        if instrument.asset_type not in {AssetType.COMMODITY_SPOT, AssetType.CFD}:
            raise DataContractError(
                "Dukascopy adapter only serves commodity_spot or cfd instruments",
                details={"field": "asset_type", "rule": "asset_type"},
            )
        if instrument.instrument_id != (
            f"{instrument.asset_type.value}:{instrument.market.value}:{instrument.symbol}"
        ):
            raise DataContractError(
                "instrument_id must match asset_type:market:symbol",
                details={"field": "instrument_id", "rule": "identity"},
            )
        return dukascopy_instrument_code(instrument.instrument_id)

    async def list_instruments(self, as_of: datetime) -> ProviderSuccess[tuple[str, ...]]:
        """Discovery helper: free instrumentList codes (not a public MCP tool)."""
        self._require_configured()
        self._require_as_of(as_of)
        payload, fetched_at = await self._get_json(
            path="api/instrumentList",
            params={},
            operation="instrumentList",
        )
        rows = decode_instrument_list(payload)
        if not rows:
            raise NoMarketData(
                "Dukascopy instrumentList returned no instruments",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "instrumentList",
                    "code": "NO_MARKET_DATA",
                },
            )
        codes = tuple(row.code for row in rows)
        self._instrument_ids.update(
            {row.code: row.instrument_id for row in rows if row.instrument_id is not None}
        )
        return ProviderSuccess(
            value=codes,
            meta=_meta(
                category=DataCategory.INSTRUMENT_MASTER,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.UNKNOWN,
                session=TradingSession.UNKNOWN,
                warnings=("DUKASCOPY_FREE_REFERENCE",),
            ),
        )

    async def _historical_instrument_id(self, code: str) -> int:
        cached = self._instrument_ids.get(code)
        if cached is not None:
            return cached
        payload, _ = await self._get_json(
            path="api/instrumentList",
            params={
                "instruments": code,
                "fields": "id,name,nameLong",
            },
            operation="instrumentList",
        )
        rows = decode_instrument_list(payload)
        for row in rows:
            if row.instrument_id is not None:
                self._instrument_ids[row.code] = row.instrument_id
        match = next((row for row in rows if row.code == code), None)
        if match is None or match.instrument_id is None:
            raise NoMarketData(
                "Dukascopy instrumentList returned no historical instrument id",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "instrumentList",
                    "code": "NO_MARKET_DATA",
                },
            )
        return match.instrument_id

    async def get_quote(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[SpotObservation]:
        self._require_configured()
        self._require_as_of(as_of)
        duka_code = self._validate_instrument(instrument)
        payload, fetched_at = await self._get_json(
            path="api/currentPrices",
            params={"instruments": duka_code},
            operation="currentPrices",
        )
        rows = decode_current_prices(payload)
        match = next((row for row in rows if row.instrument_code == duka_code), None)
        if match is None and len(rows) == 1:
            # Some responses omit the instrument label when a single code is requested.
            match = rows[0]
        if match is None:
            raise NoMarketData(
                "Dukascopy currentPrices returned no matching quote",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "currentPrices",
                    "code": "NO_MARKET_DATA",
                },
            )
        bid = match.bid
        ask = match.ask
        last = match.last
        mid: Decimal | None = None
        if bid is not None and ask is not None:
            mid = (bid + ask) / Decimal("2")
        quote_at = match.quote_at
        if quote_at is not None and quote_at > as_of:
            # Publication-time cutoff: future-visible quotes are not returned.
            raise NoMarketData(
                "Dukascopy quote is not visible at as_of",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "currentPrices",
                    "code": "NOT_VISIBLE_AT_AS_OF",
                },
            )
        observation = SpotObservation(
            instrument_id=instrument.instrument_id,
            currency=instrument.currency or "USD",
            unit=_unit_for(instrument.instrument_id),
            quote_at=quote_at,
            venue_basis=SpotVenueBasis.DUKASCOPY_SWFX,
            source=_SOURCE,
            bid=bid,
            ask=ask,
            mid=mid,
            last=last,
            delivery_location=None,
        )
        freshness = Freshness.FRESH if quote_at is not None else Freshness.UNKNOWN
        return ProviderSuccess(
            value=observation,
            meta=_meta(
                category=DataCategory.MARKET_QUOTE,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=freshness,
                session=TradingSession.UNKNOWN,
                warnings=_warnings_for(instrument),
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
        self._require_configured()
        self._require_as_of(as_of)
        duka_code = self._validate_instrument(instrument)
        if not isinstance(start, date) or isinstance(start, datetime):
            raise DataContractError(
                "start must be date",
                details={"field": "start"},
            )
        if not isinstance(end, date) or isinstance(end, datetime):
            raise DataContractError(
                "end must be date",
                details={"field": "end"},
            )
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(interval, USBarInterval):
            raise DataContractError(
                "interval must be USBarInterval",
                details={"field": "interval"},
            )
        if interval not in supported_bar_intervals():
            raise DataContractError(
                "bar interval is not verified for Dukascopy historicalPrices",
                details={
                    "vendor": self.vendor_id.value,
                    "field": "interval",
                    "rule": "verified_timeframe_only",
                    "interval": interval.value,
                    "supported": sorted(i.value for i in supported_bar_intervals()),
                },
            )
        if not isinstance(adjustment, AdjustmentMethod):
            raise DataContractError(
                "adjustment must be AdjustmentMethod",
                details={"field": "adjustment"},
            )
        if adjustment is not AdjustmentMethod.NONE:
            raise DataContractError(
                "commodity spot/CFD bars require adjustment=none",
                details={
                    "vendor": self.vendor_id.value,
                    "field": "adjustment",
                    "rule": "none_only",
                },
            )
        side = require_offer_side(offer_side)
        timeframe = timeframe_for_interval(interval)
        historical_instrument_id = await self._historical_instrument_id(duka_code)
        count = _estimate_bar_count(start, end, interval)
        start_ms = int(_day_start_utc(start).timestamp() * 1000)
        end_ms = _day_end_exclusive_ms(end) - 1
        # as_of publication cutoff: do not request future-visible bars.
        as_of_ms = int(as_of.timestamp() * 1000)
        if end_ms > as_of_ms:
            end_ms = as_of_ms
        if end_ms < start_ms:
            raise NoMarketData(
                "Dukascopy historical range is empty under as_of cutoff",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "historicalPrices",
                    "code": "NO_MARKET_DATA",
                },
            )
        payload, fetched_at = await self._get_json(
            path="api/historicalPrices",
            params={
                "instrument": str(historical_instrument_id),
                "timeFrame": timeframe,
                "count": str(count),
                "start": str(start_ms),
                "end": str(end_ms),
                "dayStartTime": "UTC",
                "offerSide": side.value,
            },
            operation="historicalPrices",
        )
        bars = decode_historical_prices(payload)
        # Inclusive UTC day filter after decode (defensive against vendor extras).
        filtered = tuple(
            bar
            for bar in bars
            if start <= bar.timestamp.astimezone(UTC).date() <= end and bar.timestamp <= as_of
        )
        if not filtered:
            raise NoMarketData(
                "Dukascopy historicalPrices returned no bars in range",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "historicalPrices",
                    "code": "NO_MARKET_DATA",
                },
            )
        series = CommoditySpotBarSeries(
            instrument_id=instrument.instrument_id,
            interval=interval,
            offer_side=side,
            start=start,
            end=end,
            adjustment=AdjustmentMethod.NONE,
            bars=filtered,
            volume_basis=SpotVolumeBasis.BEST_BID_ASK_VOLUME,
        )
        return ProviderSuccess(
            value=series,
            meta=_meta(
                category=DataCategory.MARKET_OHLCV,
                as_of=as_of,
                fetched_at=fetched_at,
                freshness=Freshness.DELAYED,
                session=TradingSession.UNKNOWN,
                warnings=_warnings_for(instrument),
                adjustment=AdjustmentMethod.NONE,
            ),
        )


_JETTA_QUOTE_WARNING = "DUKASCOPY_MINUTE_CLOSE_QUOTE_PROXY"
_LEGACY_FALLBACK_WARNING = "DUKASCOPY_LEGACY_KEY_API_FALLBACK"
_JETTA_CACHE_MAX_ITEMS = 512
_QUOTE_LOOKBACK_DAYS = 7
_QUOTE_FRESH_SECONDS = 120


class DukascopySpotAdapter:
    """CommoditySpotProvider using the current dukascopy-node Jetta strategy."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        api_key: str | None = None,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        proxy_configured: bool = False,
    ) -> None:
        if (
            not isinstance(timeout_seconds, int | float)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be positive",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._proxy_configured = bool(proxy_configured)
        self._legacy = (
            _LegacyDukascopySpotAdapter(
                transport,
                clock=self._clock,
                enabled=self._enabled,
                api_key=key,
                timeout_seconds=self._timeout_seconds,
                user_agent=self._user_agent,
                proxy_configured=self._proxy_configured,
            )
            if key is not None
            else None
        )
        # Completed Jetta buckets are immutable.  A bounded process-local cache
        # avoids repeated downloads without introducing a persistence schema.
        self._completed_bucket_cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.DUKASCOPY

    @property
    def provider_name(self) -> str:
        return VendorId.DUKASCOPY.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.OTC and category in {
            DataCategory.MARKET_QUOTE,
            DataCategory.MARKET_OHLCV,
        }

    def is_configured(self) -> bool:
        return self._enabled

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise ProviderNotConfigured(
                "Dukascopy adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        return now

    def _validate_instrument(self, instrument: Instrument) -> str:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument"},
            )
        if instrument.market is not Market.OTC:
            raise DataContractError(
                "Dukascopy adapter only serves Market.OTC instruments",
                details={"field": "market", "rule": "otc_only"},
            )
        if instrument.asset_type not in {AssetType.COMMODITY_SPOT, AssetType.CFD}:
            raise DataContractError(
                "Dukascopy adapter only serves commodity_spot or cfd instruments",
                details={"field": "asset_type", "rule": "asset_type"},
            )
        expected = f"{instrument.asset_type.value}:{instrument.market.value}:{instrument.symbol}"
        if instrument.instrument_id != expected:
            raise DataContractError(
                "instrument_id must match asset_type:market:symbol",
                details={"field": "instrument_id", "rule": "identity"},
            )
        return dukascopy_jetta_instrument_code(instrument.instrument_id)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self._user_agent,
        }

    def _raise_for_jetta_status(self, status_code: int, *, operation: str) -> None:
        if status_code == 429:
            raise ProviderRateLimitError(
                "Dukascopy Jetta endpoint rate limited",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "rate_limit",
                },
            )
        if status_code in {401, 403}:
            raise ProviderAuthenticationError(
                "Dukascopy Jetta endpoint access denied",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "access_denied",
                },
            )
        if status_code < 200 or status_code >= 300:
            raise ProviderUnavailableError(
                "Dukascopy Jetta endpoint HTTP failure",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "error_type": "http_status",
                    "status_class": f"{status_code // 100}xx",
                },
            )

    async def _fetch_jetta_request(
        self,
        request_spec: DukascopyBucketRequest,
        *,
        operation: str,
    ) -> tuple[object | None, bool]:
        if not request_spec.mutable:
            cached = self._completed_bucket_cache.get(request_spec.url)
            if cached is not None:
                self._completed_bucket_cache.move_to_end(request_spec.url)
                return loads_dukascopy_json(cached, operation=operation), True

        request = HttpRequest(
            method="GET",
            url=request_spec.url,
            params=request_spec.params,
            headers=self._headers(),
            body=None,
            timeout_seconds=self._timeout_seconds,
        )
        try:
            response = await self._transport.send(request)
        except ProviderUnavailableError as exc:
            details: dict[str, object] = {
                "vendor": self.vendor_id.value,
                "operation": operation,
                "error_type": exc.details.get("error_type", "transport_failure"),
                "network_route": "proxy" if self._proxy_configured else "direct",
            }
            status_class = exc.details.get("status_class")
            if isinstance(status_class, str):
                details["status_class"] = status_class
            raise ProviderUnavailableError(
                "Dukascopy Jetta network request failed",
                details=details,
                retryable=exc.retryable,
            ) from None

        if response.status_code in {204, 404}:
            return None, False
        self._raise_for_jetta_status(response.status_code, operation=operation)
        if not _content_type_ok(dict(response.headers)):
            raise DataContractError(
                "Dukascopy Jetta response Content-Type is not acceptable",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": operation,
                    "rule": "content_type",
                },
            )
        payload = loads_dukascopy_json(response.body, operation=operation)
        if not request_spec.mutable:
            self._completed_bucket_cache[request_spec.url] = response.body
            self._completed_bucket_cache.move_to_end(request_spec.url)
            while len(self._completed_bucket_cache) > _JETTA_CACHE_MAX_ITEMS:
                self._completed_bucket_cache.popitem(last=False)
        return payload, False

    async def _fetch_jetta_batch(
        self,
        requests: tuple[DukascopyBucketRequest, ...],
        *,
        operation: str,
    ) -> tuple[tuple[object, ...], datetime, CacheDisposition]:
        payloads: list[object] = []
        all_cache_hits = bool(requests)
        for offset in range(0, len(requests), JETTA_BATCH_SIZE):
            batch = requests[offset : offset + JETTA_BATCH_SIZE]
            results = await asyncio.gather(
                *(self._fetch_jetta_request(item, operation=operation) for item in batch)
            )
            batch_all_cache_hits = all(hit for _, hit in results)
            all_cache_hits = all_cache_hits and batch_all_cache_hits
            payloads.extend(payload for payload, _ in results if payload is not None)
            has_next_batch = offset + JETTA_BATCH_SIZE < len(requests)
            if has_next_batch and not batch_all_cache_hits:
                await asyncio.sleep(JETTA_BATCH_PAUSE_SECONDS)
        fetched_at = self._clock.now()
        require_aware_datetime(fetched_at, field_name="fetched_at")
        disposition = CacheDisposition.HIT if all_cache_hits else CacheDisposition.MISS
        return tuple(payloads), fetched_at, disposition

    async def _get_jetta_bars(
        self,
        *,
        code: str,
        start_at: datetime,
        end_at: datetime,
        interval: USBarInterval,
        offer_side: OfferSide,
        as_of: datetime,
        now: datetime,
    ) -> tuple[tuple[MarketBar, ...], datetime, CacheDisposition]:
        requests = generate_jetta_bucket_requests(
            instrument_code=code,
            interval=interval,
            offer_side=offer_side,
            start_at=start_at,
            end_at=end_at,
            now=now,
        )
        payloads, fetched_at, disposition = await self._fetch_jetta_batch(
            requests,
            operation="jetta_candles",
        )
        bars_by_time: dict[datetime, MarketBar] = {}
        for payload in payloads:
            for bar in decode_jetta_candles(payload):
                if start_at <= bar.timestamp < end_at and bar.timestamp <= as_of:
                    bars_by_time[bar.timestamp] = bar
        return (
            tuple(bars_by_time[key] for key in sorted(bars_by_time)),
            fetched_at,
            disposition,
        )

    @staticmethod
    def _legacy_result[T](result: ProviderSuccess[T]) -> ProviderSuccess[T]:
        return ProviderSuccess(
            value=result.value,
            meta=replace(
                result.meta,
                role=SourceRole.FALLBACK,
                warnings=(*result.meta.warnings, _LEGACY_FALLBACK_WARNING),
            ),
        )

    async def list_instruments(self, as_of: datetime) -> ProviderSuccess[tuple[str, ...]]:
        """Return the fixed, validated Jetta instrument subset used by this project."""
        self._require_enabled()
        self._require_as_of(as_of)
        requests = tuple(
            DukascopyBucketRequest(
                url=f"{JETTA_ROOT}/instruments/{code}",
                params={},
                mutable=True,
            )
            for code in supported_jetta_instrument_codes()
        )
        try:
            payloads, fetched_at, disposition = await self._fetch_jetta_batch(
                requests,
                operation="jetta_instruments",
            )
            codes: list[str] = []
            for payload in payloads:
                if not isinstance(payload, dict) or not isinstance(payload.get("code"), str):
                    raise DataContractError(
                        "Dukascopy Jetta instrument metadata is malformed",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "jetta_instruments",
                            "rule": "shape",
                        },
                    )
                codes.append(payload["code"])
            if len(codes) != len(requests):
                raise NoMarketData(
                    "Dukascopy Jetta returned incomplete instrument metadata",
                    details={"vendor": self.vendor_id.value, "code": "NO_MARKET_DATA"},
                )
            return ProviderSuccess(
                value=tuple(codes),
                meta=_meta(
                    category=DataCategory.INSTRUMENT_MASTER,
                    as_of=as_of,
                    fetched_at=fetched_at,
                    freshness=Freshness.UNKNOWN,
                    session=TradingSession.UNKNOWN,
                    warnings=("DUKASCOPY_JETTA_REFERENCE",),
                    cache_disposition=disposition,
                ),
            )
        except (DataContractError, NoMarketData, ProviderRateLimitError, ProviderUnavailableError):
            if self._legacy is None:
                raise
            return self._legacy_result(await self._legacy.list_instruments(as_of))

    async def get_quote(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[SpotObservation]:
        self._require_enabled()
        now = self._require_as_of(as_of)
        code = self._validate_instrument(instrument)
        try:
            for days_back in range(_QUOTE_LOOKBACK_DAYS + 1):
                day = as_of.astimezone(UTC).date() - timedelta(days=days_back)
                start_at = _day_start_utc(day)
                end_at = _day_start_utc(day + timedelta(days=1))
                bid_result, ask_result = await asyncio.gather(
                    self._get_jetta_bars(
                        code=code,
                        start_at=start_at,
                        end_at=end_at,
                        interval=USBarInterval.ONE_MINUTE,
                        offer_side=OfferSide.BID,
                        as_of=as_of,
                        now=now,
                    ),
                    self._get_jetta_bars(
                        code=code,
                        start_at=start_at,
                        end_at=end_at,
                        interval=USBarInterval.ONE_MINUTE,
                        offer_side=OfferSide.ASK,
                        as_of=as_of,
                        now=now,
                    ),
                )
                bid_bars, bid_fetched_at, bid_cache = bid_result
                ask_bars, ask_fetched_at, ask_cache = ask_result
                bids = {bar.timestamp: bar.close for bar in bid_bars}
                asks = {bar.timestamp: bar.close for bar in ask_bars}
                common = bids.keys() & asks.keys()
                if not common:
                    continue
                quote_at = max(common)
                bid = bids[quote_at]
                ask = asks[quote_at]
                if bid > ask:
                    raise DataContractError(
                        "Dukascopy Jetta bid must be <= ask",
                        details={
                            "vendor": self.vendor_id.value,
                            "operation": "jetta_quote",
                            "rule": "bid_ask_order",
                        },
                    )
                fetched_at = max(bid_fetched_at, ask_fetched_at)
                delay = max(0, int((fetched_at - quote_at).total_seconds()))
                freshness = Freshness.FRESH if delay <= _QUOTE_FRESH_SECONDS else Freshness.DELAYED
                observation = SpotObservation(
                    instrument_id=instrument.instrument_id,
                    currency=instrument.currency or "USD",
                    unit=_unit_for(instrument.instrument_id),
                    quote_at=quote_at,
                    venue_basis=SpotVenueBasis.DUKASCOPY_SWFX,
                    source=_SOURCE,
                    bid=bid,
                    ask=ask,
                    mid=(bid + ask) / Decimal("2"),
                    last=None,
                    delivery_location=None,
                )
                cache_disposition = (
                    CacheDisposition.HIT
                    if bid_cache is CacheDisposition.HIT and ask_cache is CacheDisposition.HIT
                    else CacheDisposition.MISS
                )
                return ProviderSuccess(
                    value=observation,
                    meta=_meta(
                        category=DataCategory.MARKET_QUOTE,
                        as_of=as_of,
                        fetched_at=fetched_at,
                        freshness=freshness,
                        session=TradingSession.UNKNOWN,
                        warnings=(*_warnings_for(instrument), _JETTA_QUOTE_WARNING),
                        cache_disposition=cache_disposition,
                        data_delay_seconds=delay,
                    ),
                )
            raise NoMarketData(
                "Dukascopy Jetta returned no recent quote",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "jetta_quote",
                    "code": "NO_MARKET_DATA",
                },
            )
        except (DataContractError, NoMarketData, ProviderRateLimitError, ProviderUnavailableError):
            if self._legacy is None:
                raise
            return self._legacy_result(await self._legacy.get_quote(instrument, as_of))

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
        self._require_enabled()
        now = self._require_as_of(as_of)
        code = self._validate_instrument(instrument)
        if not isinstance(start, date) or isinstance(start, datetime):
            raise DataContractError("start must be date", details={"field": "start"})
        if not isinstance(end, date) or isinstance(end, datetime):
            raise DataContractError("end must be date", details={"field": "end"})
        if end < start:
            raise DataContractError(
                "end must be >= start", details={"field": "end", "rule": "range_order"}
            )
        if not isinstance(interval, USBarInterval) or interval not in supported_bar_intervals():
            raise DataContractError(
                "bar interval is not supported by Dukascopy Jetta",
                details={
                    "vendor": self.vendor_id.value,
                    "field": "interval",
                    "rule": "jetta_native_bucket",
                },
            )
        if adjustment is not AdjustmentMethod.NONE:
            raise DataContractError(
                "commodity spot/CFD bars require adjustment=none",
                details={
                    "vendor": self.vendor_id.value,
                    "field": "adjustment",
                    "rule": "none_only",
                },
            )
        side = require_offer_side(offer_side)
        start_at = _day_start_utc(start)
        end_at = _day_start_utc(end + timedelta(days=1))
        if start_at > as_of:
            raise NoMarketData(
                "Dukascopy historical range is empty under as_of cutoff",
                details={
                    "vendor": self.vendor_id.value,
                    "operation": "jetta_candles",
                    "code": "NO_MARKET_DATA",
                },
            )
        try:
            bars, fetched_at, disposition = await self._get_jetta_bars(
                code=code,
                start_at=start_at,
                end_at=end_at,
                interval=interval,
                offer_side=side,
                as_of=as_of,
                now=now,
            )
            if not bars:
                raise NoMarketData(
                    "Dukascopy Jetta returned no bars in range",
                    details={
                        "vendor": self.vendor_id.value,
                        "operation": "jetta_candles",
                        "code": "NO_MARKET_DATA",
                    },
                )
            series = CommoditySpotBarSeries(
                instrument_id=instrument.instrument_id,
                interval=interval,
                offer_side=side,
                start=start,
                end=end,
                adjustment=AdjustmentMethod.NONE,
                bars=bars,
                volume_basis=SpotVolumeBasis.BEST_BID_ASK_VOLUME,
            )
            return ProviderSuccess(
                value=series,
                meta=_meta(
                    category=DataCategory.MARKET_OHLCV,
                    as_of=as_of,
                    fetched_at=fetched_at,
                    freshness=Freshness.DELAYED,
                    session=TradingSession.UNKNOWN,
                    warnings=_warnings_for(instrument),
                    adjustment=AdjustmentMethod.NONE,
                    cache_disposition=disposition,
                ),
            )
        except (DataContractError, NoMarketData, ProviderRateLimitError, ProviderUnavailableError):
            if self._legacy is None:
                raise
            return self._legacy_result(
                await self._legacy.get_bars(
                    instrument,
                    start=start,
                    end=end,
                    interval=interval,
                    adjustment=adjustment,
                    as_of=as_of,
                    offer_side=offer_side,
                )
            )


__all__ = ["DukascopySpotAdapter"]
