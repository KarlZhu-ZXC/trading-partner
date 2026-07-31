"""US MCP-facing tool coordinator (Phase 1F F3b).

Samples request_id / effective as_of once, resolves instruments, delegates to
US product services, and aggregates router provenance into ToolEnvelope.
Does not select vendors or import MCP/interface/infrastructure layers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar
from zoneinfo import ZoneInfo

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.provider_routing import ProviderResultMeta, RouterExecutionResult
from application.dto.tool_envelope import (
    ErrorInfo,
    SourceReference,
    ToolEnvelope,
    WarningInfo,
)
from application.dto.us_market import (
    MarketGetBarsInput,
    MarketGetContextInput,
    MarketGetSnapshotInput,
    TechnicalGetSnapshotInput,
    USBarSeriesDTO,
    USCompositeSnapshotDTO,
    USGetSnapshotInput,
    USMarketContextDTO,
    USQuoteDTO,
    USTechnicalSnapshotDTO,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.services.instrument_access_service import InstrumentAccessService
from application.services.us_market_context_service import (
    USMarketContextResult,
    USMarketContextService,
)
from application.services.us_market_data_service import USMarketDataService
from application.services.us_technical_service import USTechnicalService
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
)
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.ids import EntityIdPrefix
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from domain.us_market.models import (
    USBarSeries,
    USCompositeSnapshot,
    USMarketContext,
    USQuote,
    USTechnicalSnapshot,
)

T = TypeVar("T")
U = TypeVar("U")

_NEW_YORK = ZoneInfo("America/New_York")

_FRESHNESS_WORST_ORDER: dict[Freshness, int] = {
    Freshness.FRESH: 0,
    Freshness.DELAYED: 1,
    Freshness.STALE: 2,
    Freshness.UNKNOWN: 3,
}

_FALLBACK_WARNING = WarningInfo(
    code="FALLBACK_US_SOURCE",
    message="One or more US components used a configured fallback source.",
    details={},
)
_DELAYED_WARNING = WarningInfo(
    code="DELAYED_US_DATA",
    message="One or more US components are delayed.",
    details={},
)
_STALE_WARNING = WarningInfo(
    code="STALE_US_DATA",
    message="One or more US components are stale.",
    details={},
)
_CLOSED_SESSION_LAST_KNOWN_WARNING = WarningInfo(
    code="CLOSED_SESSION_LAST_KNOWN",
    message="The market is closed; this is the latest known session value.",
    details={},
)
_UNKNOWN_FRESHNESS_WARNING = WarningInfo(
    code="UNKNOWN_US_FRESHNESS",
    message="One or more US components have unknown freshness.",
    details={},
)
_YAHOO_KR_DELAYED_QUOTE = WarningInfo(
    code="YAHOO_KR_DELAYED_QUOTE",
    message=(
        "Yahoo labels Korea Exchange quotes as delayed but does not provide a "
        "stable declared delay in this response; inspect data_delay_seconds."
    ),
    details={},
)
_CONTEXT_UNAVAILABLE = WarningInfo(
    code="US_CONTEXT_UNAVAILABLE",
    message="US market context is temporarily unavailable.",
    details={},
)
_TECHNICAL_UNAVAILABLE = WarningInfo(
    code="US_TECHNICAL_UNAVAILABLE",
    message="US technical snapshot is temporarily unavailable.",
    details={},
)
_BREADTH_UNAVAILABLE = WarningInfo(
    code="US_BREADTH_UNAVAILABLE",
    message="US advancing, declining, and unchanged counts are unavailable.",
    details={},
)
_SECTOR_ROTATION_UNAVAILABLE = WarningInfo(
    code="US_SECTOR_ROTATION_UNAVAILABLE",
    message="US Yahoo sector-index rotation is unavailable.",
    details={},
)
_YAHOO_BREADTH_UNOFFICIAL_UNIVERSE = WarningInfo(
    code="YAHOO_BREADTH_UNOFFICIAL_UNIVERSE",
    message=(
        "Yahoo screener breadth covers a disclosed listed-security universe that "
        "may include ETFs and ADRs; it is not official exchange common-stock breadth."
    ),
    details={},
)
_FUTURES_CONTRACT_NOT_SPOT = WarningInfo(
    code="FUTURES_CONTRACT_NOT_SPOT",
    message=(
        "This price belongs to an exchange-traded futures proxy, not OTC spot. "
        "Do not reuse its exact levels as XAUUSD/XAGUSD spot levels."
    ),
    details={},
)
_CONTINUOUS_FUTURES_ROLL_RISK = WarningInfo(
    code="CONTINUOUS_FUTURES_ROLL_RISK",
    message=(
        "The ROOT=F series follows a vendor-defined continuous future; contract "
        "rolls and cross-vendor construction can create basis changes or artificial "
        "discontinuities."
    ),
    details={},
)
_BEST_EFFORT_PUBLIC_FEED_NO_SLA = WarningInfo(
    code="BEST_EFFORT_PUBLIC_FEED_NO_SLA",
    message="The fallback is a public best-effort feed without a delay or availability SLA.",
    details={},
)
_FUTURES_SESSION_UNKNOWN = WarningInfo(
    code="FUTURES_SESSION_UNKNOWN",
    message="The fallback does not provide a verified futures-session state.",
    details={},
)
_EASTMONEY_DAILY_DERIVED_BARS = WarningInfo(
    code="EASTMONEY_DAILY_DERIVED_BARS",
    message=(
        "Fallback bars originate from Eastmoney daily continuous-futures rows; "
        "date-only rows are anchored to the 17:00 New York futures trade-date "
        "boundary, and weekly/monthly bars are deterministic aggregations."
    ),
    details={},
)
_EXTENDED_HOURS_PRICE = WarningInfo(
    code="EXTENDED_HOURS_PRICE",
    message=(
        "The latest price came from a Yahoo pre-market or post-market minute bar; "
        "extended-hours liquidity may be sparse."
    ),
    details={},
)
_INTRADAY_QUOTE_RECOVERY = WarningInfo(
    code="INTRADAY_QUOTE_RECOVERY",
    message=(
        "The latest quote was recovered from a newer Yahoo minute bar because the "
        "regular quote metadata lagged."
    ),
    details={},
)
_INTRADAY_QUOTE_UNAVAILABLE = WarningInfo(
    code="INTRADAY_QUOTE_UNAVAILABLE",
    message=(
        "Yahoo intraday quote recovery was unavailable; the regular-session "
        "latest-known value remains."
    ),
    details={},
)
_GENERIC_CODE_MESSAGE = "US market data warning."

_KNOWN_WARNINGS = {
    item.code: item
    for item in (
        _FALLBACK_WARNING,
        _DELAYED_WARNING,
        _STALE_WARNING,
        _CLOSED_SESSION_LAST_KNOWN_WARNING,
        _UNKNOWN_FRESHNESS_WARNING,
        _CONTEXT_UNAVAILABLE,
        _TECHNICAL_UNAVAILABLE,
        _BREADTH_UNAVAILABLE,
        _SECTOR_ROTATION_UNAVAILABLE,
        _YAHOO_BREADTH_UNOFFICIAL_UNIVERSE,
        _FUTURES_CONTRACT_NOT_SPOT,
        _CONTINUOUS_FUTURES_ROLL_RISK,
        _BEST_EFFORT_PUBLIC_FEED_NO_SLA,
        _FUTURES_SESSION_UNKNOWN,
        _EASTMONEY_DAILY_DERIVED_BARS,
        _EXTENDED_HOURS_PRICE,
        _INTRADAY_QUOTE_RECOVERY,
        _INTRADAY_QUOTE_UNAVAILABLE,
        _YAHOO_KR_DELAYED_QUOTE,
    )
}


def _warning_from_code(code: str) -> WarningInfo:
    return _KNOWN_WARNINGS.get(
        code,
        WarningInfo(code=code, message=_GENERIC_CODE_MESSAGE, details={}),
    )


class USToolCoordinator:
    """US/CME exchange market product coordinator (quote, bars, US context, composite).

    OTC spot/CFD, futures curves, and basis live on :class:`MarketToolCoordinator`.
    """

    def __init__(
        self,
        *,
        instrument_access: InstrumentAccessService,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        data_service: USMarketDataService,
        context_service: USMarketContextService,
        technical_service: USTechnicalService,
    ) -> None:
        self._instrument_access = instrument_access
        self._clock = clock
        self._id_generator = id_generator
        self._secret_redactor = secret_redactor
        self._data_service = data_service
        self._context_service = context_service
        self._technical_service = technical_service

    async def get_market_snapshot(
        self, request: MarketGetSnapshotInput
    ) -> ToolEnvelope[USQuoteDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._instrument_access.get(
                request.instrument_id, as_of=effective_as_of
            )
            result = await self._data_service.get_quote(instrument, effective_as_of)
            return self._envelope_from_router(
                request_id,
                effective_as_of,
                result,
                dto_factory=USQuoteDTO.from_domain,
                market=instrument.market,
            )
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    async def get_market_bars(self, request: MarketGetBarsInput) -> ToolEnvelope[USBarSeriesDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._instrument_access.get(
                request.instrument_id, as_of=effective_as_of
            )
            adjustment = request.adjustment
            if adjustment is None:
                adjustment = (
                    AdjustmentMethod.NONE
                    if instrument.asset_type is AssetType.FUTURE
                    else AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
                )
            result = await self._data_service.get_bars(
                instrument,
                start=request.start,
                end=request.end,
                interval=request.interval,
                adjustment=adjustment,
                as_of=effective_as_of,
            )
            return self._envelope_from_router(
                request_id,
                effective_as_of,
                result,
                dto_factory=USBarSeriesDTO.from_domain,
                market=instrument.market,
            )
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    async def get_market_context(
        self, request: MarketGetContextInput
    ) -> ToolEnvelope[USMarketContextDTO]:
        """US market proxy/breadth/rotation context only (operation=us_market)."""
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            if request.operation != "us_market":
                raise DataContractError(
                    "USToolCoordinator only serves operation=us_market",
                    details={"operation": request.operation},
                )
            result = await self._context_service.get_context_result(effective_as_of)
            return self._envelope_from_context(request_id, effective_as_of, result)
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    async def get_technical_snapshot(
        self, request: TechnicalGetSnapshotInput
    ) -> ToolEnvelope[USTechnicalSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._instrument_access.get(
                request.instrument_id, as_of=effective_as_of
            )
            bars_result = await self._fetch_technical_bars(
                instrument,
                as_of=effective_as_of,
                lookback_sessions=request.lookback_sessions,
            )
            if not bars_result.ok:
                return self._router_failure(request_id, effective_as_of, bars_result)
            series = bars_result.value
            if not isinstance(series, USBarSeries):
                raise DataContractError(
                    "bars value must be USBarSeries",
                    details={"field": "value", "rule": "type"},
                )
            snapshot = self._technical_service.build_snapshot(
                instrument,
                series=series,
                as_of=effective_as_of,
                lookback_sessions=request.lookback_sessions,
            )
            metas = _metas_from_router(bars_result)
            warnings = _merge_warnings(
                router_warnings=bars_result.warnings,
                metas=metas,
                extra_codes=(),
            )
            return self._success_envelope(
                request_id,
                effective_as_of,
                data=USTechnicalSnapshotDTO.from_domain(snapshot),
                metas=metas,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    async def get_us_snapshot(
        self, request: USGetSnapshotInput
    ) -> ToolEnvelope[USCompositeSnapshotDTO]:
        request_id, effective_as_of = self._begin(request.as_of)
        try:
            instrument = await self._instrument_access.get(
                request.instrument_id, as_of=effective_as_of
            )
            if instrument.market is not Market.US:
                raise DataContractError(
                    "composite snapshot remains US-only",
                    details={
                        "field": "instrument_id",
                        "rule": "composite_us_only",
                        "market": instrument.market.value,
                    },
                )
            lookback = request.lookback_sessions
            quote_coro = self._data_service.get_quote(instrument, effective_as_of)
            bars_coro = self._fetch_technical_bars(
                instrument, as_of=effective_as_of, lookback_sessions=lookback
            )
            context_coro = self._optional_context_result(effective_as_of)

            quote_result, bars_result, context_outcome = await asyncio.gather(
                quote_coro, bars_coro, context_coro
            )

            metas: list[ProviderResultMeta] = []
            router_warnings: list[WarningInfo] = []
            extra_codes: list[str] = []

            if isinstance(quote_result, RouterExecutionResult):
                router_warnings.extend(quote_result.warnings)
                metas.extend(_metas_from_router(quote_result))
            if isinstance(bars_result, RouterExecutionResult):
                router_warnings.extend(bars_result.warnings)
                metas.extend(_metas_from_router(bars_result))

            # Core quote/bars: failure envelope on either typed failure.
            if not quote_result.ok:
                return self._router_failure(
                    request_id,
                    effective_as_of,
                    quote_result,
                    extra_metas=tuple(metas),
                    extra_router_warnings=tuple(router_warnings),
                )
            if not bars_result.ok:
                return self._router_failure(
                    request_id,
                    effective_as_of,
                    bars_result,
                    extra_metas=tuple(metas),
                    extra_router_warnings=tuple(router_warnings),
                )

            quote = quote_result.value
            series = bars_result.value
            if not isinstance(quote, USQuote) or not isinstance(series, USBarSeries):
                raise DataContractError(
                    "composite core values have invalid types",
                    details={"field": "value", "rule": "type"},
                )

            technical: USTechnicalSnapshot | None
            try:
                technical = self._technical_service.build_snapshot(
                    instrument,
                    series=series,
                    as_of=effective_as_of,
                    lookback_sessions=lookback,
                )
            except TradingPartnerError:
                technical = None
                extra_codes.append(_TECHNICAL_UNAVAILABLE.code)
            except Exception:  # noqa: BLE001 — optional component
                technical = None
                extra_codes.append(_TECHNICAL_UNAVAILABLE.code)

            context: USMarketContext | None
            if isinstance(context_outcome, USMarketContextResult):
                context = context_outcome.context
                metas.extend(context_outcome.metas)
                extra_codes.extend(context.warning_codes)
            else:
                context = None
                extra_codes.append(_CONTEXT_UNAVAILABLE.code)

            warnings = _merge_warnings(
                router_warnings=tuple(router_warnings),
                metas=tuple(metas),
                extra_codes=tuple(extra_codes),
            )
            degraded = bool(warnings)
            warning_codes = tuple(w.code for w in warnings)
            composite = USCompositeSnapshot(
                instrument_id=instrument.instrument_id,
                as_of=effective_as_of,
                quote=quote,
                bars=series,
                technical=technical,
                context=context,
                degraded=degraded,
                warning_codes=warning_codes,
            )
            return self._success_envelope(
                request_id,
                effective_as_of,
                data=USCompositeSnapshotDTO.from_domain(composite),
                metas=tuple(metas),
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001 — envelope boundary
            return self._exception_failure(request_id, effective_as_of, exc)

    def _begin(self, as_of: datetime | None) -> tuple[str, datetime]:
        """Sample request_id once; sample effective as_of only when caller omitted it."""
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        effective_as_of = self._clock.now() if as_of is None else as_of
        return request_id, effective_as_of

    async def _fetch_technical_bars(
        self,
        instrument: Instrument,
        *,
        as_of: datetime,
        lookback_sessions: int,
    ) -> RouterExecutionResult[USBarSeries]:
        as_of_ny_date = as_of.astimezone(_NEW_YORK).date()
        start = as_of_ny_date - timedelta(days=lookback_sessions * 2)
        end = as_of_ny_date
        adjustment = (
            AdjustmentMethod.NONE
            if instrument.asset_type is AssetType.FUTURE
            else AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
        )
        return await self._data_service.get_bars(
            instrument,
            start=start,
            end=end,
            interval=USBarInterval.ONE_DAY,
            adjustment=adjustment,
            as_of=as_of,
        )

    async def _optional_context_result(self, as_of: datetime) -> USMarketContextResult | None:
        try:
            return await self._context_service.get_context_result(as_of)
        except Exception:  # noqa: BLE001 — optional composite component
            return None

    def _envelope_from_router(
        self,
        request_id: str,
        effective_as_of: datetime,
        result: RouterExecutionResult[T],
        *,
        dto_factory: Callable[[T], U],
        market: Market = Market.US,
    ) -> ToolEnvelope[U]:
        if not result.ok or result.value is None:
            return self._router_failure(request_id, effective_as_of, result, market=market)

        data = dto_factory(result.value)
        metas = _metas_from_router(result)
        warnings = _merge_warnings(
            router_warnings=result.warnings,
            metas=metas,
            extra_codes=(),
        )
        warnings = _market_warnings(warnings, market=market)
        return self._success_envelope(
            request_id,
            effective_as_of,
            data=data,
            metas=metas,
            warnings=warnings,
            market=market,
        )

    def _envelope_from_context(
        self,
        request_id: str,
        effective_as_of: datetime,
        result: USMarketContextResult,
    ) -> ToolEnvelope[USMarketContextDTO]:
        warnings = _merge_warnings(
            router_warnings=(),
            metas=result.metas,
            extra_codes=result.context.warning_codes,
        )
        return self._success_envelope(
            request_id,
            effective_as_of,
            data=USMarketContextDTO.from_domain(result.context),
            metas=result.metas,
            warnings=warnings,
        )

    def _success_envelope(
        self,
        request_id: str,
        effective_as_of: datetime,
        *,
        data: U,
        metas: tuple[ProviderResultMeta, ...],
        warnings: tuple[WarningInfo, ...],
        market: Market = Market.US,
    ) -> ToolEnvelope[U]:
        sources = _sources_from_metas(metas)
        freshness = _worst_freshness(metas)
        fetched_at = _max_fetched_at(metas)
        if fetched_at is None:
            fetched_at = self._clock.now()
        degraded = bool(warnings)
        return ToolEnvelope.success(
            request_id=request_id,
            market=market,
            as_of=effective_as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            sources=sources,
            data=data,
            degraded=degraded,
            warnings=warnings,
        )

    def _router_failure(
        self,
        request_id: str,
        effective_as_of: datetime,
        result: RouterExecutionResult[object],
        *,
        extra_metas: tuple[ProviderResultMeta, ...] = (),
        extra_router_warnings: tuple[WarningInfo, ...] = (),
        market: Market = Market.US,
    ) -> ToolEnvelope[T]:
        metas = (*extra_metas, *_metas_from_router(result))
        router_warnings = (*extra_router_warnings, *result.warnings)
        warnings = _merge_warnings(
            router_warnings=router_warnings,
            metas=metas,
            extra_codes=(),
        )
        warnings = _market_warnings(warnings, market=market)
        sources = _sources_from_metas(metas)
        freshness = _worst_freshness(metas)
        fetched_at = _max_fetched_at(metas)
        if fetched_at is None:
            fetched_at = self._clock.now()
        error = _error_from_router(result, self._secret_redactor)
        return ToolEnvelope.failure(
            request_id=request_id,
            market=market,
            as_of=effective_as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            sources=sources,
            errors=[error],
            degraded=True,
            warnings=warnings,
            data=None,
        )

    def _exception_failure(
        self,
        request_id: str,
        effective_as_of: datetime,
        exc: BaseException,
    ) -> ToolEnvelope[T]:
        fetched_at = self._clock.now()
        error: ErrorInfo
        if isinstance(exc, TradingPartnerError):
            error = to_error_info(exc, self._secret_redactor)
        else:
            error = to_error_info_from_exception(exc, self._secret_redactor)
        return ToolEnvelope.failure(
            request_id=request_id,
            market=Market.US,
            as_of=effective_as_of,
            fetched_at=fetched_at,
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=[error],
            degraded=True,
            warnings=(),
            data=None,
        )


def _metas_from_router(
    result: RouterExecutionResult[object],
) -> tuple[ProviderResultMeta, ...]:
    if result.meta is None:
        return ()
    return (result.meta,)


def _sources_from_metas(
    metas: tuple[ProviderResultMeta, ...],
) -> tuple[SourceReference, ...]:
    """Dedupe sources while retaining the newest source metadata."""
    order: list[tuple[str, SourceRole]] = []
    best_meta: dict[tuple[str, SourceRole], ProviderResultMeta] = {}
    for meta in metas:
        key = (meta.vendor.value, meta.role)
        if key not in best_meta:
            order.append(key)
            best_meta[key] = meta
        elif meta.fetched_at > best_meta[key].fetched_at:
            best_meta[key] = meta
    return tuple(
        SourceReference(
            name=vendor,
            role=role,
            url=None,
            retrieved_at=best_meta[(vendor, role)].fetched_at,
            data_delay_seconds=best_meta[(vendor, role)].data_delay_seconds,
        )
        for vendor, role in order
    )


def _worst_freshness(metas: tuple[ProviderResultMeta, ...]) -> Freshness:
    if not metas:
        return Freshness.UNKNOWN
    return max(
        (meta.freshness for meta in metas),
        key=lambda f: _FRESHNESS_WORST_ORDER[f],
    )


def _max_fetched_at(metas: tuple[ProviderResultMeta, ...]) -> datetime | None:
    if not metas:
        return None
    return max(meta.fetched_at for meta in metas)


def _synthesized_from_metas(
    metas: tuple[ProviderResultMeta, ...],
) -> tuple[WarningInfo, ...]:
    out: list[WarningInfo] = []
    seen: set[str] = set()

    def _add(warning: WarningInfo) -> None:
        if warning.code not in seen:
            seen.add(warning.code)
            out.append(warning)

    for meta in metas:
        if meta.role is SourceRole.FALLBACK:
            _add(_FALLBACK_WARNING)
        if meta.freshness is Freshness.DELAYED:
            _add(_DELAYED_WARNING)
        elif meta.freshness is Freshness.STALE:
            if meta.session is TradingSession.CLOSED:
                _add(_CLOSED_SESSION_LAST_KNOWN_WARNING)
            else:
                _add(_STALE_WARNING)
        elif meta.freshness is Freshness.UNKNOWN:
            _add(_UNKNOWN_FRESHNESS_WARNING)
        for code in meta.warnings:
            if code not in seen:
                seen.add(code)
                out.append(_warning_from_code(code))
    return tuple(out)


def _warnings_from_codes(codes: tuple[str, ...]) -> tuple[WarningInfo, ...]:
    out: list[WarningInfo] = []
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        out.append(_warning_from_code(code))
    return tuple(out)


def _merge_warnings(
    *,
    router_warnings: tuple[WarningInfo, ...] | list[WarningInfo],
    metas: tuple[ProviderResultMeta, ...],
    extra_codes: tuple[str, ...] | list[str],
) -> tuple[WarningInfo, ...]:
    """Router warnings first, then meta/context codes, then synthesized provenance."""
    merged: list[WarningInfo] = []
    seen: set[str] = set()

    def _add(warning: WarningInfo) -> None:
        if warning.code not in seen:
            seen.add(warning.code)
            merged.append(warning)

    for warning in router_warnings:
        _add(warning)
    for warning in _warnings_from_codes(tuple(extra_codes)):
        _add(warning)
    for warning in _synthesized_from_metas(metas):
        _add(warning)
    return tuple(merged)


def _market_warnings(
    warnings: tuple[WarningInfo, ...], *, market: Market
) -> tuple[WarningInfo, ...]:
    if market is not Market.KR:
        return warnings
    replacements = {
        "FALLBACK_US_SOURCE": ("FALLBACK_KR_SOURCE", "A Korean market fallback source was used."),
        "DELAYED_US_DATA": ("DELAYED_KR_DATA", "Korean market data is delayed."),
        "STALE_US_DATA": ("STALE_KR_DATA", "Korean market data is stale."),
        "UNKNOWN_US_FRESHNESS": (
            "UNKNOWN_KR_FRESHNESS",
            "Korean market data freshness is unknown.",
        ),
    }
    localized: list[WarningInfo] = []
    for warning in warnings:
        replacement = replacements.get(warning.code)
        if replacement is None:
            localized.append(warning)
            continue
        code, message = replacement
        localized.append(WarningInfo(code=code, message=message, details=warning.details))
    return tuple(localized)


def _error_from_router(
    result: RouterExecutionResult[object],
    redactor: SecretRedactor,
) -> ErrorInfo:
    error = result.error
    if isinstance(error, TradingPartnerError):
        return to_error_info(error, redactor)
    if error is not None:
        return to_error_info_from_exception(error, redactor)
    return ErrorInfo(
        code="UNEXPECTED_ERROR",
        message="router failure without typed error",
        retryable=False,
        details={},
    )
