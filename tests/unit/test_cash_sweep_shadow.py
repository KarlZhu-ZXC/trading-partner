from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from application.dto.broker_execution import BrokerOrderPreviewInput
from application.services.cash_sweep_shadow_service import CashSweepShadowService
from domain.common.enums import VendorId
from domain.execution.models import BrokerQuoteObservation
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
)
from domain.portfolio.models import AccountOpenOrder, AccountSnapshot

_NOW = datetime(2026, 8, 9, 13, 30, tzinfo=UTC)


class _Accounts:
    def __init__(self, snapshots: tuple[AccountSnapshot, ...]) -> None:
        self._snapshots = snapshots

    def latest_accounts(self) -> tuple[AccountSnapshot, ...]:
        return self._snapshots


class _QuoteProvider:
    def __init__(self, quote: BrokerQuoteObservation) -> None:
        self.quote = quote

    async def get_quote(
        self, *, instrument_id: str, as_of: datetime
    ) -> BrokerQuoteObservation:
        assert instrument_id == "etf:US:SGOV"
        assert as_of == _NOW
        return self.quote


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _snapshot(
    account_ref: str,
    cash: str,
    *,
    open_orders: tuple[AccountOpenOrder, ...] = (),
    margin: Decimal | None = Decimal(0),
    warnings: tuple[str, ...] = (),
) -> AccountSnapshot:
    return AccountSnapshot(
        snapshot_id=f"snapshot_{account_ref}",
        account_ref=account_ref,
        provider=VendorId.SCHWAB,
        environment=AccountEnvironment.REAL,
        base_currency="USD",
        account_as_of=_NOW - timedelta(seconds=2),
        fetched_at=_NOW - timedelta(seconds=1),
        cash=Decimal(cash),
        buying_power=Decimal("999999"),
        net_assets=Decimal("1000000"),
        margin_used=margin,
        positions=(),
        open_orders=open_orders,
        degraded=bool(warnings),
        warning_codes=warnings,
    )


def _order(side: AccountOpenOrderSide, price: str, quantity: str) -> AccountOpenOrder:
    return AccountOpenOrder(
        provider_order_id=f"order_{side.value}",
        instrument_id="etf:US:SGOV",
        side=side,
        status=AccountOpenOrderStatus.PENDING,
        quantity=Decimal(quantity),
        filled_quantity=Decimal(0),
        limit_price=Decimal(price),
        submitted_at=_NOW - timedelta(minutes=5),
    )


def _service(
    snapshots: tuple[AccountSnapshot, ...],
    quote_at: datetime,
    id_generator: object,
    secret_redactor: object,
) -> CashSweepShadowService:
    return CashSweepShadowService(
        _Accounts(snapshots),  # type: ignore[arg-type]
        _QuoteProvider(
            BrokerQuoteObservation(
                instrument_id="etf:US:SGOV",
                symbol="SGOV",
                quote_at=quote_at,
                bid=Decimal("100.48"),
                ask=Decimal("100.49"),
                last=Decimal("100.48"),
                source="schwab",
            )
        ),
        _Clock(),  # type: ignore[arg-type]
        id_generator,  # type: ignore[arg-type]
        secret_redactor,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_shadow_uses_cash_balance_and_ignores_buying_power_and_sell_orders(
    id_generator: object, secret_redactor: object
) -> None:
    service = _service(
        (
            _snapshot("A", "7881.64"),
            _snapshot(
                "B",
                "17087.44",
                open_orders=(_order(AccountOpenOrderSide.SELL, "79.99", "50"),),
            ),
        ),
        _NOW - timedelta(seconds=5),
        id_generator,
        secret_redactor,
    )

    envelope = await service.preview(BrokerOrderPreviewInput())

    assert envelope.errors == ()
    assert envelope.data is not None
    first, second = envelope.data.accounts
    assert (first.quantity, first.estimated_notional) == (46, Decimal("4622.54"))
    assert first.projected_cash_after_all_open_buys == Decimal("3259.10")
    assert first.residual_above_reserve == Decimal("59.10")
    assert (second.quantity, second.estimated_notional) == (138, Decimal("13867.62"))
    assert second.open_buy_order_reserve == 0
    assert second.projected_cash_after_all_open_buys == Decimal("3219.82")
    assert envelope.data.total_quantity == 184
    assert envelope.data.total_estimated_notional == Decimal("18490.16")
    assert envelope.data.policy["cash_source"] == "currentBalances.cashBalance"
    assert first.schwab_order_payload == {
        "session": "NORMAL",
        "duration": "DAY",
        "orderType": "LIMIT",
        "price": "100.49",
        "quantity": 46,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": "BUY",
                "quantity": 46,
                "instrument": {"symbol": "SGOV", "assetType": "EQUITY"},
            }
        ],
    }
    assert first.execution_effect is False


@pytest.mark.asyncio
async def test_shadow_reserves_open_buy_orders_before_sizing(
    id_generator: object, secret_redactor: object
) -> None:
    service = _service(
        (
            _snapshot(
                "A",
                "7881.64",
                open_orders=(_order(AccountOpenOrderSide.BUY, "100", "10"),),
            ),
        ),
        _NOW - timedelta(seconds=5),
        id_generator,
        secret_redactor,
    )

    envelope = await service.preview(BrokerOrderPreviewInput())

    assert envelope.data is not None
    account = envelope.data.accounts[0]
    assert account.open_buy_order_reserve == Decimal("1000")
    assert account.quantity == 36
    assert account.projected_cash_after_all_open_buys == Decimal("3264.00")


@pytest.mark.asyncio
async def test_stale_quote_is_calculated_but_never_presented_as_executable(
    id_generator: object, secret_redactor: object
) -> None:
    service = _service(
        (_snapshot("A", "7881.64"),),
        _NOW - timedelta(days=2),
        id_generator,
        secret_redactor,
    )

    envelope = await service.preview(BrokerOrderPreviewInput())

    assert envelope.data is not None
    account = envelope.data.accounts[0]
    assert account.quantity == 46
    assert account.status == "INDICATIVE"
    assert account.blocker_codes == ("BROKER_QUOTE_STALE",)
    assert envelope.degraded is True


@pytest.mark.asyncio
async def test_margin_or_unknown_open_orders_block_payload(
    id_generator: object, secret_redactor: object
) -> None:
    service = _service(
        (
            _snapshot(
                "A",
                "7881.64",
                margin=Decimal("1"),
                warnings=("SCHWAB_OPEN_ORDERS_UNAVAILABLE",),
            ),
        ),
        _NOW - timedelta(seconds=5),
        id_generator,
        secret_redactor,
    )

    envelope = await service.preview(BrokerOrderPreviewInput())

    assert envelope.data is not None
    account = envelope.data.accounts[0]
    assert account.status == "BLOCKED"
    assert account.quantity == 0
    assert account.schwab_order_payload is None
    assert set(account.blocker_codes) == {
        "MARGIN_BALANCE_NONZERO",
        "OPEN_ORDERS_UNAVAILABLE",
    }
