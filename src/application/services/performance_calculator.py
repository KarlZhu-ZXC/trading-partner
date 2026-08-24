"""Pure account-return calculations over exact snapshot/activity inputs.

This service is intentionally read-only and Provider-free.  It turns
``AccountSnapshot`` history into auditable ``DailyEquityPoint`` values and
calculates native-currency TWR, XIRR/MWR, and drawdown only when the source
facts prove the required boundaries.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext

from domain.performance.enums import PerformanceComputationStatus, PerformanceStatus
from domain.performance.models import CyclePerformance, DailyEquityPoint, PerformanceSeries
from domain.portfolio.enums import (
    AccountTransactionKind,
    TradeCycleQuality,
    TradeCycleStatus,
)
from domain.portfolio.models import (
    AccountSnapshot,
    AccountTransaction,
    TradeCycle,
    TradeCycleProjection,
)

_SECONDS_PER_YEAR = 365.0 * 24.0 * 60.0 * 60.0
_XIRR_ALGORITHM_VERSION = "performance_returns_v1"


@dataclass(frozen=True, slots=True)
class _Flow:
    transaction: AccountTransaction

    @property
    def at(self) -> datetime:
        return self.transaction.occurred_at

    @property
    def amount(self) -> Decimal:
        # AccountTransaction cash_amount follows the account's perspective:
        # positive means cash entering the account, negative means leaving it.
        return self.transaction.cash_amount or Decimal(0)

    @property
    def id(self) -> str:
        return self.transaction.provider_transaction_id


@dataclass(frozen=True, slots=True)
class _XirrOutcome:
    value: Decimal | None
    status: PerformanceComputationStatus
    warnings: tuple[str, ...]


def _activities_argument(
    transactions: tuple[AccountTransaction, ...],
    *aliases: tuple[AccountTransaction, ...] | None,
) -> tuple[AccountTransaction, ...]:
    provided = tuple(item for item in aliases if item is not None)
    if transactions and provided:
        raise ValueError("pass either transactions or an external cash-flow alias")
    if len(provided) > 1:
        raise ValueError("pass only one external cash-flow alias")
    return provided[0] if provided else transactions


def _cycles_argument(
    cycles: tuple[TradeCycle, ...] | TradeCycleProjection | None,
) -> tuple[tuple[TradeCycle, ...], tuple[str, ...]]:
    if cycles is None:
        return (), ()
    if isinstance(cycles, TradeCycleProjection):
        return cycles.cycles, cycles.warning_codes
    return cycles, ()


class PerformanceCalculator:
    """Calculate trustworthy native-currency account returns.

    ``calculate_all`` is the explicit multi-account/multi-currency entry
    point.  ``calculate`` returns the single series when the inputs identify
    one group and returns a tuple when they contain several groups, which
    keeps the convenient single-account call compatible with the grouped
    portfolio use case.  ``calculate_series`` always requires one group.
    """

    algorithm_version = _XIRR_ALGORITHM_VERSION

    def calculate(
        self,
        *,
        snapshots: tuple[AccountSnapshot, ...],
        transactions: tuple[AccountTransaction, ...] = (),
        external_cash_flows: tuple[AccountTransaction, ...] | None = None,
        cash_flows: tuple[AccountTransaction, ...] | None = None,
        transfers: tuple[AccountTransaction, ...] | None = None,
        cycles: tuple[TradeCycle, ...] | TradeCycleProjection | None = None,
        account_ref: str | None = None,
        currency: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        coverage_status: PerformanceStatus | str = PerformanceStatus.COMPLETE,
    ) -> PerformanceSeries | tuple[PerformanceSeries, ...]:
        """Calculate one or more exact account/native-currency series.

        ``external_cash_flows`` is a descriptive alias for callers that have
        already filtered the activity ledger.  It may not be combined with a
        non-empty ``transactions`` argument.
        """

        transactions = _activities_argument(
            transactions, external_cash_flows, cash_flows, transfers
        )
        values = self.calculate_all(
            snapshots=snapshots,
            transactions=transactions,
            cycles=cycles,
            account_ref=account_ref,
            currency=currency,
            start=start,
            end=end,
            coverage_status=coverage_status,
        )
        if len(values) == 1:
            return values[0]
        return values

    def calculate_series(
        self,
        *,
        snapshots: tuple[AccountSnapshot, ...],
        transactions: tuple[AccountTransaction, ...] = (),
        external_cash_flows: tuple[AccountTransaction, ...] | None = None,
        cash_flows: tuple[AccountTransaction, ...] | None = None,
        transfers: tuple[AccountTransaction, ...] | None = None,
        cycles: tuple[TradeCycle, ...] | TradeCycleProjection | None = None,
        account_ref: str | None = None,
        currency: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        coverage_status: PerformanceStatus | str = PerformanceStatus.COMPLETE,
    ) -> PerformanceSeries:
        """Calculate exactly one account/native-currency series."""

        transactions = _activities_argument(
            transactions, external_cash_flows, cash_flows, transfers
        )
        values = self.calculate_all(
            snapshots=snapshots,
            transactions=transactions,
            cycles=cycles,
            account_ref=account_ref,
            currency=currency,
            start=start,
            end=end,
            coverage_status=coverage_status,
        )
        if len(values) != 1:
            raise ValueError("calculate_series requires one account/native-currency group")
        return values[0]

    # Familiar aliases used by application callers that name the account
    # boundary explicitly.
    calculate_account = calculate_series
    calculate_performance = calculate_series

    def calculate_all(
        self,
        *,
        snapshots: tuple[AccountSnapshot, ...],
        transactions: tuple[AccountTransaction, ...] = (),
        external_cash_flows: tuple[AccountTransaction, ...] | None = None,
        cash_flows: tuple[AccountTransaction, ...] | None = None,
        transfers: tuple[AccountTransaction, ...] | None = None,
        cycles: tuple[TradeCycle, ...] | TradeCycleProjection | None = None,
        account_ref: str | None = None,
        currency: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        coverage_status: PerformanceStatus | str = PerformanceStatus.COMPLETE,
    ) -> tuple[PerformanceSeries, ...]:
        """Calculate deterministic series sorted by ``(account_ref, currency)``."""

        transactions = _activities_argument(
            transactions, external_cash_flows, cash_flows, transfers
        )
        cycle_values, cycle_input_warnings = _cycles_argument(cycles)

        groups: set[tuple[str, str]] = {
            (item.account_ref, item.base_currency) for item in snapshots
        }
        groups.update(
            (item.account_ref, item.currency)
            for item in transactions
            if item.kind is AccountTransactionKind.TRANSFER
        )
        groups.update(
            (item.account_ref, item.currency)
            for item in cycle_values
            if item.currency is not None
        )
        if account_ref is not None:
            groups = {item for item in groups if item[0] == account_ref}
        if currency is not None:
            groups = {item for item in groups if item[1] == currency}
        if not groups:
            # Preserve a requested identity even when there are no exact
            # source rows; callers get a visible incomplete series rather than
            # an implicit "no account" success.
            if account_ref is not None and currency is not None:
                groups = {(account_ref, currency)}
            else:
                return ()
        try:
            normalized_coverage = (
                coverage_status
                if isinstance(coverage_status, PerformanceStatus)
                else PerformanceStatus(str(coverage_status))
            )
        except ValueError as exc:
            raise ValueError("coverage_status is invalid") from exc
        return tuple(
            self._calculate_group(
                account_ref=group_account,
                currency=group_currency,
                snapshots=snapshots,
                transactions=transactions,
                cycles=cycle_values,
                cycle_input_warnings=cycle_input_warnings,
                start=start,
                end=end,
                coverage_status=normalized_coverage,
            )
            for group_account, group_currency in sorted(groups)
        )

    def _calculate_group(
        self,
        *,
        account_ref: str,
        currency: str,
        snapshots: tuple[AccountSnapshot, ...],
        transactions: tuple[AccountTransaction, ...],
        cycles: tuple[TradeCycle, ...],
        cycle_input_warnings: tuple[str, ...],
        start: datetime | None,
        end: datetime | None,
        coverage_status: PerformanceStatus,
    ) -> PerformanceSeries:
        warnings: set[str] = set()
        warnings.update(cycle_input_warnings)
        relevant_snapshots = [
            item
            for item in snapshots
            if item.account_ref == account_ref
            and item.base_currency == currency
            and (start is None or item.account_as_of >= start)
            and (end is None or item.account_as_of <= end)
        ]
        relevant_snapshots.sort(key=lambda item: (item.account_as_of, item.snapshot_id))
        relevant_activities = [
            item
            for item in transactions
            if item.account_ref == account_ref
            and item.currency == currency
            and (start is None or item.occurred_at >= start)
            and (end is None or item.occurred_at <= end)
        ]
        relevant_activities.sort(key=lambda item: (item.occurred_at, item.provider_transaction_id))
        relevant_cycles = sorted(
            (
                item
                for item in cycles
                if item.account_ref == account_ref
                and item.currency == currency
            ),
            key=lambda item: (
                item.opened_at or item.closed_at or datetime.min.replace(tzinfo=UTC),
                item.cycle_id,
            ),
        )
        input_snapshot_ids = tuple(item.snapshot_id for item in relevant_snapshots)
        input_snapshot_times = tuple(item.account_as_of for item in relevant_snapshots)
        input_activity_ids = tuple(item.provider_transaction_id for item in relevant_activities)
        input_cycle_ids = tuple(item.cycle_id for item in relevant_cycles)

        if start is not None and (
            not relevant_snapshots or relevant_snapshots[0].account_as_of > start
        ):
            warnings.add("PERIOD_START_VALUATION_UNAVAILABLE")
        if end is not None and (
            not relevant_snapshots or relevant_snapshots[-1].account_as_of < end
        ):
            warnings.add("PERIOD_END_VALUATION_UNAVAILABLE")
        if not relevant_snapshots:
            warnings.add("VALUATION_SNAPSHOTS_UNAVAILABLE")

        flows = tuple(
            _Flow(item)
            for item in relevant_activities
            if item.kind is AccountTransactionKind.TRANSFER
        )
        # Dividend, interest, fees, trades, and corporate actions are investment
        # results or separate activity facts, not external cash flows.

        points = self._points(
            account_ref=account_ref,
            currency=currency,
            snapshots=tuple(relevant_snapshots),
            flows=flows,
            warnings=warnings,
        )
        if flows and points:
            first_at = points[0].valuation_at
            last_at = points[-1].valuation_at
            if any(flow.at <= first_at or flow.at > last_at for flow in flows):
                warnings.add("TWR_CASH_FLOW_BOUNDARY_MISSING")

        twr, twr_index, twr_status, twr_warnings = self._twr(points, flows)
        warnings.update(twr_warnings)
        xirr_outcome = self._xirr(points, flows)
        warnings.update(xirr_outcome.warnings)
        if coverage_status is not PerformanceStatus.COMPLETE:
            twr = None
            twr_index = ()
            twr_status = PerformanceComputationStatus.UNAVAILABLE
            warnings.add("TWR_INPUT_COVERAGE_INCOMPLETE")
            xirr_outcome = _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(set(xirr_outcome.warnings) | {"XIRR_INPUT_COVERAGE_INCOMPLETE"})),
            )
            warnings.update(xirr_outcome.warnings)
        (
            dividends,
            interest,
            known_fees,
            capital_base,
            income_return,
            fee_drag,
            income_return_status,
            fee_drag_status,
            income_fee_warnings,
        ) = self._income_and_fees(
            activities=tuple(relevant_activities),
            points=points,
            coverage_status=coverage_status,
        )
        warnings.update(income_fee_warnings)
        cycle_performance = self._cycle_performance(relevant_cycles)
        warnings.update(
            code for item in cycle_performance for code in item.warning_codes
        )
        maximum_drawdown, drawdown_status, drawdown_warnings = self._drawdown(
            twr_index=twr_index,
            points=points,
            twr_status=twr_status,
        )
        warnings.update(drawdown_warnings)

        if coverage_status is PerformanceStatus.INCOMPLETE:
            warnings.add("INPUT_COVERAGE_INCOMPLETE")
        status = (
            PerformanceStatus.COMPLETE
            if not warnings and points and all(item.equity_value is not None for item in points)
            else PerformanceStatus.INCOMPLETE
        )
        period_start, period_end = self._period(
            points=points,
            flows=flows,
            start=start,
            end=end,
        )
        return PerformanceSeries(
            account_ref=account_ref,
            currency=currency,
            period_start=period_start,
            period_end=period_end,
            points=points,
            twr=twr,
            xirr=xirr_outcome.value,
            maximum_drawdown=maximum_drawdown,
            twr_index=twr_index,
            status=status,
            input_snapshot_ids=input_snapshot_ids,
            input_snapshot_times=input_snapshot_times,
            input_activity_ids=input_activity_ids,
            warning_codes=tuple(sorted(warnings)),
            twr_status=twr_status,
            xirr_status=xirr_outcome.status,
            drawdown_status=drawdown_status,
            algorithm_version=self.algorithm_version,
            dividends=dividends,
            interest=interest,
            known_fees=known_fees,
            capital_base=capital_base,
            income_return=income_return,
            fee_drag=fee_drag,
            income_return_status=income_return_status,
            fee_drag_status=fee_drag_status,
            cycle_performance=cycle_performance,
            input_cycle_ids=input_cycle_ids,
        )

    @staticmethod
    def _income_and_fees(
        *,
        activities: tuple[AccountTransaction, ...],
        points: tuple[DailyEquityPoint, ...],
        coverage_status: PerformanceStatus,
    ) -> tuple[
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        PerformanceComputationStatus,
        PerformanceComputationStatus,
        tuple[str, ...],
    ]:
        """Calculate income/fee ratios without treating missing facts as zero."""

        warnings: set[str] = set()
        dividend_rows = tuple(
            item for item in activities if item.kind is AccountTransactionKind.DIVIDEND
        )
        interest_rows = tuple(
            item for item in activities if item.kind is AccountTransactionKind.INTEREST
        )
        dividend_amount: Decimal | None = (
            sum((item.cash_amount or Decimal(0) for item in dividend_rows), Decimal(0))
            if all(item.cash_amount is not None for item in dividend_rows)
            else None
        )
        interest_amount: Decimal | None = (
            sum((item.cash_amount or Decimal(0) for item in interest_rows), Decimal(0))
            if all(item.cash_amount is not None for item in interest_rows)
            else None
        )
        if dividend_amount is None:
            warnings.add("DIVIDEND_ACTIVITY_AMOUNT_UNAVAILABLE")
        if interest_amount is None:
            warnings.add("INTEREST_ACTIVITY_AMOUNT_UNAVAILABLE")

        known_fee_values: list[Decimal] = []
        fees_complete = True
        for item in activities:
            if item.kind is AccountTransactionKind.TRADE:
                if item.fees is None:
                    fees_complete = False
                    continue
                known_fee_values.append(item.fees)
            elif item.kind is AccountTransactionKind.FEE:
                if item.cash_amount is not None:
                    known_fee_values.append(abs(item.cash_amount))
                elif item.fees is not None:
                    known_fee_values.append(item.fees)
                else:
                    fees_complete = False
        known_fees: Decimal | None = (
            sum(known_fee_values, Decimal(0)) if fees_complete else None
        )
        if known_fees is None:
            warnings.add("FEE_ACTIVITY_AMOUNT_UNAVAILABLE")

        capital_base = points[0].equity_value if points else None
        source_coverage_complete = all(
            item.coverage_status is PerformanceStatus.COMPLETE for item in points
        )
        if coverage_status is not PerformanceStatus.COMPLETE or not source_coverage_complete:
            warnings.add("INCOME_ACTIVITY_COVERAGE_INCOMPLETE")
            warnings.add("FEE_ACTIVITY_COVERAGE_INCOMPLETE")
            dividend_amount = None
            interest_amount = None
            known_fees = None
        if capital_base is None or capital_base == 0:
            warnings.add("INCOME_RETURN_CAPITAL_BASE_UNAVAILABLE")
            warnings.add("FEE_DRAG_CAPITAL_BASE_UNAVAILABLE")
            income_status = PerformanceComputationStatus.UNAVAILABLE
            fee_status = PerformanceComputationStatus.UNAVAILABLE
            income_return = None
            fee_drag = None
        else:
            if dividend_amount is None or interest_amount is None:
                income_status = PerformanceComputationStatus.UNAVAILABLE
                income_return = None
            else:
                income_status = PerformanceComputationStatus.COMPUTED
                income_return = (dividend_amount + interest_amount) / capital_base
            if known_fees is None:
                fee_status = PerformanceComputationStatus.UNAVAILABLE
                fee_drag = None
            else:
                fee_status = PerformanceComputationStatus.COMPUTED
                fee_drag = known_fees / capital_base
        return (
            dividend_amount,
            interest_amount,
            known_fees,
            capital_base,
            income_return,
            fee_drag,
            income_status,
            fee_status,
            tuple(sorted(warnings)),
        )

    @staticmethod
    def _cycle_performance(
        cycles: list[TradeCycle],
    ) -> tuple[CyclePerformance, ...]:
        result: list[CyclePerformance] = []
        for cycle in cycles:
            warnings = set(cycle.warning_codes)
            cycle_return: Decimal | None = None
            if cycle.status is TradeCycleStatus.CLOSED:
                if cycle.quality is not TradeCycleQuality.COMPLETE:
                    warnings.add("CYCLE_COVERAGE_INCOMPLETE")
                elif cycle.net_realized_pnl is None:
                    warnings.add("CYCLE_NET_PNL_UNAVAILABLE")
                elif cycle.maximum_deployed_capital in (None, Decimal(0)):
                    warnings.add("CYCLE_MAX_DEPLOYED_CAPITAL_UNAVAILABLE")
                else:
                    cycle_return = (
                        cycle.net_realized_pnl / cycle.maximum_deployed_capital
                    )
            elif cycle.status is TradeCycleStatus.OPEN:
                warnings.add("CYCLE_UNREALIZED_PNL_UNAVAILABLE")
            else:
                warnings.add("CYCLE_RETURN_UNAVAILABLE")
            # No durable Trade Plan risk snapshot is attached to a TradeCycle;
            # maximum deployed capital is not a substitute for planned risk.
            warnings.add("CYCLE_PLANNED_RISK_UNAVAILABLE")
            result.append(
                CyclePerformance(
                    cycle_id=cycle.cycle_id,
                    account_ref=cycle.account_ref,
                    currency=cycle.currency or "",
                    instrument_id=cycle.instrument_id,
                    opened_at=cycle.opened_at,
                    closed_at=cycle.closed_at,
                    status=cycle.status,
                    opening_count=cycle.opening_count,
                    add_count=cycle.add_count,
                    reduce_count=cycle.reduce_count,
                    holding_duration_seconds=cycle.holding_duration_seconds,
                    gross_realized_pnl=cycle.gross_realized_pnl,
                    net_realized_pnl=cycle.net_realized_pnl,
                    remaining_unrealized_pnl=None,
                    maximum_deployed_capital=cycle.maximum_deployed_capital,
                    cycle_return=cycle_return,
                    initial_planned_risk=None,
                    r_multiple=None,
                    activity_ids=cycle.activity_ids,
                    warning_codes=tuple(sorted(warnings)),
                )
            )
        return tuple(result)

    def _points(
        self,
        *,
        account_ref: str,
        currency: str,
        snapshots: tuple[AccountSnapshot, ...],
        flows: tuple[_Flow, ...],
        warnings: set[str],
    ) -> tuple[DailyEquityPoint, ...]:
        result: list[DailyEquityPoint] = []
        previous_at: datetime | None = None
        for index, snapshot in enumerate(snapshots):
            point_warnings = set(snapshot.warning_codes)
            if snapshot.degraded:
                point_warnings.add("SNAPSHOT_DEGRADED")
            if snapshot.net_assets is None:
                point_warnings.add("EQUITY_VALUE_UNAVAILABLE")
                warnings.add("EQUITY_VALUE_UNAVAILABLE")
            if snapshot.warning_codes or snapshot.degraded:
                warnings.add("SNAPSHOT_COVERAGE_INCOMPLETE")
            if previous_at is not None and snapshot.account_as_of == previous_at:
                point_warnings.add("DUPLICATE_VALUATION_TIMESTAMP")
                warnings.add("DUPLICATE_VALUATION_TIMESTAMP")
            previous_at = snapshot.account_as_of
            point_flows = tuple(
                flow
                for flow in flows
                if index > 0
                and snapshots[index - 1].account_as_of < flow.at <= snapshot.account_as_of
            )
            # Multiple transfers at one exact timestamp are a single known
            # boundary; transfers at different timestamps in one valuation
            # interval are flagged by _twr as not exactly boundary-covered.
            result.append(
                DailyEquityPoint(
                    account_ref=account_ref,
                    currency=currency,
                    valuation_at=snapshot.account_as_of,
                    market_session_date=snapshot.account_as_of.date(),
                    equity_value=snapshot.net_assets,
                    source_snapshot_id=snapshot.snapshot_id,
                    source_snapshot_as_of=snapshot.account_as_of,
                    source_fetched_at=snapshot.fetched_at,
                    coverage_status=(
                        PerformanceStatus.INCOMPLETE
                        if point_warnings
                        else PerformanceStatus.COMPLETE
                    ),
                    net_external_cash_flow_since_previous=sum(
                        (flow.amount for flow in point_flows), Decimal(0)
                    ),
                    external_flow_ids=tuple(flow.id for flow in point_flows),
                    warning_codes=tuple(sorted(point_warnings)),
                )
            )
        if any(item.equity_value is None for item in result):
            warnings.add("TWR_EQUITY_VALUE_UNAVAILABLE")
        return tuple(result)

    @staticmethod
    def _twr(
        points: tuple[DailyEquityPoint, ...], flows: tuple[_Flow, ...]
    ) -> tuple[
        Decimal | None,
        tuple[Decimal, ...],
        PerformanceComputationStatus,
        tuple[str, ...],
    ]:
        warnings: set[str] = set()
        if len(points) < 2:
            warnings.add("TWR_INSUFFICIENT_VALUATION_POINTS")
            return None, (), PerformanceComputationStatus.UNAVAILABLE, tuple(sorted(warnings))
        if any(point.equity_value is None for point in points):
            warnings.add("TWR_EQUITY_VALUE_UNAVAILABLE")
            return None, (), PerformanceComputationStatus.UNAVAILABLE, tuple(sorted(warnings))
        if any(
            points[index].valuation_at >= points[index + 1].valuation_at
            for index in range(len(points) - 1)
        ):
            warnings.add("TWR_CASH_FLOW_BOUNDARY_MISSING")
            return None, (), PerformanceComputationStatus.NOT_COMPUTABLE, tuple(sorted(warnings))

        # A transfer outside the observed valuation range has no before/after
        # valuation boundary.  Do not silently switch to Modified Dietz.
        if any(
            flow.at <= points[0].valuation_at or flow.at > points[-1].valuation_at
            for flow in flows
        ):
            warnings.add("TWR_CASH_FLOW_BOUNDARY_MISSING")
            return None, (), PerformanceComputationStatus.NOT_COMPUTABLE, tuple(sorted(warnings))

        index_values: list[Decimal] = [Decimal(1)]
        for point_index in range(1, len(points)):
            previous = points[point_index - 1].equity_value
            current = points[point_index].equity_value
            assert previous is not None and current is not None
            if previous == 0:
                warnings.add("TWR_ZERO_OPENING_EQUITY")
                return (
                    None,
                    (),
                    PerformanceComputationStatus.NOT_COMPUTABLE,
                    tuple(sorted(warnings)),
                )
            interval_flows = tuple(
                flow
                for flow in flows
                if (
                    points[point_index - 1].valuation_at
                    < flow.at
                    <= points[point_index].valuation_at
                )
            )
            distinct_times = {flow.at for flow in interval_flows}
            if len(distinct_times) > 1:
                warnings.add("TWR_CASH_FLOW_BOUNDARY_MISSING")
                return (
                    None,
                    (),
                    PerformanceComputationStatus.NOT_COMPUTABLE,
                    tuple(sorted(warnings)),
                )
            flow_total = sum((flow.amount for flow in interval_flows), Decimal(0))
            with localcontext() as context:
                context.prec = 40
                factor = (current - flow_total) / previous
                index_values.append(index_values[-1] * factor)
        return (
            index_values[-1] - Decimal(1),
            tuple(index_values),
            PerformanceComputationStatus.COMPUTED,
            tuple(sorted(warnings)),
        )

    @classmethod
    def _xirr(cls, points: tuple[DailyEquityPoint, ...], flows: tuple[_Flow, ...]) -> _XirrOutcome:
        warnings: set[str] = set()
        if len(points) < 2:
            warnings.add("XIRR_VALUATION_POINTS_UNAVAILABLE")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.UNAVAILABLE,
                tuple(sorted(warnings)),
            )
        first = points[0].equity_value
        last = points[-1].equity_value
        if first is None or last is None:
            warnings.add("XIRR_EQUITY_VALUE_UNAVAILABLE")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.UNAVAILABLE,
                tuple(sorted(warnings)),
            )
        if first == 0:
            warnings.add("XIRR_ZERO_OPENING_EQUITY")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(warnings)),
            )
        if points[-1].valuation_at <= points[0].valuation_at:
            warnings.add("XIRR_TIME_WINDOW_INVALID")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(warnings)),
            )
        if any(
            flow.at <= points[0].valuation_at or flow.at > points[-1].valuation_at
            for flow in flows
        ):
            warnings.add("XIRR_CASH_FLOW_BOUNDARY_MISSING")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(warnings)),
            )
        cashflows: list[tuple[float, Decimal]] = [
            (0.0, -first),
            *(
                (
                    (flow.at - points[0].valuation_at).total_seconds() / _SECONDS_PER_YEAR,
                    -flow.amount,
                )
                for flow in flows
            ),
            (
                (points[-1].valuation_at - points[0].valuation_at).total_seconds()
                / _SECONDS_PER_YEAR,
                last,
            ),
        ]
        # Aggregate same-time values. This is exact for a broker ledger and
        # makes root detection independent of input row order.
        by_year: dict[float, Decimal] = defaultdict(Decimal)
        for year, amount in cashflows:
            by_year[year] += amount
        cashflows = sorted(by_year.items())
        signs = {amount > 0 for _, amount in cashflows if amount != 0}
        if len(signs) < 2:
            warnings.add("XIRR_NO_SIGN_CHANGE")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(warnings)),
            )
        roots = cls._find_xirr_roots(tuple(cashflows))
        if not roots:
            warnings.add("XIRR_NO_ROOT")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(warnings)),
            )
        if len(roots) != 1:
            warnings.add("XIRR_MULTIPLE_ROOTS")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(warnings)),
            )
        try:
            return _XirrOutcome(
                Decimal(str(round(roots[0], 12))),
                PerformanceComputationStatus.COMPUTED,
                tuple(sorted(warnings)),
            )
        except (InvalidOperation, ValueError):
            warnings.add("XIRR_NON_CONVERGENT")
            return _XirrOutcome(
                None,
                PerformanceComputationStatus.NOT_COMPUTABLE,
                tuple(sorted(warnings)),
            )

    @staticmethod
    def _find_xirr_roots(cashflows: tuple[tuple[float, Decimal], ...]) -> tuple[float, ...]:
        """Find all useful XIRR roots on ``r > -1`` deterministically.

        Actual timestamps make this a non-polynomial equation.  A log-rate
        scan plus bisection is deliberately conservative: it returns no root
        rather than fabricating one when the numerical surface is not stable.
        Newton refinement from the scan points also catches an even-multiplicity
        root that does not cross zero.
        """

        try:
            values = tuple((float(year), float(amount)) for year, amount in cashflows)
        except (OverflowError, ValueError):
            return ()

        def npv_log_rate(log_rate: float) -> float:
            total = 0.0
            for year, amount in values:
                exponent = -year * log_rate
                if exponent > 709:
                    return math.copysign(math.inf, amount) if amount else math.inf
                if exponent < -745:
                    continue
                total += amount * math.exp(exponent)
            return total

        def derivative(log_rate: float) -> float:
            total = 0.0
            for year, amount in values:
                exponent = -year * log_rate
                if exponent > 709:
                    return math.copysign(math.inf, -year * amount) if amount else math.inf
                if exponent < -745:
                    continue
                total += -year * amount * math.exp(exponent)
            return total

        # Keep the domain strictly above -1 while spanning rates from almost
        # -100% through very large annualized returns.
        grid = [
            -40.0 + index * 0.25
            for index in range(321)
        ]
        values_on_grid = [npv_log_rate(value) for value in grid]
        roots: list[float] = []

        def add_root(log_rate: float) -> None:
            if not math.isfinite(log_rate):
                return
            rate = math.expm1(log_rate)
            if not math.isfinite(rate) or rate <= -1.0:
                return
            if not any(
                abs(rate - existing)
                <= 1e-8 * max(1.0, abs(rate), abs(existing))
                for existing in roots
            ):
                roots.append(rate)

        for index, value in enumerate(values_on_grid):
            if math.isfinite(value) and abs(value) <= 1e-9:
                add_root(grid[index])
            if index == 0:
                continue
            left_value = values_on_grid[index - 1]
            if not math.isfinite(left_value) or not math.isfinite(value):
                continue
            if left_value * value > 0:
                continue
            left, right = grid[index - 1], grid[index]
            left_npv = left_value
            for _ in range(180):
                middle = (left + right) / 2.0
                middle_npv = npv_log_rate(middle)
                if not math.isfinite(middle_npv):
                    break
                if abs(middle_npv) <= 1e-10:
                    left = right = middle
                    break
                if left_npv * middle_npv <= 0:
                    right = middle
                else:
                    left, left_npv = middle, middle_npv
            add_root((left + right) / 2.0)

        # A tangential root has no sign change. Newton starts at each grid
        # point and is accepted only after a direct residual check.
        for starting in grid[::2]:
            current = starting
            for _ in range(80):
                value = npv_log_rate(current)
                slope = derivative(current)
                if not math.isfinite(value) or not math.isfinite(slope) or slope == 0:
                    break
                step = value / slope
                if abs(step) > 4:
                    step = math.copysign(4, step)
                candidate = current - step
                if candidate < -40 or candidate > 40:
                    break
                current = candidate
                if abs(value) <= 1e-9:
                    break
            if math.isfinite(current) and abs(npv_log_rate(current)) <= 1e-7:
                add_root(current)
        roots.sort()
        return tuple(roots)

    @staticmethod
    def _drawdown(
        *,
        twr_index: tuple[Decimal, ...],
        points: tuple[DailyEquityPoint, ...],
        twr_status: PerformanceComputationStatus,
    ) -> tuple[Decimal | None, PerformanceComputationStatus, tuple[str, ...]]:
        warnings: set[str] = set()
        if twr_status is not PerformanceComputationStatus.COMPUTED or not twr_index:
            warnings.add("DRAWDOWN_TWR_INDEX_UNAVAILABLE")
            return None, PerformanceComputationStatus.UNAVAILABLE, tuple(sorted(warnings))
        if len(twr_index) != len(points):
            warnings.add("DRAWDOWN_TWR_INDEX_UNAVAILABLE")
            return None, PerformanceComputationStatus.UNAVAILABLE, tuple(sorted(warnings))
        peak = twr_index[0]
        if peak == 0:
            warnings.add("DRAWDOWN_ZERO_TWR_INDEX")
            return None, PerformanceComputationStatus.NOT_COMPUTABLE, tuple(sorted(warnings))
        maximum = Decimal(0)
        for value in twr_index:
            if value > peak:
                peak = value
            if peak == 0:
                warnings.add("DRAWDOWN_ZERO_TWR_INDEX")
                return None, PerformanceComputationStatus.NOT_COMPUTABLE, tuple(sorted(warnings))
            drawdown = value / peak - Decimal(1)
            if drawdown < maximum:
                maximum = drawdown
        return maximum, PerformanceComputationStatus.COMPUTED, ()

    @staticmethod
    def _period(
        *,
        points: tuple[DailyEquityPoint, ...],
        flows: tuple[_Flow, ...],
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[datetime, datetime]:
        values: list[datetime] = [item.valuation_at for item in points]
        values.extend(item.at for item in flows)
        if start is not None:
            values.append(start)
        if end is not None:
            values.append(end)
        if not values:
            # A timezone-aware epoch keeps the immutable model valid for an
            # empty requested group; the series remains INCOMPLETE.
            from datetime import UTC

            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            return epoch, epoch
        return min(values), max(values)


# Compatibility spelling for callers that call this a returns calculator.
PerformanceReturnsCalculator = PerformanceCalculator
PerformanceSeriesCalculator = PerformanceCalculator

__all__ = [
    "PerformanceCalculator",
    "PerformanceReturnsCalculator",
    "PerformanceSeriesCalculator",
]
