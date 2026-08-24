from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.dto.performance import (
    CyclePerformanceDTO,
    DailyEquityPointDTO,
    PerformanceSeriesDTO,
)
from application.services.performance_calculator import PerformanceCalculator
from domain.common.enums import VendorId
from domain.performance.enums import PerformanceComputationStatus, PerformanceStatus
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountTransactionKind,
    AccountTransactionSide,
    TradeCycleClassification,
    TradeCycleQuality,
    TradeCycleStatus,
)
from domain.portfolio.models import AccountSnapshot, AccountTransaction, TradeCycle

ORIGIN = datetime(2025, 1, 1, tzinfo=UTC)


def _snapshot(
    snapshot_id: str,
    days: int,
    value: str | None,
    *,
    account_ref: str = "account_1",
    currency: str = "USD",
    degraded: bool = False,
    warning_codes: tuple[str, ...] = (),
) -> AccountSnapshot:
    timestamp = ORIGIN + timedelta(days=days)
    return AccountSnapshot(
        snapshot_id=snapshot_id,
        account_ref=account_ref,
        provider=VendorId.SCHWAB,
        environment=AccountEnvironment.REAL,
        base_currency=currency,
        account_as_of=timestamp,
        fetched_at=timestamp,
        cash=Decimal("1"),
        buying_power=None,
        net_assets=Decimal(value) if value is not None else None,
        margin_used=None,
        positions=(),
        open_orders=(),
        degraded=degraded,
        warning_codes=warning_codes,
    )


def _transfer(
    transaction_id: str,
    days: int,
    amount: str,
    *,
    account_ref: str = "account_1",
    currency: str = "USD",
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=transaction_id,
        account_ref=account_ref,
        provider=VendorId.SCHWAB,
        instrument_id=None,
        kind=AccountTransactionKind.TRANSFER,
        side=None,
        quantity=None,
        price=None,
        fees=None,
        currency=currency,
        occurred_at=ORIGIN + timedelta(days=days),
        cash_amount=Decimal(amount),
    )


def _cash_activity(
    transaction_id: str,
    days: int,
    kind: AccountTransactionKind,
    amount: str | None,
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=transaction_id,
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        instrument_id=None,
        kind=kind,
        side=None,
        quantity=None,
        price=None,
        fees=None,
        currency="USD",
        occurred_at=ORIGIN + timedelta(days=days),
        cash_amount=Decimal(amount) if amount is not None else None,
    )


def _trade(
    transaction_id: str,
    days: int,
    fees: str | None,
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=transaction_id,
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:NVDA",
        kind=AccountTransactionKind.TRADE,
        side=AccountTransactionSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        fees=Decimal(fees) if fees is not None else None,
        currency="USD",
        occurred_at=ORIGIN + timedelta(days=days),
    )


def _cycle(
    cycle_id: str,
    *,
    status: TradeCycleStatus,
    quality: TradeCycleQuality = TradeCycleQuality.COMPLETE,
    net_realized_pnl: str | None = "30",
    maximum_deployed_capital: str | None = "100",
) -> TradeCycle:
    opened_at = ORIGIN
    closed_at = ORIGIN + timedelta(days=10) if status is TradeCycleStatus.CLOSED else None
    return TradeCycle(
        cycle_id=cycle_id,
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:NVDA",
        currency="USD",
        activity_ids=(f"{cycle_id}_buy", f"{cycle_id}_sell"),
        opened_at=opened_at,
        closed_at=closed_at,
        status=status,
        classification=TradeCycleClassification.UNCLASSIFIED,
        opening_count=1,
        add_count=0,
        reduce_count=1,
        ending_quantity=Decimal("0") if closed_at is not None else Decimal("1"),
        gross_realized_pnl=(
            Decimal("30") if net_realized_pnl is not None else None
        ),
        net_realized_pnl=(
            Decimal(net_realized_pnl) if net_realized_pnl is not None else None
        ),
        maximum_deployed_capital=(
            Decimal(maximum_deployed_capital)
            if maximum_deployed_capital is not None
            else None
        ),
        holding_duration_seconds=10 * 24 * 60 * 60,
        reentry_of_cycle_id=None,
        quality=quality,
        warning_codes=(),
    )


