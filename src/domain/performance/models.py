"""Immutable native-currency account-performance models.

These models intentionally keep the broker snapshot as the valuation source.
In particular, ``equity_value`` is never reconstructed from cash and position
market values: a missing broker ``net_assets`` stays missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.performance.enums import PerformanceComputationStatus, PerformanceStatus
from domain.portfolio.enums import TradeCycleStatus


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be a bounded non-blank string")
    return value


def _decimal(value: Decimal | None, field: str) -> None:
    if value is not None and (type(value) is not Decimal or not value.is_finite()):
        raise DataContractError(f"{field} must be a finite Decimal")


def _codes(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple) or len(values) != len(set(values)):
        raise DataContractError(f"{field} must be a unique tuple")
    for value in values:
        _text(value, field, 128)


def _ids(values: tuple[str, ...], field: str) -> None:
    _codes(values, field)


@dataclass(frozen=True, slots=True)
class CyclePerformance:
    """Return facts that can be proven from one durable ``TradeCycle``.

    A closed cycle can expose realized P/L divided by its deterministic
    maximum deployed capital.  Existing durable facts do not contain a
    planned-risk snapshot or an end-of-period mark for an open cycle, so those
    metrics remain ``None`` instead of being inferred from unrelated fields.
    """

    cycle_id: str
    account_ref: str
    currency: str
    instrument_id: str | None
    opened_at: datetime | None
    closed_at: datetime | None
    status: TradeCycleStatus
    opening_count: int
    add_count: int
    reduce_count: int
    holding_duration_seconds: int | None
    gross_realized_pnl: Decimal | None
    net_realized_pnl: Decimal | None
    remaining_unrealized_pnl: Decimal | None
    maximum_deployed_capital: Decimal | None
    cycle_return: Decimal | None
    initial_planned_risk: Decimal | None
    r_multiple: Decimal | None
    activity_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]
    algorithm_version: str = "performance_returns_v1"

    def __post_init__(self) -> None:
        _text(self.cycle_id, "cycle_id", 160)
        _text(self.account_ref, "account_ref", 128)
        _text(self.currency, "currency", 16)
        if self.instrument_id is not None:
            _text(self.instrument_id, "instrument_id", 256)
        for field_name in ("opened_at", "closed_at"):
            value = getattr(self, field_name)
            if value is not None:
                require_aware_datetime(value, field_name=field_name)
        if (
            self.opened_at is not None
            and self.closed_at is not None
            and self.closed_at < self.opened_at
        ):
            raise DataContractError("cycle performance closed_at precedes opened_at")
        if not isinstance(self.status, TradeCycleStatus):
            raise DataContractError("cycle performance status is invalid")
        for field_name in ("opening_count", "add_count", "reduce_count"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise DataContractError(f"{field_name} must be a nonnegative int")
        if self.holding_duration_seconds is not None and (
            type(self.holding_duration_seconds) is not int
            or self.holding_duration_seconds < 0
        ):
            raise DataContractError("holding_duration_seconds must be nonnegative int")
        for field_name in (
            "gross_realized_pnl",
            "net_realized_pnl",
            "remaining_unrealized_pnl",
            "maximum_deployed_capital",
            "cycle_return",
            "initial_planned_risk",
            "r_multiple",
        ):
            _decimal(getattr(self, field_name), field_name)
        if self.maximum_deployed_capital is not None and self.maximum_deployed_capital < 0:
            raise DataContractError("maximum_deployed_capital must be nonnegative")
        if self.initial_planned_risk is not None and self.initial_planned_risk <= 0:
            raise DataContractError("initial_planned_risk must be positive")
        _ids(self.activity_ids, "activity_ids")
        _codes(self.warning_codes, "warning_codes")
        _text(self.algorithm_version, "algorithm_version", 64)

    @property
    def return_on_maximum_deployed_capital(self) -> Decimal | None:
        return self.cycle_return

    @property
    def planned_risk(self) -> Decimal | None:
        return self.initial_planned_risk


@dataclass(frozen=True, slots=True)
class DailyEquityPoint:
    """One auditable broker valuation point in one account/native currency.

    ``source_snapshot_as_of`` and ``source_fetched_at`` preserve both the
    valuation timestamp and the observation timestamp.  The latter must not
    be substituted for the valuation time when calculating returns.
    """

    account_ref: str
    currency: str
    valuation_at: datetime
    market_session_date: date
    equity_value: Decimal | None
    source_snapshot_id: str
    source_snapshot_as_of: datetime
    source_fetched_at: datetime
    coverage_status: PerformanceStatus
    net_external_cash_flow_since_previous: Decimal = Decimal(0)
    external_flow_ids: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    valuation_basis: str = "BROKER_NET_ASSETS"

    def __post_init__(self) -> None:
        _text(self.account_ref, "account_ref", 128)
        _text(self.currency, "currency", 16)
        require_aware_datetime(self.valuation_at, field_name="valuation_at")
        require_aware_datetime(self.source_snapshot_as_of, field_name="source_snapshot_as_of")
        require_aware_datetime(self.source_fetched_at, field_name="source_fetched_at")
        if not isinstance(self.market_session_date, date):
            raise DataContractError("market_session_date must be a date")
        _decimal(self.equity_value, "equity_value")
        _decimal(
            self.net_external_cash_flow_since_previous,
            "net_external_cash_flow_since_previous",
        )
        _text(self.source_snapshot_id, "source_snapshot_id", 128)
        if self.source_snapshot_as_of > self.source_fetched_at:
            raise DataContractError("source_snapshot_as_of must be <= source_fetched_at")
        if not isinstance(self.coverage_status, PerformanceStatus):
            raise DataContractError("coverage_status is invalid")
        _ids(self.external_flow_ids, "external_flow_ids")
        _codes(self.warning_codes, "warning_codes")
        _text(self.valuation_basis, "valuation_basis", 64)

    # Compatibility/readability aliases used by callers that refer to the
    # source object as a normal account snapshot.
    @property
    def snapshot_id(self) -> str:
        return self.source_snapshot_id

    @property
    def snapshot_as_of(self) -> datetime:
        return self.source_snapshot_as_of

    @property
    def fetched_at(self) -> datetime:
        return self.source_fetched_at

    @property
    def net_external_cash_flow(self) -> Decimal:
        return self.net_external_cash_flow_since_previous

    @property
    def coverage(self) -> PerformanceStatus:
        return self.coverage_status

    @property
    def valuation_time(self) -> datetime:
        return self.valuation_at


@dataclass(frozen=True, slots=True)
class PerformanceSeries:
    """A deterministic native-currency performance series.

    All source IDs and timestamps are retained so a future durable Run can
    reference the exact inputs without copying or rewriting account facts.
    ``xirr`` is the money-weighted return (MWR) under the actual timestamp
    convention; ``mwr`` is exposed as a property alias.
    """

    account_ref: str
    currency: str
    period_start: datetime
    period_end: datetime
    points: tuple[DailyEquityPoint, ...]
    twr: Decimal | None
    xirr: Decimal | None
    maximum_drawdown: Decimal | None
    twr_index: tuple[Decimal, ...]
    status: PerformanceStatus
    input_snapshot_ids: tuple[str, ...]
    input_snapshot_times: tuple[datetime, ...]
    input_activity_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]
    twr_status: PerformanceComputationStatus = PerformanceComputationStatus.UNAVAILABLE
    xirr_status: PerformanceComputationStatus = PerformanceComputationStatus.UNAVAILABLE
    drawdown_status: PerformanceComputationStatus = PerformanceComputationStatus.UNAVAILABLE
    algorithm_version: str = "performance_returns_v1"
    dividends: Decimal | None = None
    interest: Decimal | None = None
    known_fees: Decimal | None = None
    capital_base: Decimal | None = None
    income_return: Decimal | None = None
    fee_drag: Decimal | None = None
    income_return_status: PerformanceComputationStatus = (
        PerformanceComputationStatus.UNAVAILABLE
    )
    fee_drag_status: PerformanceComputationStatus = PerformanceComputationStatus.UNAVAILABLE
    cycle_performance: tuple[CyclePerformance, ...] = ()
    input_cycle_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.account_ref, "account_ref", 128)
        _text(self.currency, "currency", 16)
        require_aware_datetime(self.period_start, field_name="period_start")
        require_aware_datetime(self.period_end, field_name="period_end")
        if self.period_start > self.period_end:
            raise DataContractError("performance period is invalid")
        if not isinstance(self.points, tuple) or any(
            not isinstance(item, DailyEquityPoint) for item in self.points
        ):
            raise DataContractError("performance points are invalid")
        if any(
            item.account_ref != self.account_ref or item.currency != self.currency
            for item in self.points
        ):
            raise DataContractError("performance points have mismatched identity")
        if any(
            self.points[index].valuation_at > self.points[index + 1].valuation_at
            for index in range(len(self.points) - 1)
        ):
            raise DataContractError("performance points must be ordered")
        for field_name in ("twr", "xirr", "maximum_drawdown"):
            _decimal(getattr(self, field_name), field_name)
        for field_name in (
            "dividends",
            "interest",
            "known_fees",
            "capital_base",
            "income_return",
            "fee_drag",
        ):
            _decimal(getattr(self, field_name), field_name)
        if not isinstance(self.twr_index, tuple) or any(
            type(value) is not Decimal or not value.is_finite() for value in self.twr_index
        ):
            raise DataContractError("twr_index must contain finite Decimals")
        if self.twr_index and len(self.twr_index) != len(self.points):
            raise DataContractError("twr_index must align with points")
        if not isinstance(self.status, PerformanceStatus):
            raise DataContractError("performance status is invalid")
        for field_name in ("twr_status", "xirr_status", "drawdown_status"):
            if not isinstance(getattr(self, field_name), PerformanceComputationStatus):
                raise DataContractError(f"{field_name} is invalid")
        for field_name in ("income_return_status", "fee_drag_status"):
            if not isinstance(getattr(self, field_name), PerformanceComputationStatus):
                raise DataContractError(f"{field_name} is invalid")
        _ids(self.input_snapshot_ids, "input_snapshot_ids")
        if len(self.input_snapshot_times) != len(self.input_snapshot_ids):
            raise DataContractError("input_snapshot_times must align with input_snapshot_ids")
        for value in self.input_snapshot_times:
            require_aware_datetime(value, field_name="input_snapshot_time")
        _ids(self.input_activity_ids, "input_activity_ids")
        _codes(self.warning_codes, "warning_codes")
        _text(self.algorithm_version, "algorithm_version", 64)
        if not isinstance(self.cycle_performance, tuple) or any(
            not isinstance(item, CyclePerformance) for item in self.cycle_performance
        ):
            raise DataContractError("cycle_performance is invalid")
        if any(
            item.account_ref != self.account_ref or item.currency != self.currency
            for item in self.cycle_performance
        ):
            raise DataContractError("cycle performance has mismatched identity")
        _ids(self.input_cycle_ids, "input_cycle_ids")
        if self.input_cycle_ids != tuple(item.cycle_id for item in self.cycle_performance):
            raise DataContractError("input_cycle_ids must align with cycle_performance")

    @property
    def start(self) -> datetime:
        return self.period_start

    @property
    def end(self) -> datetime:
        return self.period_end

    @property
    def time_weighted_return(self) -> Decimal | None:
        return self.twr

    @property
    def money_weighted_return(self) -> Decimal | None:
        return self.xirr

    @property
    def mwr(self) -> Decimal | None:
        return self.xirr

    @property
    def mwr_status(self) -> PerformanceComputationStatus:
        return self.xirr_status

    @property
    def money_weighted_status(self) -> PerformanceComputationStatus:
        return self.xirr_status

    @property
    def max_drawdown(self) -> Decimal | None:
        return self.maximum_drawdown

    @property
    def coverage_status(self) -> PerformanceStatus:
        return self.status

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        return self.input_snapshot_ids

    @property
    def activity_ids(self) -> tuple[str, ...]:
        return self.input_activity_ids

    @property
    def account_snapshot_ids(self) -> tuple[str, ...]:
        return self.input_snapshot_ids

    @property
    def transaction_ids(self) -> tuple[str, ...]:
        return self.input_activity_ids

    @property
    def equity_points(self) -> tuple[DailyEquityPoint, ...]:
        return self.points

    @property
    def selected_capital_base(self) -> Decimal | None:
        return self.capital_base

    @property
    def cycle_returns(self) -> tuple[CyclePerformance, ...]:
        return self.cycle_performance
