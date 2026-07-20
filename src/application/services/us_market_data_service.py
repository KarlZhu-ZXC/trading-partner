"""US market quote/bars thin product service (Phase 1F F2c).

Routes quote and OHLCV through ``ProviderRouter`` with exact protocol
``isinstance`` narrowing, stable ``us.quote.v1`` / ``us.bars.v1`` operation
names, secret-free fingerprints, injected codecs, and strict result validators.

Context composition lives in ``USMarketContextService``.
"""

from __future__ import annotations

from datetime import date, datetime

from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.us_market_providers import USBarsProvider, USQuoteProvider
from application.services.provider_router import ProviderRouter
from domain.common.enums import AdjustmentMethod, AssetType, DataCategory, Market
from domain.common.errors import DataContractError
from domain.common.time import ensure_utc, require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries, USQuote

OP_QUOTE = "us.quote.v1"
OP_BARS = "us.bars.v1"

_QUOTE_ASSET_TYPES = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})


def _as_of_utc_z(as_of: datetime) -> str:
    """Canonical UTC Z form for fingerprints (no secrets)."""
    utc = ensure_utc(as_of)
    text = utc.isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _sorted_params(params: dict[str, str]) -> str:
    return ",".join(f"{k}={params[k]}" for k in sorted(params))


def build_us_fingerprint(
    operation: str,
    instrument_id: str,
    params: dict[str, str],
    as_of: datetime,
) -> str:
    """Canonical secret-free fingerprint body.

    Template: ``v1|{operation}|{instrument_id}|{sorted params}|{as_of_utc_z}``
    """
    return (
        f"v1|{operation}|{instrument_id}|"
        f"{_sorted_params(params)}|{_as_of_utc_z(as_of)}"
    )


def _require_exact_date(value: object, *, field: str) -> date:
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date (not datetime)",
            details={"field": field, "rule": "exact_date_type"},
        )
    return value