def test_no_flow_twr_xirr_and_drawdown_use_net_assets_only() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(
            _snapshot("s2", 2, "90"),
            _snapshot("s0", 0, "100"),
            _snapshot("s1", 1, "120"),
        )
    )

    assert result.twr == Decimal("-0.1")
    assert result.xirr is not None and result.xirr < Decimal("0")
    assert result.maximum_drawdown == Decimal("-0.25")
    assert result.twr_index == (Decimal("1"), Decimal("1.2"), Decimal("0.9"))
    assert result.input_snapshot_ids == ("s0", "s1", "s2")
    assert result.input_snapshot_times == tuple(item.valuation_at for item in result.points)
    assert result.status is PerformanceStatus.COMPLETE
    assert result.warning_codes == ()


def test_transfer_is_excluded_from_twr_but_retained_on_point_and_mwr() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(
            _snapshot("s0", 0, "100"),
            _snapshot("s1", 10, "100"),
            _snapshot("s2", 20, "220"),
        ),
        transactions=(_transfer("deposit", 15, "100"),),
    )

    assert result.twr == Decimal("0.2")
    assert result.points[2].net_external_cash_flow_since_previous == Decimal("100")
    assert result.points[2].external_flow_ids == ("deposit",)
    assert result.xirr is not None and result.xirr > Decimal("1")
    assert result.input_activity_ids == ("deposit",)


def test_missing_cash_flow_boundary_withholds_twr_without_modified_dietz() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "220")),
        transactions=(_transfer("deposit", 0, "100"),),
    )

    assert result.twr is None
    assert result.twr_status is PerformanceComputationStatus.NOT_COMPUTABLE
    assert "TWR_CASH_FLOW_BOUNDARY_MISSING" in result.warning_codes


def test_missing_net_assets_is_not_reconstructed_from_cash_or_positions() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 1, None))
    )

    assert result.points[1].equity_value is None
    assert result.twr is None
    assert result.xirr is None
    assert "EQUITY_VALUE_UNAVAILABLE" in result.warning_codes
    assert result.status is PerformanceStatus.INCOMPLETE


def test_xirr_multiple_roots_is_not_compressed_to_one_answer() -> None:
    # Investor cash flows are -100, +230, -132 at t=0, 1, 2 years.
    # NPV has two roots (10% and 20%).
    result = PerformanceCalculator().calculate_series(
        snapshots=(
            _snapshot("s0", 0, "100"),
            _snapshot("s1", 365, "230"),
            _snapshot("s2", 730, "0"),
        ),
        transactions=(_transfer("withdraw", 365, "-230"), _transfer("deposit", 730, "132")),
    )

    assert result.xirr is None
    assert result.xirr_status is PerformanceComputationStatus.NOT_COMPUTABLE
    assert "XIRR_MULTIPLE_ROOTS" in result.warning_codes


def test_xirr_no_sign_change_is_not_computed() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 365, "0")),
        transactions=(_transfer("deposit", 100, "20"),),
    )

    assert result.xirr is None
    assert result.xirr_status is PerformanceComputationStatus.NOT_COMPUTABLE
    assert "XIRR_NO_SIGN_CHANGE" in result.warning_codes


def test_each_account_and_currency_has_its_own_series_in_stable_order() -> None:
    snapshots = (
        _snapshot("eur_late", 1, "210", account_ref="account_2", currency="EUR"),
        _snapshot("usd_late", 1, "110"),
        _snapshot("eur_early", 0, "200", account_ref="account_2", currency="EUR"),
        _snapshot("usd_early", 0, "100"),
    )
    values = PerformanceCalculator().calculate_all(snapshots=snapshots)

    assert tuple((item.account_ref, item.currency) for item in values) == (
        ("account_1", "USD"),
        ("account_2", "EUR"),
    )
    assert values[0].twr == Decimal("0.1")
    assert values[1].twr == Decimal("0.05")


def test_non_transfer_activity_never_becomes_external_flow() -> None:
    dividend = AccountTransaction(
        provider_transaction_id="dividend",
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        instrument_id=None,
        kind=AccountTransactionKind.DIVIDEND,
        side=None,
        quantity=None,
        price=None,
        fees=None,
        currency="USD",
        occurred_at=ORIGIN + timedelta(days=5),
        cash_amount=Decimal("25"),
    )
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "125")),
        transactions=(dividend,),
    )

    assert result.points[1].net_external_cash_flow_since_previous == Decimal("0")
    assert result.twr == Decimal("0.25")
    assert result.warning_codes == ()


def test_dto_round_trip_keeps_source_coverage_and_algorithm_version() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 1, "101"))
    )
    point = DailyEquityPointDTO.from_domain(result.points[0])
    wire = PerformanceSeriesDTO.from_domain(result)

    assert point.source_snapshot_id == "s0"
    assert wire.input_snapshot_ids == ("s0", "s1")
    assert wire.algorithm_version == "performance_returns_v1"


