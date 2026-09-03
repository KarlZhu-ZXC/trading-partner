from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.services.trade_cycle_calculator import TradeCycleCalculator
from domain.attribution.models import PositionBasisCheckpoint
from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountTransactionKind,
    AccountTransactionSide,
    TradeCycleClassification,
    TradeCycleQuality,
    TradeCycleStatus,
)
from domain.portfolio.models import AccountTransaction

T0 = datetime(2026, 8, 1, 14, tzinfo=UTC)


def _trade(
    identity: str,
    side: AccountTransactionSide,
    quantity: str,
    price: str | None,
    fees: str | None,
    *,
    day: int,
    account: str = "account_a",
    instrument: str = "equity:US:ABC",
    currency: str = "USD",
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=identity,
        account_ref=account,
        provider=VendorId.SCHWAB,
        instrument_id=instrument,
        kind=AccountTransactionKind.TRADE,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price) if price is not None else None,
        fees=Decimal(fees) if fees is not None else None,
        currency=currency,
        occurred_at=T0 + timedelta(days=day),
    )


def _calculate(*items: AccountTransaction):
    return TradeCycleCalculator().calculate(
        transactions=tuple(items),
        as_of=T0 + timedelta(days=30),
        coverage_status=AccountActivityCoverageStatus.COMPLETE,
    )


def test_groups_scale_in_reduce_close_and_reentry_with_fifo_pnl() -> None:
    values = (
        _trade("sell-close", AccountTransactionSide.SELL, "7", "130", ".7", day=4),
        _trade("buy-open", AccountTransactionSide.BUY, "10", "100", "1", day=1),
        _trade("sell-reduce", AccountTransactionSide.SELL, "8", "120", ".8", day=3),
        _trade("buy-add", AccountTransactionSide.BUY, "5", "110", ".5", day=2),
        _trade("buy-reentry", AccountTransactionSide.BUY, "2", "140", ".2", day=5),
        _trade("sell-reentry", AccountTransactionSide.SELL, "2", "150", ".2", day=6),
    )

    result = _calculate(*values)

    assert len(result.cycles) == 2
    newest, first = result.cycles
    assert first.status is TradeCycleStatus.CLOSED
    assert first.classification is TradeCycleClassification.UNCLASSIFIED
    assert first.activity_ids == ("buy-open", "buy-add", "sell-reduce", "sell-close")
    assert first.opening_count == 1
    assert first.add_count == 1
    assert first.reduce_count == 2
    assert first.ending_quantity == 0
    assert first.gross_realized_pnl == Decimal("320")
    assert first.net_realized_pnl == Decimal("317.0")
    assert first.maximum_deployed_capital == Decimal("1550")
    assert newest.reentry_of_cycle_id == first.cycle_id
    assert newest.gross_realized_pnl == Decimal("20")
    assert newest.net_realized_pnl == Decimal("19.6")
    assert result.status is TradeCycleQuality.COMPLETE


def test_keeps_open_cycles_separate_by_account_and_currency() -> None:
    result = _calculate(
        _trade("usd-a", AccountTransactionSide.BUY, "3", "10", "0", day=1),
        _trade(
            "usd-b",
            AccountTransactionSide.BUY,
            "4",
            "11",
            "0",
            day=2,
            account="account_b",
        ),
        _trade(
            "cad-a",
            AccountTransactionSide.BUY,
            "5",
            "12",
            "0",
            day=3,
            currency="CAD",
        ),
    )

    assert len(result.cycles) == 3
    assert all(item.status is TradeCycleStatus.OPEN for item in result.cycles)
    assert {(item.account_ref, item.currency) for item in result.cycles} == {
        ("account_a", "USD"),
        ("account_b", "USD"),
        ("account_a", "CAD"),
    }


def test_missing_fee_preserves_gross_but_hides_net() -> None:
    result = _calculate(
        _trade("buy", AccountTransactionSide.BUY, "2", "100", None, day=1),
        _trade("sell", AccountTransactionSide.SELL, "2", "110", "0", day=2),
    )

    cycle = result.cycles[0]
    assert cycle.status is TradeCycleStatus.CLOSED
    assert cycle.gross_realized_pnl == Decimal("20")
    assert cycle.net_realized_pnl is None
    assert cycle.quality is TradeCycleQuality.INCOMPLETE
    assert "TRANSACTION_FEES_UNAVAILABLE" in cycle.warning_codes


