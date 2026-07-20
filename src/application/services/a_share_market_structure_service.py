"""A-share market structure thin product service (Phase 1E E2).

Routes quote, bars, order book, ticks, industry performance, and market board
through ``ProviderRouter`` with exact protocol isinstance narrowing, tool
policies, stable ``a_share.<capability>.v1`` operation names, secret-free
fingerprints, explicit E2 cache codecs, and strict result validators.

E5 will assemble public MCP tools on top of these focused methods. E2 is not
wired into bootstrap/MCP.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from application.dto.a_share import (
    AShareBarDTO,
    AShareMarketStructureSnapshotDTO,
    IndustryPerformanceRowDTO,
    MarketBoardSnapshotDTO,
    OrderBookLevelDTO,
    TradeTickDTO,
)
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
    validate_provenance_tuple,
)
from application.dto.provider_routing import ProviderSuccess, RouterExecutionResult
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import (
    AShareMarketStructureProvider,
    AShareOhlcvProvider,
    AShareQuoteProvider,
)
from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.services.a_share_tool_policies import (
    STRUCTURE_INSTRUMENT_BARS_POLICY,
    STRUCTURE_INSTRUMENT_BOOK_TICKS_POLICY,
    STRUCTURE_MARKET_INDUSTRY_POLICY,
)
from application.services.provider_router import ProviderRouter
from domain.a_share.current_clist_policy import (
    require_current_clist_trade_date,
    resolve_supportable_closed_trade_date,
)
from domain.a_share.enums import AShareComponentType, AShareMarketScope, BarInterval
from domain.a_share.models import (
    AShareBar,
    AShareQuote,
    IndustryPerformanceRow,
    MarketBoardSnapshot,
    OrderBookLevel,
    TradeTick,
    validate_order_book_levels,
)
from domain.common.enums import AdjustmentMethod, AssetType, DataCategory, Market
from domain.common.errors import (
    DataContractError,
    ProviderUnavailableError,
    StaleMarketData,
    TradingPartnerError,
)
from domain.common.time import ensure_utc, require_aware_datetime
from domain.instruments.models import Instrument

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# Stable operation names (§18.4).
OP_QUOTE = "a_share.quote.v1"
OP_BARS = "a_share.bars.v1"
OP_ORDER_BOOK = "a_share.order_book.v1"
OP_TICKS = "a_share.ticks.v1"
OP_INDUSTRY = "a_share.industry_performance.v1"
OP_MARKET_BOARD = "a_share.market_board.v1"

_DEFAULT_FRESHNESS_WINDOW_SECONDS = 900

_STRUCTURE_ORDER = (
    AShareComponentType.BARS,
    AShareComponentType.ORDER_BOOK,
    AShareComponentType.TICKS,
    AShareComponentType.INDUSTRIES,
    AShareComponentType.MARKET_BOARD,
)


@dataclass(frozen=True, slots=True)
class AShareMarketStructureResult:
    ok: bool
    data: AShareMarketStructureSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        validate_provenance_tuple(self.provenance, order=_STRUCTURE_ORDER)
        if type(self.ok) is not bool or not isinstance(self.warnings, tuple):
            raise DataContractError("invalid market-structure result shape")
        if any(not isinstance(item, WarningInfo) for item in self.warnings):
            raise DataContractError("warnings elements must be WarningInfo")
        if self.ok:
            if self.data is None or self.error is not None:
                raise DataContractError("ok=True requires data and no error")
            validate_data_provenance(self.data, self.provenance)
            if tuple(item.component for item in self.provenance) != self.data.included_components:
                raise DataContractError(
                    "successful structure provenance must exactly match included components"
                )
        elif self.data is not None or not isinstance(self.error, TradingPartnerError):
            raise DataContractError("ok=False requires typed error and no data")


def _as_of_utc_z(as_of: datetime) -> str:
    """Canonical UTC Z form for fingerprints (no secrets)."""
    utc = ensure_utc(as_of)
    text = utc.isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _sorted_params(params: dict[str, str]) -> str:
    return ",".join(f"{k}={params[k]}" for k in sorted(params))


def build_a_share_fingerprint(
    operation: str,
    instrument_or_market: str,
    params: dict[str, str],
    as_of: datetime,
) -> str:
    """Canonical secret-free fingerprint body (§18.4).

    Template: ``v1|{operation}|{instrument_id-or-market}|{sorted params}|{as_of_utc_z}``
    """
    return (
        f"v1|{operation}|{instrument_or_market}|"
        f"{_sorted_params(params)}|{_as_of_utc_z(as_of)}"
    )


def _require_exact_date(value: object, *, field: str) -> date:
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date (not datetime)",
            details={"field": field, "rule": "exact_date_type"},
        )
    return value


class AShareMarketStructureService:
    """E2 thin service: each method is one Router-backed capability."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        calendar: AShareTradingCalendar,
        quote_codec: ProviderCacheCodec[AShareQuote],
        bars_codec: ProviderCacheCodec[tuple[AShareBar, ...]],
        order_book_codec: ProviderCacheCodec[tuple[OrderBookLevel, ...]],
        ticks_codec: ProviderCacheCodec[tuple[TradeTick, ...]],
        industry_codec: ProviderCacheCodec[tuple[IndustryPerformanceRow, ...]],
        market_board_codec: ProviderCacheCodec[MarketBoardSnapshot],
        freshness_window_seconds: int = _DEFAULT_FRESHNESS_WINDOW_SECONDS,
    ) -> None:
        if router is None or clock is None:
            raise DataContractError(
                "router and clock are required",
                details={"field": "dependencies", "rule": "required"},
            )
        if calendar is None:
            raise DataContractError(
                "calendar is required",
                details={"field": "calendar", "rule": "required"},
            )
        for attr in ("is_trading_day", "previous_trading_day"):
            if not callable(getattr(calendar, attr, None)):
                raise DataContractError(
                    "calendar must provide is_trading_day and previous_trading_day",
                    details={
                        "field": "calendar",
                        "rule": "protocol",
                        "missing": attr,
                    },
                )
        if (
            not isinstance(freshness_window_seconds, int)
            or isinstance(freshness_window_seconds, bool)
            or freshness_window_seconds < 0
        ):
            raise DataContractError(
                "freshness_window_seconds must be a nonnegative int",
                details={"field": "freshness_window_seconds", "rule": "nonnegative"},
            )
        for name, codec in (
            ("quote_codec", quote_codec),
            ("bars_codec", bars_codec),
            ("order_book_codec", order_book_codec),
            ("ticks_codec", ticks_codec),
            ("industry_codec", industry_codec),
            ("market_board_codec", market_board_codec),
        ):
            if codec is None or not hasattr(codec, "codec_id"):
                raise DataContractError(
                    f"{name} must be a ProviderCacheCodec",
                    details={"field": name, "rule": "required"},
                )
        self._router = router
        self._clock = clock
        self._calendar = calendar
        self._quote_codec = quote_codec
        self._bars_codec = bars_codec
        self._order_book_codec = order_book_codec
        self._ticks_codec = ticks_codec
        self._industry_codec = industry_codec
        self._market_board_codec = market_board_codec
        self._freshness_window_seconds = freshness_window_seconds

    def _require_current_clist_trade_date(
        self, trade_date: date, *, operation: str, now: datetime | None = None
    ) -> date:
        now = self._clock.now() if now is None else now
        require_aware_datetime(now, field_name="clock.now")
        return require_current_clist_trade_date(
            trade_date=trade_date,
            now=now,
            is_trading_day=self._calendar.is_trading_day,
            previous_trading_day=self._calendar.previous_trading_day,
            operation=operation,
        )

    def _reject_stale_as_of(
        self, as_of: datetime, *, operation: str, now: datetime | None = None
    ) -> None:
        now = self._clock.now() if now is None else now
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future", "operation": operation},
            )
        age = (now - as_of).total_seconds()
        if age > self._freshness_window_seconds:
            raise StaleMarketData(
                "as_of is outside the supported freshness window for live structure data",
                details={
                    "operation": operation,
                    "rule": "freshness_window",
                    "window_seconds": self._freshness_window_seconds,
                },
            )

    def _require_trade_date_not_after_as_of_local(
        self, trade_date: date, as_of: datetime, *, operation: str
    ) -> None:
        """Reject trade_date later than Asia/Shanghai local date of as_of."""
        as_of_local_day = as_of.astimezone(_SHANGHAI).date()
        if trade_date > as_of_local_day:
            raise DataContractError(
                "trade_date must not be later than the Asia/Shanghai local date of as_of",
                details={
                    "field": "trade_date",
                    "rule": "trade_date_not_after_as_of_local",
                    "operation": operation,
                },
            )

    def _require_a_share(self, instrument: Instrument) -> None:
        if not isinstance(instrument, Instrument):
            raise DataContractError(
                "instrument must be Instrument",
                details={"field": "instrument", "rule": "type"},
            )
        if instrument.market is not Market.A_SHARE:
            raise DataContractError(
                "instrument market must be A_SHARE",
                details={"field": "instrument", "rule": "market"},
            )

    # --- validators -----------------------------------------------------------

    def _validate_quote(
        self,
        success: ProviderSuccess[AShareQuote],
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
        if not isinstance(success.value, AShareQuote):
            raise DataContractError(
                "success.value must be AShareQuote",
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

    def _validate_bars(
        self,
        success: ProviderSuccess[tuple[AShareBar, ...]],
        *,
        start: date,
        end: date,
        interval: BarInterval,
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
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of AShareBar",
                details={"field": "value", "rule": "type"},
            )
        prev_start: datetime | None = None
        for idx, bar in enumerate(success.value):
            if not isinstance(bar, AShareBar):
                raise DataContractError(
                    "bars elements must be AShareBar",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if bar.interval is not interval:
                raise DataContractError(
                    "bar interval must match request",
                    details={"field": "interval", "index": idx, "rule": "interval"},
                )
            if bar.adjustment is not adjustment:
                raise DataContractError(
                    "bar adjustment must match request",
                    details={"field": "adjustment", "index": idx, "rule": "adjustment"},
                )
            if bar.end_at > as_of:
                raise DataContractError(
                    "bar end_at must be <= as_of",
                    details={"field": "end_at", "index": idx, "rule": "as_of_cutoff"},
                )
            # Always evaluate the bar's calendar day in Asia/Shanghai.
            local_day = bar.end_at.astimezone(_SHANGHAI).date()
            if local_day < start or local_day > end:
                raise DataContractError(
                    "bar end day must be within requested range",
                    details={"field": "end_at", "index": idx, "rule": "range"},
                )
            if prev_start is not None and bar.start_at <= prev_start:
                raise DataContractError(
                    "bars must be sorted by unique start_at ascending",
                    details={"field": "start_at", "index": idx, "rule": "sorted_unique"},
                )
            prev_start = bar.start_at

    def _validate_order_book(
        self, success: ProviderSuccess[tuple[OrderBookLevel, ...]]
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.MARKET_STRUCTURE:
            raise DataContractError(
                "order book meta.category must be MARKET_STRUCTURE",
                details={"field": "meta.category", "rule": "category"},
            )
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of OrderBookLevel",
                details={"field": "value", "rule": "type"},
            )
        validate_order_book_levels(success.value)

    def _validate_ticks(
        self,
        success: ProviderSuccess[tuple[TradeTick, ...]],
        *,
        as_of: datetime,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.MARKET_STRUCTURE:
            raise DataContractError(
                "ticks meta.category must be MARKET_STRUCTURE",
                details={"field": "meta.category", "rule": "category"},
            )
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of TradeTick",
                details={"field": "value", "rule": "type"},
            )
        for idx, tick in enumerate(success.value):
            if not isinstance(tick, TradeTick):
                raise DataContractError(
                    "ticks elements must be TradeTick",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if tick.occurred_at > as_of:
                raise DataContractError(
                    "tick occurred_at must be <= as_of",
                    details={"field": "occurred_at", "index": idx, "rule": "as_of_cutoff"},
                )

    def _validate_industry(
        self,
        success: ProviderSuccess[tuple[IndustryPerformanceRow, ...]],
        *,
        trade_date: date,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.MARKET_STRUCTURE:
            raise DataContractError(
                "industry meta.category must be MARKET_STRUCTURE",
                details={"field": "meta.category", "rule": "category"},
            )
        if not isinstance(success.value, tuple):
            raise DataContractError(
                "success.value must be a tuple of IndustryPerformanceRow",
                details={"field": "value", "rule": "type"},
            )
        seen: set[str] = set()
        for idx, row in enumerate(success.value):
            if not isinstance(row, IndustryPerformanceRow):
                raise DataContractError(
                    "industry rows must be IndustryPerformanceRow",
                    details={"field": "value", "index": idx, "rule": "type"},
                )
            if row.trade_date != trade_date:
                raise DataContractError(
                    "industry trade_date must match request",
                    details={"field": "trade_date", "index": idx, "rule": "trade_date"},
                )
            if row.industry_code in seen:
                raise DataContractError(
                    "industry_code must be unique",
                    details={
                        "field": "industry_code",
                        "index": idx,
                        "rule": "unique",
                    },
                )
            seen.add(row.industry_code)

    def _validate_market_board(
        self,
        success: ProviderSuccess[MarketBoardSnapshot],
        *,
        trade_date: date,
    ) -> None:
        if not isinstance(success, ProviderSuccess):
            raise DataContractError(
                "provider call must return ProviderSuccess",
                details={"field": "result", "rule": "type"},
            )
        if success.meta.category is not DataCategory.MARKET_STRUCTURE:
            raise DataContractError(
                "market board meta.category must be MARKET_STRUCTURE",
                details={"field": "meta.category", "rule": "category"},
            )
        if not isinstance(success.value, MarketBoardSnapshot):
            raise DataContractError(
                "success.value must be MarketBoardSnapshot",
                details={
                    "field": "value",
                    "rule": "type",
                    "type": type(success.value).__name__,
                },
            )
        if success.value.trade_date != trade_date:
            raise DataContractError(
                "market board trade_date must match request",
                details={"field": "trade_date", "rule": "trade_date"},
            )

    # --- public capability methods --------------------------------------------

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> RouterExecutionResult[AShareQuote]:
        require_aware_datetime(as_of, field_name="as_of")
        self._require_a_share(instrument)
        self._reject_stale_as_of(as_of, operation=OP_QUOTE)
        fingerprint = build_a_share_fingerprint(
            OP_QUOTE, instrument.instrument_id, {}, as_of
        )

        async def _call(adapter: CategoryProvider) -> ProviderSuccess[AShareQuote]:
            if not isinstance(adapter, AShareQuoteProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.MARKET_QUOTE.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_quote(instrument, as_of)

        def _validator(success: ProviderSuccess[AShareQuote]) -> None:
            self._validate_quote(success, instrument=instrument, as_of=as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
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

    async def get(
        self,
        *,
        scope: AShareMarketScope,
        instrument: Instrument | None,
        trade_date: date | None,
        start: date | None,
        end: date | None,
        interval: BarInterval,
        adjustment: AdjustmentMethod,
        include_bars: bool,
        include_order_book: bool,
        include_ticks: bool,
        include_industries: bool,
        include_market_board: bool,
        industry_limit: int,
        tick_limit: int,
        as_of: datetime,
    ) -> AShareMarketStructureResult:
        """Compose the required market-structure components deterministically."""
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError("as_of must not be in the future relative to clock")
        if not isinstance(scope, AShareMarketScope):
            raise DataContractError("scope must be AShareMarketScope")
        flags = (
            include_bars,
            include_order_book,
            include_ticks,
            include_industries,
            include_market_board,
        )
        if any(type(flag) is not bool for flag in flags) or not any(flags):
            raise DataContractError("at least one exact-bool structure component is required")
        if scope is AShareMarketScope.INSTRUMENT:
            if instrument is None:
                raise DataContractError("instrument is required for instrument scope")
            self._require_a_share(instrument)
            if instrument.asset_type not in {
                AssetType.EQUITY,
                AssetType.ETF,
                AssetType.INDEX,
            }:
                raise DataContractError("unsupported instrument asset type")
            if include_bars and (start is None or end is None):
                raise DataContractError("start and end are required for bars")
        else:
            if instrument is not None:
                raise DataContractError("instrument is forbidden outside instrument scope")
            if include_bars or include_order_book or include_ticks:
                raise DataContractError("instrument components require instrument scope")
            if scope is AShareMarketScope.INDUSTRY and not include_industries:
                raise DataContractError("industry scope requires industries")
            if scope is AShareMarketScope.MARKET and not include_market_board:
                raise DataContractError("market scope requires market_board")

        included = tuple(
            component
            for component, enabled in zip(_STRUCTURE_ORDER, flags, strict=True)
            if enabled
        )
        effective_trade_date = trade_date
        if include_industries or include_market_board:
            if effective_trade_date is None:
                effective_trade_date = resolve_supportable_closed_trade_date(
                    now=as_of,
                    is_trading_day=self._calendar.is_trading_day,
                    previous_trading_day=self._calendar.previous_trading_day,
                )
            else:
                effective_trade_date = _require_exact_date(
                    effective_trade_date, field="trade_date"
                )

        async def settle(
            awaitable: Awaitable[Any],
        ) -> tuple[Any | None, TradingPartnerError | None]:
            try:
                return await awaitable, None
            except TradingPartnerError as exc:
                return None, exc
            except Exception:
                return (
                    None,
                    ProviderUnavailableError(
                        "Unexpected A-share component failure",
                        details={"error_type": "unexpected_component_failure"},
                    ),
                )

        tasks: dict[
            AShareComponentType,
            asyncio.Task[tuple[Any | None, TradingPartnerError | None]],
        ] = {}
        assert not include_bars or (
            instrument is not None and start is not None and end is not None
        )
        assert not (include_order_book or include_ticks) or instrument is not None
        assert not (include_industries or include_market_board) or effective_trade_date is not None
        required_instrument = cast(Instrument, instrument)
        required_start = cast(date, start)
        required_end = cast(date, end)
        required_trade_date = cast(date, effective_trade_date)
        async with asyncio.TaskGroup() as tg:
            if include_bars:
                tasks[AShareComponentType.BARS] = tg.create_task(
                    settle(
                        self._get_bars(
                            required_instrument,
                            start=required_start,
                            end=required_end,
                            interval=interval,
                            adjustment=adjustment,
                            as_of=as_of,
                            sampled_now=now,
                        )
                    )
                )
            if include_order_book:
                tasks[AShareComponentType.ORDER_BOOK] = tg.create_task(
                    settle(
                        self._get_order_book(
                            required_instrument, as_of, sampled_now=now
                        )
                    )
                )
            if include_ticks:
                tasks[AShareComponentType.TICKS] = tg.create_task(
                    settle(
                        self._get_ticks(
                            required_instrument,
                            limit=tick_limit,
                            as_of=as_of,
                            sampled_now=now,
                        )
                    )
                )
            if include_industries:
                tasks[AShareComponentType.INDUSTRIES] = tg.create_task(
                    settle(
                        self._get_industry_performance(
                            trade_date=required_trade_date,
                            limit=industry_limit,
                            as_of=as_of,
                            sampled_now=now,
                        )
                    )
                )
            if include_market_board:
                tasks[AShareComponentType.MARKET_BOARD] = tg.create_task(
                    settle(
                        self._get_market_board(
                            trade_date=required_trade_date,
                            as_of=as_of,
                            sampled_now=now,
                        )
                    )
                )

        results: dict[AShareComponentType, RouterExecutionResult[Any]] = {}
        failures: dict[AShareComponentType, TradingPartnerError] = {}
        warnings: list[WarningInfo] = []
        provenance_items: list[AShareComponentProvenance] = []
        for component in included:
            result, exception = tasks[component].result()
            if exception is not None:
                failures[component] = exception
                continue
            if not isinstance(result, RouterExecutionResult):
                failures[component] = ProviderUnavailableError(
                    "Unexpected A-share component failure",
                    details={"error_type": "unexpected_component_failure"},
                )
                continue
            results[component] = result
            for warning in result.warnings:
                if warning not in warnings:
                    warnings.append(warning)
            if not result.ok or result.value is None:
                failures[component] = result.error or DataContractError(
                    "required market-structure component failed",
                    details={"component": component.value, "rule": "required"},
                )
            elif result.meta is not None:
                provenance_items.append(
                    component_provenance(component, result.meta, result.value)
                )
        provenance = tuple(provenance_items)
        if failures:
            first = next(component for component in included if component in failures)
            return AShareMarketStructureResult(
                ok=False,
                data=None,
                warnings=tuple(warnings),
                error=failures[first],
                provenance=provenance,
            )

        bars = results.get(AShareComponentType.BARS)
        book = results.get(AShareComponentType.ORDER_BOOK)
        ticks = results.get(AShareComponentType.TICKS)
        industries = results.get(AShareComponentType.INDUSTRIES)
        board = results.get(AShareComponentType.MARKET_BOARD)
        dto = AShareMarketStructureSnapshotDTO(
            scope=scope,
            instrument_id=instrument.instrument_id if instrument is not None else None,
            trade_date=(
                effective_trade_date
                if (include_industries or include_market_board)
                else trade_date
            ),
            as_of=as_of,
            included_components=included,
            bars=tuple(
                AShareBarDTO.from_domain(item)
                for item in cast(tuple[AShareBar, ...], bars.value if bars else ())
            ),
            order_book=tuple(
                OrderBookLevelDTO.from_domain(item)
                for item in cast(
                    tuple[OrderBookLevel, ...], book.value if book else ()
                )
            ),
            ticks=tuple(
                TradeTickDTO.from_domain(item)
                for item in cast(tuple[TradeTick, ...], ticks.value if ticks else ())
            ),
            industries=tuple(
                IndustryPerformanceRowDTO.from_domain(item)
                for item in cast(
                    tuple[IndustryPerformanceRow, ...],
                    industries.value if industries else (),
                )
            ),
            market_board=(
                MarketBoardSnapshotDTO.from_domain(
                    cast(MarketBoardSnapshot, board.value)
                )
                if board is not None
                else None
            ),
            provenance=provenance_dtos(provenance),
        )
        return AShareMarketStructureResult(
            ok=True,
            data=dto,
            warnings=tuple(warnings),
            error=None,
            provenance=provenance,
        )

    async def get_bars(
        self, instrument: Instrument, *, start: date, end: date, interval: BarInterval,
        adjustment: AdjustmentMethod, as_of: datetime,
    ) -> RouterExecutionResult[tuple[AShareBar, ...]]:
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        return await self._get_bars(
            instrument, start=start, end=end, interval=interval, adjustment=adjustment,
            as_of=as_of, sampled_now=now,
        )

    async def _get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: BarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
        sampled_now: datetime,
    ) -> RouterExecutionResult[tuple[AShareBar, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        self._require_a_share(instrument)
        start = _require_exact_date(start, field="start")
        end = _require_exact_date(end, field="end")
        require_aware_datetime(sampled_now, field_name="clock.now")
        if as_of > sampled_now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if not isinstance(interval, BarInterval):
            raise DataContractError(
                "interval must be BarInterval",
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
        fingerprint = build_a_share_fingerprint(
            OP_BARS, instrument.instrument_id, params, as_of
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[AShareBar, ...]]:
            if not isinstance(adapter, AShareOhlcvProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
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

        def _validator(success: ProviderSuccess[tuple[AShareBar, ...]]) -> None:
            self._validate_bars(
                success,
                start=start,
                end=end,
                interval=interval,
                adjustment=adjustment,
                as_of=as_of,
            )

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.MARKET_OHLCV,
            call=_call,
            operation_name=OP_BARS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=STRUCTURE_INSTRUMENT_BARS_POLICY,
            bypass_cache=False,
            cache_codec=self._bars_codec,
            result_validator=_validator,
        )

    async def get_order_book(
        self, instrument: Instrument, as_of: datetime,
    ) -> RouterExecutionResult[tuple[OrderBookLevel, ...]]:
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        return await self._get_order_book(instrument, as_of, sampled_now=now)

    async def _get_order_book(
        self,
        instrument: Instrument,
        as_of: datetime,
        *,
        sampled_now: datetime,
    ) -> RouterExecutionResult[tuple[OrderBookLevel, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        self._require_a_share(instrument)
        self._reject_stale_as_of(
            as_of, operation=OP_ORDER_BOOK, now=sampled_now
        )
        fingerprint = build_a_share_fingerprint(
            OP_ORDER_BOOK, instrument.instrument_id, {}, as_of
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[OrderBookLevel, ...]]:
            if not isinstance(adapter, AShareMarketStructureProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.MARKET_STRUCTURE.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_order_book(instrument, as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.MARKET_STRUCTURE,
            call=_call,
            operation_name=OP_ORDER_BOOK,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=STRUCTURE_INSTRUMENT_BOOK_TICKS_POLICY,
            bypass_cache=False,
            cache_codec=self._order_book_codec,
            result_validator=self._validate_order_book,
        )

    async def get_ticks(
        self, instrument: Instrument, *, limit: int, as_of: datetime,
    ) -> RouterExecutionResult[tuple[TradeTick, ...]]:
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        return await self._get_ticks(instrument, limit=limit, as_of=as_of, sampled_now=now)

    async def _get_ticks(
        self,
        instrument: Instrument,
        *,
        limit: int,
        as_of: datetime,
        sampled_now: datetime,
    ) -> RouterExecutionResult[tuple[TradeTick, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        self._require_a_share(instrument)
        self._reject_stale_as_of(as_of, operation=OP_TICKS, now=sampled_now)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        params = {"limit": str(limit)}
        fingerprint = build_a_share_fingerprint(
            OP_TICKS, instrument.instrument_id, params, as_of
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[TradeTick, ...]]:
            if not isinstance(adapter, AShareMarketStructureProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.MARKET_STRUCTURE.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_ticks(instrument, limit=limit, as_of=as_of)

        def _validator(success: ProviderSuccess[tuple[TradeTick, ...]]) -> None:
            self._validate_ticks(success, as_of=as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.MARKET_STRUCTURE,
            call=_call,
            operation_name=OP_TICKS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=STRUCTURE_INSTRUMENT_BOOK_TICKS_POLICY,
            bypass_cache=False,
            cache_codec=self._ticks_codec,
            result_validator=_validator,
        )

    async def get_industry_performance(
        self, *, trade_date: date, limit: int, as_of: datetime,
    ) -> RouterExecutionResult[tuple[IndustryPerformanceRow, ...]]:
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        return await self._get_industry_performance(
            trade_date=trade_date, limit=limit, as_of=as_of, sampled_now=now,
        )

    async def _get_industry_performance(
        self,
        *,
        trade_date: date,
        limit: int,
        as_of: datetime,
        sampled_now: datetime,
    ) -> RouterExecutionResult[tuple[IndustryPerformanceRow, ...]]:
        require_aware_datetime(as_of, field_name="as_of")
        trade_date = _require_exact_date(trade_date, field="trade_date")
        # Live freshness before trade-date resolution / Router (same as quote/book/ticks).
        self._reject_stale_as_of(as_of, operation=OP_INDUSTRY, now=sampled_now)
        self._require_trade_date_not_after_as_of_local(
            trade_date, as_of, operation=OP_INDUSTRY
        )
        # Preflight before Router/network: current-only cross-section.
        self._require_current_clist_trade_date(
            trade_date, operation=OP_INDUSTRY, now=sampled_now
        )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise DataContractError(
                "limit must be a positive int",
                details={"field": "limit", "rule": "positive"},
            )
        params = {"limit": str(limit), "trade_date": trade_date.isoformat()}
        fingerprint = build_a_share_fingerprint(OP_INDUSTRY, "market", params, as_of)

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[IndustryPerformanceRow, ...]]:
            if not isinstance(adapter, AShareMarketStructureProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.MARKET_STRUCTURE.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_industry_performance(
                trade_date=trade_date, limit=limit, as_of=as_of
            )

        def _validator(
            success: ProviderSuccess[tuple[IndustryPerformanceRow, ...]],
        ) -> None:
            self._validate_industry(success, trade_date=trade_date)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.MARKET_STRUCTURE,
            call=_call,
            operation_name=OP_INDUSTRY,
            request_fingerprint=fingerprint,
            instrument=None,
            as_of=as_of,
            tool_policy=STRUCTURE_MARKET_INDUSTRY_POLICY,
            bypass_cache=False,
            cache_codec=self._industry_codec,
            result_validator=_validator,
        )

    async def get_market_board(
        self, *, trade_date: date, as_of: datetime,
    ) -> RouterExecutionResult[MarketBoardSnapshot]:
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        return await self._get_market_board(trade_date=trade_date, as_of=as_of, sampled_now=now)

    async def _get_market_board(
        self,
        *,
        trade_date: date,
        as_of: datetime,
        sampled_now: datetime,
    ) -> RouterExecutionResult[MarketBoardSnapshot]:
        require_aware_datetime(as_of, field_name="as_of")
        trade_date = _require_exact_date(trade_date, field="trade_date")
        # Live freshness before trade-date resolution / Router (same as quote/book/ticks).
        self._reject_stale_as_of(
            as_of, operation=OP_MARKET_BOARD, now=sampled_now
        )
        self._require_trade_date_not_after_as_of_local(
            trade_date, as_of, operation=OP_MARKET_BOARD
        )
        # Preflight before Router/network: current-only cross-section.
        self._require_current_clist_trade_date(
            trade_date, operation=OP_MARKET_BOARD, now=sampled_now
        )
        params = {"trade_date": trade_date.isoformat()}
        fingerprint = build_a_share_fingerprint(
            OP_MARKET_BOARD, "market", params, as_of
        )

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[MarketBoardSnapshot]:
            if not isinstance(adapter, AShareMarketStructureProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.MARKET_STRUCTURE.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_market_board(trade_date=trade_date, as_of=as_of)

        def _validator(success: ProviderSuccess[MarketBoardSnapshot]) -> None:
            self._validate_market_board(success, trade_date=trade_date)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.MARKET_STRUCTURE,
            call=_call,
            operation_name=OP_MARKET_BOARD,
            request_fingerprint=fingerprint,
            instrument=None,
            as_of=as_of,
            tool_policy=STRUCTURE_MARKET_INDUSTRY_POLICY,
            bypass_cache=False,
            cache_codec=self._market_board_codec,
            result_validator=_validator,
        )