def test_income_return_and_fee_drag_use_native_opening_capital_base() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "107")),
        transactions=(
            _cash_activity("dividend", 2, AccountTransactionKind.DIVIDEND, "5"),
            _cash_activity("interest", 3, AccountTransactionKind.INTEREST, "2"),
            _cash_activity("fee", 4, AccountTransactionKind.FEE, "-3"),
        ),
    )

    assert result.capital_base == Decimal("100")
    assert result.dividends == Decimal("5")
    assert result.interest == Decimal("2")
    assert result.known_fees == Decimal("3")
    assert result.income_return == Decimal("0.07")
    assert result.fee_drag == Decimal("0.03")
    assert result.warning_codes == ()


def test_missing_trade_fee_keeps_fee_drag_unavailable_but_income_explicit() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "100")),
        transactions=(_trade("buy", 2, None),),
    )

    assert result.income_return == Decimal("0")
    assert result.known_fees is None
    assert result.fee_drag is None
    assert result.fee_drag_status is PerformanceComputationStatus.UNAVAILABLE
    assert "FEE_ACTIVITY_AMOUNT_UNAVAILABLE" in result.warning_codes


def test_missing_capital_base_does_not_infer_income_or_fee_ratios() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, None), _snapshot("s1", 10, "100")),
        transactions=(_cash_activity("dividend", 2, AccountTransactionKind.DIVIDEND, "5"),),
    )

    assert result.capital_base is None
    assert result.income_return is None
    assert result.fee_drag is None
    assert "INCOME_RETURN_CAPITAL_BASE_UNAVAILABLE" in result.warning_codes
    assert "FEE_DRAG_CAPITAL_BASE_UNAVAILABLE" in result.warning_codes


def test_incomplete_activity_coverage_withholds_income_and_fee_totals() -> None:
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "107")),
        transactions=(_cash_activity("dividend", 2, AccountTransactionKind.DIVIDEND, "5"),),
        coverage_status=PerformanceStatus.INCOMPLETE,
    )

    assert result.dividends is None
    assert result.known_fees is None
    assert result.income_return is None
    assert result.fee_drag is None
    assert result.twr is None
    assert result.xirr is None
    assert "INCOME_ACTIVITY_COVERAGE_INCOMPLETE" in result.warning_codes
    assert "FEE_ACTIVITY_COVERAGE_INCOMPLETE" in result.warning_codes


def test_closed_cycle_return_is_proven_but_r_multiple_and_open_unrealized_are_not() -> None:
    closed = _cycle("closed", status=TradeCycleStatus.CLOSED)
    opened = _cycle(
        "open",
        status=TradeCycleStatus.OPEN,
        net_realized_pnl=None,
        maximum_deployed_capital="100",
    )
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "107")),
        cycles=(opened, closed),
    )

    assert result.input_cycle_ids == ("closed", "open")
    closed_result, open_result = result.cycle_performance
    assert closed_result.cycle_return == Decimal("0.3")
    assert closed_result.r_multiple is None
    assert "CYCLE_PLANNED_RISK_UNAVAILABLE" in closed_result.warning_codes
    assert open_result.cycle_return is None
    assert open_result.remaining_unrealized_pnl is None
    assert "CYCLE_UNREALIZED_PNL_UNAVAILABLE" in open_result.warning_codes


def test_cycle_with_incomplete_pnl_does_not_return_a_partial_cycle_return() -> None:
    cycle = _cycle(
        "incomplete",
        status=TradeCycleStatus.CLOSED,
        quality=TradeCycleQuality.INCOMPLETE,
        net_realized_pnl=None,
    )
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "100")),
        cycles=(cycle,),
    )

    item = result.cycle_performance[0]
    assert item.cycle_return is None
    assert "CYCLE_COVERAGE_INCOMPLETE" in item.warning_codes
    assert result.status is PerformanceStatus.INCOMPLETE


def test_cycle_dto_preserves_unavailable_r_multiple_and_source_ids() -> None:
    cycle = _cycle("closed", status=TradeCycleStatus.CLOSED)
    result = PerformanceCalculator().calculate_series(
        snapshots=(_snapshot("s0", 0, "100"), _snapshot("s1", 10, "107")),
        cycles=(cycle,),
    )
    dto = PerformanceSeriesDTO.from_domain(result)
    cycle_dto = CyclePerformanceDTO.from_domain(result.cycle_performance[0])

    assert dto.input_cycle_ids == ("closed",)
    assert dto.cycle_performance[0].cycle_return == Decimal("0.3")
    assert cycle_dto.r_multiple is None
