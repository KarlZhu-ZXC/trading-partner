"""A-share capital product aggregation service (Phase 1E E4a).

``AShareCapitalService.get`` fans out one ``ProviderRouter.execute`` per
requested capital metric with the eight fine-grained runtime-checkable
protocols, corporate-actions filtering for unlock/dividend, fixed chain
overrides, ``asyncio.TaskGroup`` structured concurrency, and deterministic
merge in caller/default metric order.

The service is bootstrapped behind ``a_share_get_facts(operation="capital")``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from application.dto.a_share import (
    AShareCapitalSnapshotDTO,
    BlockTradeRecordDTO,
    ChipDistributionSnapshotDTO,
    DividendRecordDTO,
    DragonTigerRecordDTO,
    FundFlowPointDTO,
    MarginRecordDTO,
    NorthboundFlowPointDTO,
    ShareholderCountRecordDTO,
    UnlockRecordDTO,
)
from application.dto.a_share_provenance import (
    AShareComponentProvenance,
    component_provenance,
    provenance_dtos,
    validate_data_provenance,
    validate_provenance_tuple,
)
from application.dto.provider_routing import (
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.a_share_providers import (
    AShareBlockTradeProvider,
    AShareChipProvider,
    AShareDailyFlowProvider,
    AShareDisclosureProvider,
    AShareDragonTigerProvider,
    AShareIntradayFlowProvider,
    AShareMarginProvider,
    AShareNorthboundProvider,
    AShareShareholderProvider,
)
from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.services.a_share_capital_validation import (
    AShareCapitalValidationMixin,
)
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import (
    A_SHARE_TOOL_ASSET_SUPPORT,
    CAPITAL_DEFAULT_REQUIRED_METRICS,
    CAPITAL_DEFAULT_SUMMARY_METRICS,
    CAPITAL_METRIC_CATEGORY,
    capital_metric_router_policy,
)
from application.services.component_settlement import settle_router_component
from application.services.provider_router import ProviderRouter
from domain.a_share.enums import AShareComponentType, BarInterval, CapitalMetricType
from domain.a_share.models import (
    BlockTradeRecord,
    ChipDistributionSnapshot,
    DividendRecord,
    DragonTigerRecord,
    FundFlowPoint,
    MarginRecord,
    NorthboundFlowPoint,
    ShareholderCountRecord,
    UnlockRecord,
)
from domain.common.enums import (
    AssetType,
    DataCategory,
    Market,
    ReliabilityLevel,
)
from domain.common.errors import DataContractError, TradingPartnerError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DEFAULT_CURRENT_WINDOW_SECONDS = 300
_ESTABLISHED_META_WARNING_CODES = frozenset(
    {
        "PUBLICATION_TIME_UNKNOWN_EXCLUDED",
        "NORTHBOUND_DISCLOSURE_INCOMPLETE",
        "LOW_RELIABILITY_MARKET_SIGNAL",
        "DERIVED_CHIP_DISTRIBUTION",
    }
)
_META_WARNING_MESSAGES: dict[str, str] = {
    "PUBLICATION_TIME_UNKNOWN_EXCLUDED": ("Records with unknown publication time excluded"),
    "NORTHBOUND_DISCLOSURE_INCOMPLETE": (
        "Northbound disclosure is incomplete or non-authoritative"
    ),
    "LOW_RELIABILITY_MARKET_SIGNAL": "Capital metric carries low reliability",
    "DERIVED_CHIP_DISTRIBUTION": "Chip distribution is a derived turnover-decay estimate",
}

# Stable operation names (§18.4).
OP_INTRADAY_FLOW = "a_share.intraday_flow.v1"
OP_DAILY_FLOW = "a_share.daily_flow.v1"
OP_NORTHBOUND = "a_share.northbound.v1"
OP_DRAGON_TIGER = "a_share.dragon_tiger.v1"
OP_MARGIN = "a_share.margin.v1"
OP_BLOCK_TRADES = "a_share.block_trades.v1"
OP_SHAREHOLDER_COUNTS = "a_share.shareholder_counts.v1"
OP_CHIP_DISTRIBUTION = "a_share.chip_distribution.v2"
OP_CORPORATE_ACTIONS = "a_share.corporate_actions.v1"

_DEFAULT_MARGIN_LIMIT = 40
_DEFAULT_BLOCK_TRADE_LIMIT = 40
_CAPITAL_COMPONENT_ORDER = tuple(AShareComponentType(metric.value) for metric in CapitalMetricType)

_DEFAULT_SHAREHOLDER_LIMIT = 40

_OP_BY_METRIC: dict[CapitalMetricType, str] = {
    CapitalMetricType.INTRADAY_FLOW: OP_INTRADAY_FLOW,
    CapitalMetricType.DAILY_FLOW: OP_DAILY_FLOW,
    CapitalMetricType.NORTHBOUND: OP_NORTHBOUND,
    CapitalMetricType.DRAGON_TIGER: OP_DRAGON_TIGER,
    CapitalMetricType.MARGIN: OP_MARGIN,
    CapitalMetricType.BLOCK_TRADE: OP_BLOCK_TRADES,
    CapitalMetricType.SHAREHOLDER_COUNT: OP_SHAREHOLDER_COUNTS,
    CapitalMetricType.CHIP_DISTRIBUTION: OP_CHIP_DISTRIBUTION,
    CapitalMetricType.UNLOCK: OP_CORPORATE_ACTIONS,
    CapitalMetricType.DIVIDEND: OP_CORPORATE_ACTIONS,
}


def _require_exact_date(value: object, *, field: str) -> date:
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date (not datetime)",
            details={"field": field, "rule": "exact_date_type"},
        )
    return value


def _require_positive_limit(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DataContractError(
            f"{field} must be a positive int",
            details={"field": field, "rule": "positive"},
        )
    return value


def _require_nonnegative_window(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DataContractError(
            f"{field} must be a nonnegative exact int",
            details={"field": field, "rule": "nonnegative"},
        )
    return value


@dataclass(frozen=True, slots=True)
class AShareCapitalResult:
    """Typed result wrapper for capital product aggregation."""

    ok: bool
    data: AShareCapitalSnapshotDTO | None
    warnings: tuple[WarningInfo, ...]
    error: TradingPartnerError | None
    provenance: tuple[AShareComponentProvenance, ...]

    def __post_init__(self) -> None:
        validate_provenance_tuple(self.provenance)
        if type(self.ok) is not bool:
            raise DataContractError(
                "ok must be exact bool",
                details={"field": "ok", "rule": "type", "type": type(self.ok).__name__},
            )
        if not isinstance(self.warnings, tuple):
            raise DataContractError(
                "warnings must be a tuple of WarningInfo",
                details={
                    "field": "warnings",
                    "rule": "type",
                    "type": type(self.warnings).__name__,
                },
            )
        for idx, warning in enumerate(self.warnings):
            if not isinstance(warning, WarningInfo):
                raise DataContractError(
                    "warnings elements must be WarningInfo",
                    details={"field": "warnings", "index": idx, "rule": "type"},
                )
        if self.ok:
            if self.data is None:
                raise DataContractError(
                    "AShareCapitalResult ok=True requires data non-None",
                    details={"field": "data", "rule": "ok_true_data_required"},
                )
            if self.error is not None:
                raise DataContractError(
                    "AShareCapitalResult ok=True requires error is None",
                    details={"field": "error", "rule": "ok_true_error_none"},
                )
            validate_data_provenance(self.data, self.provenance)
            if not self.provenance:
                raise DataContractError("successful capital result requires provenance")
            metric_components = tuple(
                AShareComponentType(metric.value) for metric in self.data.metrics
            )
            actual = tuple(item.component for item in self.provenance)
            if any(component not in metric_components for component in actual):
                raise DataContractError("capital provenance component was not requested")
            if tuple(sorted(actual, key=metric_components.index)) != actual:
                raise DataContractError("capital provenance order must follow metrics")
        else:
            if any(item.component not in _CAPITAL_COMPONENT_ORDER for item in self.provenance):
                raise DataContractError("capital failure provenance contains unrelated component")
            if self.data is not None:
                raise DataContractError(
                    "AShareCapitalResult ok=False requires data is None",
                    details={"field": "data", "rule": "ok_false_data_none"},
                )
            if self.error is None or not isinstance(self.error, TradingPartnerError):
                raise DataContractError(
                    "AShareCapitalResult ok=False requires typed TradingPartnerError",
                    details={
                        "field": "error",
                        "rule": "ok_false_error_required",
                        "type": type(self.error).__name__,
                    },
                )


class AShareCapitalService(AShareCapitalValidationMixin):
    """E4a product service: capital metrics via ProviderRouter per metric."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        clock: Clock,
        calendar: AShareTradingCalendar,
        intraday_flow_codec: ProviderCacheCodec[tuple[FundFlowPoint, ...]],
        daily_flow_codec: ProviderCacheCodec[tuple[FundFlowPoint, ...]],
        northbound_codec: ProviderCacheCodec[tuple[NorthboundFlowPoint, ...]],
        dragon_tiger_codec: ProviderCacheCodec[tuple[DragonTigerRecord, ...]],
        margin_codec: ProviderCacheCodec[tuple[MarginRecord, ...]],
        block_trades_codec: ProviderCacheCodec[tuple[BlockTradeRecord, ...]],
        shareholder_counts_codec: ProviderCacheCodec[tuple[ShareholderCountRecord, ...]],
        chip_distribution_codec: ProviderCacheCodec[ChipDistributionSnapshot],
        corporate_actions_codec: ProviderCacheCodec[tuple[UnlockRecord | DividendRecord, ...]],
        margin_limit: int = _DEFAULT_MARGIN_LIMIT,
        block_trade_limit: int = _DEFAULT_BLOCK_TRADE_LIMIT,
        shareholder_limit: int = _DEFAULT_SHAREHOLDER_LIMIT,
        current_window_seconds: int = _DEFAULT_CURRENT_WINDOW_SECONDS,
    ) -> None:
        if router is None or clock is None or calendar is None:
            raise DataContractError(
                "router, clock, and calendar are required",
                details={"field": "dependencies", "rule": "required"},
            )
        for attr in ("is_trading_day", "previous_trading_day", "sessions_for"):
            if not callable(getattr(calendar, attr, None)):
                raise DataContractError(
                    "calendar must implement AShareTradingCalendar",
                    details={"field": "calendar", "rule": "protocol", "missing": attr},
                )
        calendar_version = getattr(calendar, "version", None)
        if not isinstance(calendar_version, str) or not calendar_version.strip():
            raise DataContractError(
                "calendar must expose a nonblank version",
                details={"field": "calendar.version", "rule": "protocol"},
            )
        for name, codec in (
            ("intraday_flow_codec", intraday_flow_codec),
            ("daily_flow_codec", daily_flow_codec),
            ("northbound_codec", northbound_codec),
            ("dragon_tiger_codec", dragon_tiger_codec),
            ("margin_codec", margin_codec),
            ("block_trades_codec", block_trades_codec),
            ("shareholder_counts_codec", shareholder_counts_codec),
            ("chip_distribution_codec", chip_distribution_codec),
            ("corporate_actions_codec", corporate_actions_codec),
        ):
            if codec is None or not hasattr(codec, "codec_id"):
                raise DataContractError(
                    f"{name} must be a ProviderCacheCodec",
                    details={"field": name, "rule": "required"},
                )
        self._router = router
        self._clock = clock
        self._calendar = calendar
        self._intraday_flow_codec = intraday_flow_codec
        self._daily_flow_codec = daily_flow_codec
        self._northbound_codec = northbound_codec
        self._dragon_tiger_codec = dragon_tiger_codec
        self._margin_codec = margin_codec
        self._block_trades_codec = block_trades_codec
        self._shareholder_counts_codec = shareholder_counts_codec
        self._chip_distribution_codec = chip_distribution_codec
        self._corporate_actions_codec = corporate_actions_codec
        self._margin_limit = _require_positive_limit(margin_limit, field="margin_limit")
        self._block_trade_limit = _require_positive_limit(
            block_trade_limit, field="block_trade_limit"
        )
        self._shareholder_limit = _require_positive_limit(
            shareholder_limit, field="shareholder_limit"
        )
        self._current_window_seconds = _require_nonnegative_window(
            current_window_seconds, field="current_window_seconds"
        )

    def _resolve_metrics(
        self, metrics: tuple[CapitalMetricType, ...]
    ) -> tuple[tuple[CapitalMetricType, ...], frozenset[CapitalMetricType], bool]:
        """Return (ordered metrics, required set, explicit_caller)."""
        if not isinstance(metrics, tuple):
            raise DataContractError(
                "metrics must be a tuple of CapitalMetricType",
                details={"field": "metrics", "rule": "type"},
            )
        if not metrics:
            return (
                CAPITAL_DEFAULT_SUMMARY_METRICS,
                CAPITAL_DEFAULT_REQUIRED_METRICS,
                False,
            )
        seen: set[CapitalMetricType] = set()
        ordered: list[CapitalMetricType] = []
        for metric in metrics:
            if not isinstance(metric, CapitalMetricType):
                raise DataContractError(
                    "metrics elements must be CapitalMetricType",
                    details={"field": "metrics", "rule": "type"},
                )
            if metric in seen:
                raise DataContractError(
                    "metrics must not contain duplicates",
                    details={"field": "metrics", "rule": "unique"},
                )
            seen.add(metric)
            ordered.append(metric)
        # Explicit metrics preserve caller order; all become required (§18.5).
        return tuple(ordered), frozenset(ordered), True

    def _require_instrument_rules(
        self,
        instrument: Instrument | None,
        metrics: tuple[CapitalMetricType, ...],
    ) -> None:
        northbound_only = metrics == (CapitalMetricType.NORTHBOUND,)
        if northbound_only:
            if instrument is not None:
                # Allowed but unused for northbound-only; still validate if given.
                self._require_a_share_capital_instrument(instrument, market_scope_ok=True)
            return
        if instrument is None:
            raise DataContractError(
                "instrument is required unless metrics is exactly (northbound,)",
                details={"field": "instrument", "rule": "required"},
            )
        self._require_a_share_capital_instrument(instrument, market_scope_ok=False)
        if (
            CapitalMetricType.CHIP_DISTRIBUTION in metrics
            and instrument.asset_type is not AssetType.EQUITY
        ):
            raise DataContractError(
                "chip distribution supports equity only",
                details={"field": "instrument", "rule": "asset_support"},
            )

    def _require_a_share_capital_instrument(
        self, instrument: Instrument, *, market_scope_ok: bool
    ) -> None:
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
        support = A_SHARE_TOOL_ASSET_SUPPORT["capital"].get(instrument.asset_type)
        if support == "reject" or support is None:
            raise DataContractError(
                "asset type not supported for capital",
                details={
                    "field": "instrument",
                    "rule": "asset_support",
                    "asset_type": instrument.asset_type.value,
                },
            )
        if instrument.asset_type is AssetType.INDEX and not market_scope_ok:
            # INDEX only for market-scope capital (northbound-only path).
            raise DataContractError(
                "INDEX capital is market-scope only",
                details={
                    "field": "instrument",
                    "rule": "asset_support",
                    "asset_type": instrument.asset_type.value,
                },
            )
        if instrument.asset_type is AssetType.ETF:
            # ETF: only provider-supported capital metrics (service accepts;
            # adapters may return empty/no-data per capability).
            return

    async def get(
        self,
        *,
        instrument: Instrument | None,
        metrics: tuple[CapitalMetricType, ...] = (),
        start: date | None = None,
        end: date | None = None,
        as_of: datetime,
        trade_date: date | None = None,
    ) -> AShareCapitalResult:
        require_aware_datetime(as_of, field_name="as_of")
        # Single sampled now for composition decisions (publication cutoffs, etc.).
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        if start is not None:
            start = _require_exact_date(start, field="start")
        if end is not None:
            end = _require_exact_date(end, field="end")
        if start is not None and end is not None and end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        if trade_date is not None:
            trade_date = _require_exact_date(trade_date, field="trade_date")

        ordered_metrics, required_metrics, _explicit = self._resolve_metrics(metrics)
        self._require_instrument_rules(instrument, ordered_metrics)

        # Dragon-tiger trade_date default: Asia/Shanghai local day of as_of.
        effective_trade_date = trade_date
        if CapitalMetricType.DRAGON_TIGER in ordered_metrics and effective_trade_date is None:
            effective_trade_date = as_of.astimezone(_SHANGHAI).date()

        warnings: list[WarningInfo] = []
        tasks: dict[CapitalMetricType, asyncio.Task[RouterExecutionResult[Any]]] = {}

        async with asyncio.TaskGroup() as tg:
            for metric in ordered_metrics:
                tasks[metric] = tg.create_task(
                    settle_router_component(
                        self._fetch_metric(
                            metric,
                            instrument=instrument,
                            start=start,
                            end=end,
                            as_of=as_of,
                            now=now,
                            trade_date=effective_trade_date,
                            required=metric in required_metrics,
                        )
                    )
                )

        # Deterministic merge in ordered_metrics (not completion order).
        values: dict[CapitalMetricType, Any] = {}
        provenance_items: list[AShareComponentProvenance] = []
        for metric in ordered_metrics:
            settled = tasks[metric].result()
            if settled.ok and settled.value is not None and settled.meta is not None:
                provenance_items.append(
                    component_provenance(
                        AShareComponentType(metric.value),
                        settled.meta,
                        settled.value,
                        empty_reliability=(
                            ReliabilityLevel.LOW
                            if metric is CapitalMetricType.CHIP_DISTRIBUTION
                            else None
                        ),
                        empty_authoritative=(
                            False if metric is CapitalMetricType.CHIP_DISTRIBUTION else None
                        ),
                        is_derived=metric is CapitalMetricType.CHIP_DISTRIBUTION,
                    )
                )
        provenance = tuple(provenance_items)
        for metric in ordered_metrics:
            self._merge_warnings(warnings, tasks[metric].result())
        for metric in ordered_metrics:
            result = tasks[metric].result()
            is_required = metric in required_metrics
            if result.ok and result.value is not None:
                values[metric] = result.value
            elif is_required:
                return AShareCapitalResult(
                    ok=False,
                    data=None,
                    warnings=tuple(warnings),
                    error=result.error
                    or DataContractError(
                        f"required capital metric {metric.value} failed",
                        details={"metric": metric.value, "rule": "required"},
                    ),
                    provenance=provenance,
                )
            else:
                self._partial(warnings, metric.value)

        dto = self._compose_dto(
            instrument=instrument,
            as_of=as_of,
            metrics=ordered_metrics,
            values=values,
            provenance=provenance,
        )
        return AShareCapitalResult(
            ok=True,
            data=dto,
            warnings=tuple(warnings),
            error=None,
            provenance=provenance,
        )

    def _compose_dto(
        self,
        *,
        instrument: Instrument | None,
        as_of: datetime,
        metrics: tuple[CapitalMetricType, ...],
        values: dict[CapitalMetricType, Any],
        provenance: tuple[AShareComponentProvenance, ...],
    ) -> AShareCapitalSnapshotDTO:
        unlocks: tuple[UnlockRecord, ...] = ()
        dividends: tuple[DividendRecord, ...] = ()
        if CapitalMetricType.UNLOCK in values:
            unlocks = values[CapitalMetricType.UNLOCK]
        if CapitalMetricType.DIVIDEND in values:
            dividends = values[CapitalMetricType.DIVIDEND]

        chip = values.get(CapitalMetricType.CHIP_DISTRIBUTION)
        return AShareCapitalSnapshotDTO(
            instrument_id=instrument.instrument_id if instrument is not None else None,
            as_of=as_of,
            metrics=metrics,
            intraday_flow=tuple(
                FundFlowPointDTO.from_domain(p)
                for p in values.get(CapitalMetricType.INTRADAY_FLOW, ())
            ),
            daily_flow=tuple(
                FundFlowPointDTO.from_domain(p)
                for p in values.get(CapitalMetricType.DAILY_FLOW, ())
            ),
            northbound=tuple(
                NorthboundFlowPointDTO.from_domain(p)
                for p in values.get(CapitalMetricType.NORTHBOUND, ())
            ),
            dragon_tiger=tuple(
                DragonTigerRecordDTO.from_domain(r)
                for r in values.get(CapitalMetricType.DRAGON_TIGER, ())
            ),
            margin=tuple(
                MarginRecordDTO.from_domain(r) for r in values.get(CapitalMetricType.MARGIN, ())
            ),
            block_trades=tuple(
                BlockTradeRecordDTO.from_domain(r)
                for r in values.get(CapitalMetricType.BLOCK_TRADE, ())
            ),
            shareholder_counts=tuple(
                ShareholderCountRecordDTO.from_domain(r)
                for r in values.get(CapitalMetricType.SHAREHOLDER_COUNT, ())
            ),
            chip_distribution=(
                ChipDistributionSnapshotDTO.from_domain(chip)
                if isinstance(chip, ChipDistributionSnapshot)
                else None
            ),
            unlocks=tuple(UnlockRecordDTO.from_domain(u) for u in unlocks),
            dividends=tuple(DividendRecordDTO.from_domain(d) for d in dividends),
            provenance=provenance_dtos(provenance),
        )

    @staticmethod
    def _partial(warnings: list[WarningInfo], metric: str) -> None:
        if not any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in warnings):
            warnings.append(
                WarningInfo(
                    code="PARTIAL_A_SHARE_SNAPSHOT",
                    message="One or more optional capital metrics failed",
                    details={"metric": metric},
                )
            )

    @staticmethod
    def _merge_warnings(warnings: list[WarningInfo], result: RouterExecutionResult[object]) -> None:
        """Propagate router + established meta warnings only (no invented codes)."""
        for w in result.warnings:
            if w not in warnings:
                warnings.append(w)
        if result.meta is not None:
            for code in result.meta.warnings:
                if code not in _ESTABLISHED_META_WARNING_CODES:
                    continue
                if any(x.code == code for x in warnings):
                    continue
                warnings.append(
                    WarningInfo(
                        code=code,
                        message=_META_WARNING_MESSAGES[code],
                        details={},
                    )
                )

    async def _fetch_metric(
        self,
        metric: CapitalMetricType,
        *,
        instrument: Instrument | None,
        start: date | None,
        end: date | None,
        as_of: datetime,
        now: datetime,
        trade_date: date | None,
        required: bool,
    ) -> RouterExecutionResult[Any]:
        policy = capital_metric_router_policy(metric, required=required)
        category = CAPITAL_METRIC_CATEGORY[metric]
        operation = _OP_BY_METRIC[metric]
        instrument_key = instrument.instrument_id if instrument is not None else "market"
        params: dict[str, str] = {"metric": metric.value}
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()
        if trade_date is not None and metric is CapitalMetricType.DRAGON_TIGER:
            params["trade_date"] = trade_date.isoformat()
        fingerprint = build_a_share_fingerprint(operation, instrument_key, params, as_of)

        if metric is CapitalMetricType.INTRADAY_FLOW:
            assert instrument is not None
            return await self._exec_intraday(instrument, as_of, policy, fingerprint)
        if metric is CapitalMetricType.DAILY_FLOW:
            assert instrument is not None
            return await self._exec_daily(
                instrument,
                start=start,
                end=end,
                as_of=as_of,
                tool_policy=policy,
                fingerprint=fingerprint,
            )
        if metric is CapitalMetricType.NORTHBOUND:
            return await self._exec_northbound(
                start=start,
                end=end,
                as_of=as_of,
                tool_policy=policy,
                fingerprint=fingerprint,
            )
        if metric is CapitalMetricType.DRAGON_TIGER:
            assert trade_date is not None
            return await self._exec_dragon_tiger(
                instrument,
                trade_date=trade_date,
                as_of=as_of,
                tool_policy=policy,
                fingerprint=fingerprint,
            )
        if metric is CapitalMetricType.MARGIN:
            assert instrument is not None
            return await self._exec_margin(
                instrument, as_of=as_of, tool_policy=policy, fingerprint=fingerprint
            )
        if metric is CapitalMetricType.BLOCK_TRADE:
            assert instrument is not None
            return await self._exec_block_trades(
                instrument, as_of=as_of, tool_policy=policy, fingerprint=fingerprint
            )
        if metric is CapitalMetricType.SHAREHOLDER_COUNT:
            assert instrument is not None
            return await self._exec_shareholder(
                instrument,
                as_of=as_of,
                now=now,
                tool_policy=policy,
                fingerprint=fingerprint,
            )
        if metric is CapitalMetricType.CHIP_DISTRIBUTION:
            assert instrument is not None
            return await self._exec_chip(
                instrument, as_of=as_of, tool_policy=policy, fingerprint=fingerprint
            )
        if metric is CapitalMetricType.UNLOCK:
            assert instrument is not None
            return await self._exec_actions_filtered(
                instrument,
                start=start,
                end=end,
                as_of=as_of,
                now=now,
                kind="unlock",
                tool_policy=policy,
                fingerprint=fingerprint,
                category=category,
            )
        if metric is CapitalMetricType.DIVIDEND:
            assert instrument is not None
            return await self._exec_actions_filtered(
                instrument,
                start=start,
                end=end,
                as_of=as_of,
                now=now,
                kind="dividend",
                tool_policy=policy,
                fingerprint=fingerprint,
                category=category,
            )
        raise DataContractError(
            "unknown capital metric",
            details={"field": "metric", "rule": "unknown"},
        )

    # --- router executes ------------------------------------------------------

    async def _exec_intraday(
        self,
        instrument: Instrument,
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[tuple[FundFlowPoint, ...]]:
        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
            if not isinstance(adapter, AShareIntradayFlowProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_intraday_flow(instrument, as_of)

        def _validator(success: ProviderSuccess[tuple[FundFlowPoint, ...]]) -> None:
            self._validate_fund_flow(success, as_of=as_of, expected_interval=BarInterval.ONE_MINUTE)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_INTRADAY_FLOW,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._intraday_flow_codec,
            result_validator=_validator,
        )

    async def _exec_daily(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[tuple[FundFlowPoint, ...]]:
        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[FundFlowPoint, ...]]:
            if not isinstance(adapter, AShareDailyFlowProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_daily_flow(instrument, start=start, end=end, as_of=as_of)

        def _validator(success: ProviderSuccess[tuple[FundFlowPoint, ...]]) -> None:
            self._validate_fund_flow(
                success,
                as_of=as_of,
                expected_interval=BarInterval.ONE_DAY,
                start=start,
                end=end,
            )

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_DAILY_FLOW,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._daily_flow_codec,
            result_validator=_validator,
        )

    async def _exec_northbound(
        self,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[tuple[NorthboundFlowPoint, ...]]:
        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[NorthboundFlowPoint, ...]]:
            if not isinstance(adapter, AShareNorthboundProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_northbound(start=start, end=end, as_of=as_of)

        def _validator(
            success: ProviderSuccess[tuple[NorthboundFlowPoint, ...]],
        ) -> None:
            self._validate_northbound(success, as_of=as_of, start=start, end=end)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_NORTHBOUND,
            request_fingerprint=fingerprint,
            instrument=None,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._northbound_codec,
            result_validator=_validator,
        )

    async def _exec_dragon_tiger(
        self,
        instrument: Instrument | None,
        *,
        trade_date: date,
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[tuple[DragonTigerRecord, ...]]:
        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[DragonTigerRecord, ...]]:
            if not isinstance(adapter, AShareDragonTigerProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_dragon_tiger(instrument, trade_date=trade_date, as_of=as_of)

        def _validator(
            success: ProviderSuccess[tuple[DragonTigerRecord, ...]],
        ) -> None:
            self._validate_dragon_tiger(success, trade_date=trade_date, instrument=instrument)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_DRAGON_TIGER,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._dragon_tiger_codec,
            result_validator=_validator,
        )

    async def _exec_margin(
        self,
        instrument: Instrument,
        *,
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[tuple[MarginRecord, ...]]:
        limit = self._margin_limit

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[MarginRecord, ...]]:
            if not isinstance(adapter, AShareMarginProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_margin(instrument, limit=limit, as_of=as_of)

        def _validator(success: ProviderSuccess[tuple[MarginRecord, ...]]) -> None:
            self._validate_margin(success, as_of=as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_MARGIN,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._margin_codec,
            result_validator=_validator,
        )

    async def _exec_block_trades(
        self,
        instrument: Instrument,
        *,
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[tuple[BlockTradeRecord, ...]]:
        limit = self._block_trade_limit

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[BlockTradeRecord, ...]]:
            if not isinstance(adapter, AShareBlockTradeProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_block_trades(instrument, limit=limit, as_of=as_of)

        def _validator(
            success: ProviderSuccess[tuple[BlockTradeRecord, ...]],
        ) -> None:
            self._validate_block_trades(success, as_of=as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_BLOCK_TRADES,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._block_trades_codec,
            result_validator=_validator,
        )

    async def _exec_shareholder(
        self,
        instrument: Instrument,
        *,
        as_of: datetime,
        now: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[tuple[ShareholderCountRecord, ...]]:
        limit = self._shareholder_limit

        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[ShareholderCountRecord, ...]]:
            if not isinstance(adapter, AShareShareholderProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_shareholder_counts(instrument, limit=limit, as_of=as_of)

        def _validator(
            success: ProviderSuccess[tuple[ShareholderCountRecord, ...]],
        ) -> None:
            self._validate_shareholder(success, as_of=as_of, now=now)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_SHAREHOLDER_COUNTS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._shareholder_counts_codec,
            result_validator=_validator,
        )

    async def _exec_chip(
        self,
        instrument: Instrument,
        *,
        as_of: datetime,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
    ) -> RouterExecutionResult[ChipDistributionSnapshot]:
        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[ChipDistributionSnapshot]:
            if not isinstance(adapter, AShareChipProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": DataCategory.CAPITAL.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_chip_distribution(instrument, as_of)

        def _validator(success: ProviderSuccess[ChipDistributionSnapshot]) -> None:
            self._validate_chip(success, instrument=instrument, as_of=as_of)

        return await self._router.execute(
            market=Market.A_SHARE,
            category=DataCategory.CAPITAL,
            call=_call,
            operation_name=OP_CHIP_DISTRIBUTION,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._chip_distribution_codec,
            result_validator=_validator,
        )

    async def _exec_actions_filtered(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        as_of: datetime,
        now: datetime,
        kind: str,
        tool_policy: ToolDataPolicy,
        fingerprint: str,
        category: DataCategory,
    ) -> RouterExecutionResult[tuple[Any, ...]]:
        async def _call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]:
            if not isinstance(adapter, AShareDisclosureProvider):
                raise DataContractError(
                    "adapter does not implement required A-share protocol",
                    details={
                        "category": category.value,
                        "rule": "protocol",
                    },
                )
            return await adapter.get_corporate_actions(
                instrument, start=start, end=end, as_of=as_of
            )

        def _validator(
            success: ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]],
        ) -> None:
            self._validate_corporate_actions(success, as_of=as_of, now=now)

        result = await self._router.execute(
            market=Market.A_SHARE,
            category=category,
            call=_call,
            operation_name=OP_CORPORATE_ACTIONS,
            request_fingerprint=fingerprint,
            instrument=instrument,
            as_of=as_of,
            tool_policy=tool_policy,
            bypass_cache=False,
            cache_codec=self._corporate_actions_codec,
            result_validator=_validator,
        )
        if not result.ok or result.value is None:
            return result
        if kind == "unlock":
            filtered: tuple[Any, ...] = tuple(
                item for item in result.value if isinstance(item, UnlockRecord)
            )
        else:
            filtered = tuple(item for item in result.value if isinstance(item, DividendRecord))
        # RouterExecutionResult is frozen; rebuild with filtered value.
        return RouterExecutionResult(
            value=filtered,
            ok=True,
            criticality=result.criticality,
            meta=result.meta,
            attempts=result.attempts,
            warnings=result.warnings,
            error=None,
        )
