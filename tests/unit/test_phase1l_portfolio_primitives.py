from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from application.dto.account_transactions import (
    AccountGetTransactionsInput,
    AccountTransactionDTO,
)
from application.services.portfolio_enrichment_calculator import (
    PortfolioEnrichmentCalculator,
)
from application.services.portfolio_risk_calculator import PortfolioRiskCalculator
from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from domain.portfolio.enums import (
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountPosition, AccountTransaction, PortfolioClassification
from infrastructure.providers.account.moomoo import MoomooAccountAdapter


class _Ids:
    def new(self, prefix: object) -> str:
        return f"{getattr(prefix, 'value', 'id')}_test"


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 18, 12, tzinfo=UTC)


class _MoomooContext:
    closed = False

    def get_acc_list(self) -> tuple[int, list[dict[str, object]]]:
        return 0, [{"acc_id": "raw-account", "trd_env": "REAL"}]

    def history_deal_list_query(self, **kwargs: object) -> tuple[int, list[dict[str, object]]]:
        assert kwargs["start"] == "2026-07-01"
        return 0, [
            {
                "deal_id": "raw-deal",
                "code": "US.NVDA",
                "trd_side": "BUY",
                "qty": "2",
                "price": "100.25",
                "create_time": "2026-07-02 10:30:00",
            }
        ]

    def close(self) -> None:
        self.closed = True


def _transaction(occurred_at: datetime) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id="txn_hash_1",
        account_ref="account_hash_1",
        provider=VendorId.MOOMOO,
        instrument_id="equity:US:NVDA",
        kind=AccountTransactionKind.TRADE,
        side=AccountTransactionSide.BUY,
        quantity=Decimal("2"),
        price=Decimal("100.25"),
        fees=Decimal("1.05"),
        currency="USD",
        occurred_at=occurred_at,
    )


@pytest.mark.asyncio
async def test_transaction_requires_aware_time_and_moomoo_history_is_normalized() -> None:
    with pytest.raises(DataContractError, match="timezone-aware"):
        _transaction(datetime(2026, 7, 18, 12))

    dto = AccountTransactionDTO.from_domain(_transaction(datetime(2026, 7, 18, 12, tzinfo=UTC)))
    assert dto.model_dump(mode="json")["price"] == "100.25"
    context = _MoomooContext()
    adapter = MoomooAccountAdapter(
        _Ids(),  # type: ignore[arg-type]
        enabled=True,
        host="127.0.0.1",
        port=11111,
        clock=_Clock(),
        context_factory=lambda _host, _port: context,  # type: ignore[arg-type]
    )
    result = await adapter.get_account_transactions(
        start=datetime(2026, 7, 1, tzinfo=UTC), end=None, limit=10
    )
    transaction = result.value.transactions[0]
    assert transaction.provider_transaction_id != "raw-deal"
    assert transaction.occurred_at.tzinfo is not None
    assert transaction.fees is None
    assert result.value.coverage[0].mapping_version == "moomoo_deals_v2"
    assert context.closed is True


def test_transaction_query_requires_ordered_aware_window() -> None:
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    with pytest.raises(ValidationError, match="start must be <= end"):
        AccountGetTransactionsInput(start=now, end=now - timedelta(days=1))
    with pytest.raises(ValidationError, match="timezone-aware"):
        AccountGetTransactionsInput(start=datetime(2026, 7, 1))


def test_portfolio_risk_is_decimal_and_reports_insufficient_history() -> None:
    calculator = PortfolioRiskCalculator()
    start = date(2026, 1, 1)
    closes = {
        start + timedelta(days=index): Decimal(100 + index + (index % 3)) for index in range(25)
    }
    available = calculator.calculate(
        instrument_id="equity:US:NVDA",
        benchmark_instrument_id="etf:US:SPY",
        instrument_closes=closes,
        benchmark_closes=closes,
    )
    missing = calculator.calculate(
        instrument_id="equity:US:NVDA",
        benchmark_instrument_id="etf:US:SPY",
        instrument_closes=dict(list(closes.items())[:10]),
        benchmark_closes=dict(list(closes.items())[:10]),
    )

    assert available.correlation == Decimal(1)
    assert available.beta == Decimal(1)
    assert missing.correlation is None
    assert missing.missing_reason == "requires at least 20 aligned daily returns"

    position = AccountPosition(
        instrument_id="equity:US:NVDA",
        side=AccountPositionSide.LONG,
        quantity=Decimal("2"),
        sellable_quantity=None,
        average_cost=None,
        diluted_cost=None,
        market_price=None,
        market_price_at=None,
        market_value=Decimal("200"),
        unrealized_pnl=None,
        realized_pnl=None,
        currency="USD",
    )
    enrichment = PortfolioEnrichmentCalculator().calculate(
        (position,),
        {
            position.instrument_id: PortfolioClassification(
                position.instrument_id, "Semiconductors", ("AI",)
            )
        },
    )
    actual = {
        (item.dimension, item.key, item.weight_within_currency) for item in enrichment.exposures
    }
    assert actual == {
        ("industry", "Semiconductors", Decimal(1)),
        ("theme", "AI", Decimal(1)),
    }
