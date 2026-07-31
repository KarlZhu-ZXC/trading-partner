from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.services.performance_attribution_calculator import (
    PerformanceAttributionCalculator,
)
from domain.attribution.enums import AttributionStatus, CostBasisMethod
from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountPosition, AccountSnapshot, AccountTransaction

NOW = datetime(2026, 8, 1, 20, tzinfo=UTC)
START = NOW - timedelta(days=10)


def _trade(
    event_id: str,
    *,
    side: AccountTransactionSide,
    quantity: str,
    price: str,
    fees: str | None,
    occurred_at: datetime,
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=event_id,
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:NVDA",
        kind=AccountTransactionKind.TRADE,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees) if fees is not None else None,
        currency="USD",
        occurred_at=occurred_at,
        source_type="TRADE",
        mapping_version="test_v1",
    )


def _snapshot(
    *, side: AccountPositionSide, quantity: str, market_price: str
) -> AccountSnapshot:
    return AccountSnapshot(
        snapshot_id="snapshot_1",
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        environment=AccountEnvironment.REAL,
        base_currency="USD",
        account_as_of=NOW,
        fetched_at=NOW,
        cash=Decimal("1000"),
        buying_power=None,
        net_assets=Decimal("10000"),
        margin_used=None,
        positions=(
            AccountPosition(
                instrument_id="equity:US:NVDA",
                side=side,
                quantity=Decimal(quantity),
                sellable_quantity=None,
                average_cost=Decimal("100"),
                diluted_cost=None,
                market_price=Decimal(market_price),
                market_price_at=NOW,
                market_value=None,
                unrealized_pnl=Decimal("123"),
                realized_pnl=None,
                currency="USD",
            ),
        ),
        open_orders=(),
        degraded=False,
        warning_codes=(),
    )


def test_fifo_long_realized_net_and_unrealized_are_auditable() -> None:
    result = PerformanceAttributionCalculator().calculate_account(
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        currency="USD",
        transactions=(
            _trade(
                "buy_1",
                side=AccountTransactionSide.BUY,
                quantity="10",
                price="100",
                fees="10",
                occurred_at=START - timedelta(days=1),
            ),
            _trade(
                "sell_1",
                side=AccountTransactionSide.SELL,
                quantity="4",
                price="120",
                fees="4",
                occurred_at=START + timedelta(days=1),
            ),
        ),
        snapshot=_snapshot(side=AccountPositionSide.LONG, quantity="6", market_price="130"),
        start=START,
        end=NOW,
        method=CostBasisMethod.FIFO,
        opening_history_verified=True,
    )

    instrument = result.instruments[0]
    assert result.status is AttributionStatus.COMPLETE
    assert instrument.realized_pnl_before_fees == Decimal("80")
    assert instrument.realized_pnl_after_fees == Decimal("72")
    assert instrument.unrealized_pnl_before_fees == Decimal("180")
    assert instrument.open_cost_basis == Decimal("600")
    assert instrument.activity_ids == ("sell_1",)


def test_fifo_short_cover_and_missing_fees_remain_explicit() -> None:
    result = PerformanceAttributionCalculator().calculate_account(
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        currency="USD",
        transactions=(
            _trade(
                "short_1",
                side=AccountTransactionSide.SELL,
                quantity="5",
                price="100",
                fees="5",
                occurred_at=START - timedelta(days=1),
            ),
            _trade(
                "cover_1",
                side=AccountTransactionSide.BUY,
                quantity="2",
                price="80",
                fees=None,
                occurred_at=START + timedelta(days=1),
            ),
        ),
        snapshot=_snapshot(side=AccountPositionSide.SHORT, quantity="3", market_price="90"),
        start=START,
        end=NOW,
        method=CostBasisMethod.FIFO,
        opening_history_verified=True,
    )

    instrument = result.instruments[0]
    assert result.status is AttributionStatus.INCOMPLETE
    assert instrument.ending_quantity == Decimal("-3")
    assert instrument.realized_pnl_before_fees == Decimal("40")
    assert instrument.realized_pnl_after_fees is None
    assert instrument.unrealized_pnl_before_fees == Decimal("30")
    assert "TRANSACTION_FEES_UNAVAILABLE" in instrument.warning_codes


def test_broker_reported_basis_never_relabels_position_pnl_as_period_realized() -> None:
    result = PerformanceAttributionCalculator().calculate_account(
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        currency="USD",
        transactions=(),
        snapshot=_snapshot(side=AccountPositionSide.LONG, quantity="6", market_price="130"),
        start=START,
        end=NOW,
        method=CostBasisMethod.BROKER_REPORTED,
        opening_history_verified=False,
    )

    instrument = result.instruments[0]
    assert instrument.realized_pnl_before_fees is None
    assert instrument.broker_reported_unrealized_pnl == Decimal("123")
    assert "BROKER_REPORTED_REALIZED_PERIOD_UNVERIFIED" in instrument.warning_codes