def test_missing_price_and_oversell_fail_closed_without_short_cycle() -> None:
    missing = _calculate(
        _trade("buy-no-price", AccountTransactionSide.BUY, "2", None, "0", day=1),
        _trade("sell", AccountTransactionSide.SELL, "2", "110", "0", day=2),
    ).cycles[0]
    assert missing.status is TradeCycleStatus.UNRESOLVED
    assert missing.gross_realized_pnl is None
    assert "TRADE_PRICE_UNAVAILABLE" in missing.warning_codes

    oversold = _calculate(
        _trade("buy", AccountTransactionSide.BUY, "2", "100", "0", day=1),
        _trade("sell-too-much", AccountTransactionSide.SELL, "3", "110", "0", day=2),
    ).cycles[0]
    assert oversold.status is TradeCycleStatus.UNRESOLVED
    assert oversold.ending_quantity == 0
    assert oversold.gross_realized_pnl is None
    assert oversold.net_realized_pnl is None
    assert "OVERSELL_SHORT_UNSUPPORTED" in oversold.warning_codes


def test_sell_without_open_and_non_trade_activity_are_not_fabricated() -> None:
    dividend = AccountTransaction(
        provider_transaction_id="dividend",
        account_ref="account_a",
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:ABC",
        kind=AccountTransactionKind.DIVIDEND,
        side=None,
        quantity=None,
        price=None,
        fees=None,
        currency="USD",
        occurred_at=T0,
        cash_amount=Decimal("5"),
    )
    result = _calculate(
        dividend,
        _trade("orphan-sell", AccountTransactionSide.SELL, "1", "100", "0", day=1),
    )

    assert len(result.cycles) == 1
    assert result.cycles[0].activity_ids == ("orphan-sell",)
    assert result.cycles[0].status is TradeCycleStatus.UNRESOLVED
    assert result.cycles[0].gross_realized_pnl is None
    assert "SELL_WITHOUT_OPEN_LONG" in result.cycles[0].warning_codes


def test_start_filter_limit_and_cycle_ids_are_deterministic() -> None:
    values = (
        _trade("buy-1", AccountTransactionSide.BUY, "1", "10", "0", day=1),
        _trade("sell-1", AccountTransactionSide.SELL, "1", "11", "0", day=2),
        _trade("buy-2", AccountTransactionSide.BUY, "1", "12", "0", day=3),
        _trade("sell-2", AccountTransactionSide.SELL, "1", "13", "0", day=4),
    )
    calculator = TradeCycleCalculator()
    first = calculator.calculate(
        transactions=values,
        as_of=T0 + timedelta(days=10),
        coverage_status=AccountActivityCoverageStatus.COMPLETE,
        start=T0 + timedelta(days=3),
        limit=1,
    )
    second = calculator.calculate(
        transactions=tuple(reversed(values)),
        as_of=T0 + timedelta(days=10),
        coverage_status=AccountActivityCoverageStatus.COMPLETE,
        start=T0 + timedelta(days=3),
        limit=1,
    )

    assert tuple(item.cycle_id for item in first.cycles) == tuple(
        item.cycle_id for item in second.cycles
    )
    assert "TRADE_CYCLE_RESULTS_TRUNCATED" not in first.warning_codes
    assert len(first.cycles) == 1

    truncated = calculator.calculate(
        transactions=values,
        as_of=T0 + timedelta(days=10),
        coverage_status=AccountActivityCoverageStatus.COMPLETE,
        limit=1,
    )
    assert len(truncated.cycles) == 1
    assert "TRADE_CYCLE_RESULTS_TRUNCATED" in truncated.warning_codes


def test_sgov_cycle_is_cash_management_not_active_trade() -> None:
    result = _calculate(
        _trade(
            "sgov-buy",
            AccountTransactionSide.BUY,
            "10",
            "100",
            "0",
            day=1,
            instrument="etf:US:SGOV",
        )
    )

    assert result.cycles[0].classification is TradeCycleClassification.CASH_MANAGEMENT


def test_basis_checkpoint_rebases_open_cycle_without_counting_import_as_trade() -> None:
    checkpoint = PositionBasisCheckpoint(
        checkpoint_id="basis_1",
        provider=VendorId.SCHWAB,
        account_ref="account_a",
        instrument_id="equity:US:ABC",
        currency="USD",
        effective_at=T0 + timedelta(days=2),
        quantity=Decimal("10"),
        total_cost_basis=Decimal("900"),
        source_type="BROKER_POSITION_IMPORT",
        source_ref="test_import",
        replaces_activity_id="import",
    )
    result = TradeCycleCalculator((checkpoint,)).calculate(
        transactions=(
            _trade("open", AccountTransactionSide.BUY, "3", "100", "0", day=1),
            _trade("import", AccountTransactionSide.BUY, "10", "50", "0", day=2),
            _trade("sell", AccountTransactionSide.SELL, "4", "120", "4", day=3),
        ),
        as_of=T0 + timedelta(days=30),
        coverage_status=AccountActivityCoverageStatus.COMPLETE,
    )

    cycle = result.cycles[0]
    assert cycle.activity_ids == ("open", "sell")
    assert cycle.add_count == 0
    assert cycle.ending_quantity == Decimal("6")
    assert cycle.gross_realized_pnl == Decimal("120")
    assert cycle.net_realized_pnl == Decimal("116")
    assert cycle.maximum_deployed_capital == Decimal("900")