class USMarketDataService:
    """F2c thin service: quote and bars are Router-backed capabilities."""

    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        quote_codec: ProviderCacheCodec[USQuote],
        bars_codec: ProviderCacheCodec[USBarSeries],
    ) -> None:
        if router is None or clock is None:
            raise DataContractError(
                "router and clock are required",
                details={"field": "dependencies", "rule": "required"},
            )
        for name, codec in (
            ("quote_codec", quote_codec),
            ("bars_codec", bars_codec),
        ):
            if codec is None or not hasattr(codec, "codec_id"):
                raise DataContractError(
                    f"{name} must be a ProviderCacheCodec",
                    details={"field": name, "rule": "required"},
                )
        self._router = router
        self._clock = clock
        self._quote_codec = quote_codec
        self._bars_codec = bars_codec

    def _require_us_tradable(self, instrument: Instrument) -> None:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.US:
            raise DataContractError(
                "instrument market must be US",
                details={"field": "instrument", "rule": "market"},
            )
        if instrument.asset_type not in _QUOTE_ASSET_TYPES:
            raise DataContractError(
                "instrument asset_type must be equity, etf, or index",
                details={
                    "field": "instrument",
                    "rule": "asset_type",
                    "asset_type": instrument.asset_type.value,
                },
            )

    def _require_as_of_not_future(self, as_of: datetime, *, operation: str) -> None:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={
                    "field": "as_of",
                    "rule": "not_future",
                    "operation": operation,
                },
            )

    def _validate_quote(
        self,
        success: ProviderSuccess[USQuote],
        *,
        instrument: Instrument,
        as_of: datetime,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.MARKET_QUOTE:
            raise DataContractError(
                "quote meta.category must be MARKET_QUOTE",
                details={"field": "meta.category", "rule": "category"},
            )
        if not isinstance(success.value, USQuote):
            raise DataContractError(
                "success.value must be USQuote",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        if success.value.instrument_id != instrument.instrument_id:
            raise DataContractError(
                "quote instrument_id must match request",
                details={"field": "instrument_id", "rule": "identity"},
            )
        if success.value.quote_at > as_of:
            raise DataContractError(
                "quote_at must be <= as_of",
                details={"field": "quote_at", "rule": "as_of_cutoff"},
            )
        if success.meta.as_of != as_of:
            raise DataContractError(
                "meta.as_of must match request as_of",
                details={"field": "meta.as_of", "rule": "as_of_identity"},
            )

    def _validate_bars(
        self,
        success: ProviderSuccess[USBarSeries],
        *,
        instrument: Instrument,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.MARKET_OHLCV:
            raise DataContractError(
                "bars meta.category must be MARKET_OHLCV",
                details={"field": "meta.category", "rule": "category"},
            )
        if success.meta.adjustment is not adjustment:
            raise DataContractError(
                "bars meta.adjustment must match request",
                details={"field": "meta.adjustment", "rule": "adjustment"},
            )
        if not isinstance(success.value, USBarSeries):
            raise DataContractError(
                "success.value must be USBarSeries",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        series = success.value
        if series.instrument_id != instrument.instrument_id:
            raise DataContractError(
                "bars instrument_id must match request",
                details={"field": "instrument_id", "rule": "identity"},
            )
        if series.interval is not interval:
            raise DataContractError(
                "bars interval must match request",
                details={"field": "interval", "rule": "interval"},
            )
        if series.adjustment is not adjustment:
            raise DataContractError(
                "bars adjustment must match request",
                details={"field": "adjustment", "rule": "adjustment"},
            )
        if series.start != start:
            raise DataContractError(
                "bars start must match request",
                details={"field": "start", "rule": "range"},
            )
        if series.end != end:
            raise DataContractError(
                "bars end must match request",
                details={"field": "end", "rule": "range"},
            )
        if success.meta.as_of != as_of:
            raise DataContractError(
                "meta.as_of must match request as_of",
                details={"field": "meta.as_of", "rule": "as_of_identity"},
            )
        for idx, bar in enumerate(series.bars):
            if bar.timestamp > as_of:
                raise DataContractError(
                    "bar timestamp must be <= as_of",
                    details={
                        "field": "bars.timestamp",
                        "index": idx,
                        "rule": "as_of_cutoff",
                    },
                )

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> RouterExecutionResult[USQuote]:
        self._require_us_tradable(instrument)
        self._require_as_of_not_future(as_of, operation=OP_QUOTE)
        fingerprint = build_us_fingerprint(
            OP_QUOTE, instrument.instrument_id, {}, as_of
        )

        async def _call(adapter: CategoryProvider) -> ProviderSuccess[USQuote]:
            if not isinstance(adapter, USQuoteProvider):
                raise DataContractError(
                    "adapter does not implement required US quote protocol",
                    details={
                        "category": DataCategory.MARKET_QUOTE.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_quote(instrument, as_of)

        def _validator(success: ProviderSuccess[USQuote]) -> None:
            self._validate_quote(success, instrument=instrument, as_of=as_of)

        return await self._router.execute(
            market=Market.US,
            category=DataCategory.MARKET_QUOTE,
            call=_call,
            operation_name=OP_QUOTE,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=None,
            bypass_cache=False,
            cache_codec=self._quote_codec,
            result_validator=_validator,
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
    ) -> RouterExecutionResult[USBarSeries]:
        self._require_us_tradable(instrument)
        self._require_as_of_not_future(as_of, operation=OP_BARS)
        start = _require_exact_date(start, field="start")
        end = _require_exact_date(end, field="end")
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(interval, USBarInterval):
            raise DataContractError(
                "interval must be USBarInterval",
                details={"field": "interval", "rule": "type"},
            )
        if not isinstance(adjustment, AdjustmentMethod):
            raise DataContractError(
                "adjustment must be AdjustmentMethod",
                details={"field": "adjustment", "rule": "type"},
            )
        params = {
            "adjustment": adjustment.value,
            "end": end.isoformat(),
            "interval": interval.value,
            "start": start.isoformat(),
        }
        fingerprint = build_us_fingerprint(
            OP_BARS, instrument.instrument_id, params, as_of
        )

        async def _call(adapter: CategoryProvider) -> ProviderSuccess[USBarSeries]:
            if not isinstance(adapter, USBarsProvider):
                raise DataContractError(
                    "adapter does not implement required US bars protocol",
                    details={
                        "category": DataCategory.MARKET_OHLCV.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_bars(
                instrument,
                start=start,
                end=end,
                interval=interval,
                adjustment=adjustment,
                as_of=as_of,
            )

        def _validator(success: ProviderSuccess[USBarSeries]) -> None:
            self._validate_bars(
                success,
                instrument=instrument,
                start=start,
                end=end,
                interval=interval,
                adjustment=adjustment,
                as_of=as_of,
            )

        return await self._router.execute(
            market=Market.US,
            category=DataCategory.MARKET_OHLCV,
            call=_call,
            operation_name=OP_BARS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=None,
            bypass_cache=False,
            cache_codec=self._bars_codec,
            result_validator=_validator,
        )
