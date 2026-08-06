from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from application.dto.portfolio import AccountGetPositionsInput
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
    AccountPositionSide,
)
from domain.portfolio.models import AccountOpenOrder, AccountPosition, AccountSnapshot

_NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Ids:
    def new(self, _prefix: object) -> str:
        return "req_portfolio_test"


class _Redactor:
    def redact_text(self, value: str) -> str:
        return value


def test_positions_mapping_preserves_account_snapshot_context() -> None:
    position = AccountPosition(
        instrument_id="equity:US:NVDA",
        side=AccountPositionSide.LONG,
        quantity=Decimal("2"),
        sellable_quantity=Decimal("2"),
        average_cost=Decimal("100"),
        diluted_cost=None,
        market_price=Decimal("120"),
        market_price_at=_NOW,
        market_value=Decimal("240"),
        unrealized_pnl=Decimal("40"),
        realized_pnl=None,
        currency="USD",
    )
    order = AccountOpenOrder(
        provider_order_id="order-1",
        instrument_id="equity:US:NVDA",
        side=AccountOpenOrderSide.BUY,
        status=AccountOpenOrderStatus.PENDING,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        limit_price=Decimal("115"),
        submitted_at=_NOW,
    )
    snapshot = AccountSnapshot(
        snapshot_id="snapshot-1",
        account_ref="acct-1",
        provider=VendorId.SCHWAB,
        environment=AccountEnvironment.REAL,
        base_currency="USD",
        account_as_of=_NOW,
        fetched_at=_NOW,
        cash=Decimal("500"),
        buying_power=Decimal("450"),
        net_assets=Decimal("740"),
        margin_used=Decimal("20"),
        positions=(position,),
        open_orders=(order,),
        degraded=True,
        warning_codes=("SCHWAB_OPEN_ORDERS_NOT_INGESTED",),
    )
    account_service = SimpleNamespace(get_snapshots=lambda _ids: (snapshot,))
    coordinator = PortfolioToolCoordinator(
        account_service=account_service,  # type: ignore[arg-type]
        portfolio_service=SimpleNamespace(),  # type: ignore[arg-type]
        clock=_Clock(),
        id_generator=_Ids(),
        secret_redactor=_Redactor(),  # type: ignore[arg-type]
    )

    result = coordinator.get_account_positions(AccountGetPositionsInput())

    assert result.ok is True
    assert result.data is not None
    account = result.data.accounts[0]
    assert account.environment == AccountEnvironment.REAL
    assert account.base_currency == "USD"
    assert account.fetched_at == _NOW
    assert account.cash == Decimal("500")
    assert account.buying_power == Decimal("450")
    assert account.net_assets == Decimal("740")
    assert account.margin_used == Decimal("20")
    assert len(account.open_orders) == 1
    assert account.open_orders[0].provider_order_id == "order-1"
    assert account.degraded is True
    assert account.warning_codes == ("SCHWAB_OPEN_ORDERS_NOT_INGESTED",)
