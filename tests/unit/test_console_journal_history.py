from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine

from application.dto.account_transactions import AccountGetTransactionsInput, TradeCycleQueryInput
from application.dto.behavior import BehaviorSummaryQueryInput
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from domain.common.enums import VendorId
from domain.portfolio.enums import AccountTransactionKind, AccountTransactionSide
from domain.portfolio.models import AccountTransaction
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.metadata import Base
from interfaces.console.journal_history import journal_history


def _activity(
    identity: str, account: str, when: datetime, side: AccountTransactionSide
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=identity,
        account_ref=account,
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:ABC",
        kind=AccountTransactionKind.TRADE,
        side=side,
        quantity=Decimal("1"),
        price=Decimal("100"),
        fees=Decimal("0"),
        currency="USD",
        occurred_at=when,
    )


@pytest.mark.asyncio
async def test_complete_journal_cohort_preserves_opening_lots_and_matches_behavior(
    id_generator: Any,
    fixed_clock: Any,
    secret_redactor: Any,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAccountTransactionRepository(engine)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2027, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(801):
        close = start + timedelta(days=index)
        rows.extend(
            (
                _activity(
                    f"buy-{index}", "ira", close - timedelta(hours=1), AccountTransactionSide.BUY
                ),
                _activity(f"sell-{index}", "ira", close, AccountTransactionSide.SELL),
            )
        )
    rows.extend(
        (
            _activity("other-buy", "brokerage", start, AccountTransactionSide.BUY),
            _activity(
                "other-sell", "brokerage", start + timedelta(hours=1), AccountTransactionSide.SELL
            ),
        )
    )
    repository.append_many(tuple(rows))
    service = AccountTransactionCoordinator(
        {},
        repository,
        SimpleNamespace(),
        fixed_clock,
        id_generator,
        secret_redactor,
    )
    # Public bounded reads are unchanged; the Console's owned read is complete.
    bounded = service.list_durable_transactions(AccountGetTransactionsInput(limit=500, end=end))
    assert bounded.data is not None and len(bounded.data.transactions) == 500
    bounded_cycles = service.get_trade_cycles(TradeCycleQueryInput(limit=500, end=end))
    assert bounded_cycles.data is not None and len(bounded_cycles.data.cycles) == 500
    result = await journal_history(
        service, account_refs=["ira"], instrument_ids=["equity:US:ABC"], start=start, end=end
    )
    tx = result["transactions"]["data"]
    cycles = result["trade_cycles"]["data"]
    assert tx["history_complete"] is True
    assert len(tx["transactions"]) == 1601  # first opening fill predates the period
    assert len(tx["cycle_activities"]) == 1
    assert len(cycles["cycles"]) == 801
    assert {row["account_ref"] for row in cycles["cycles"]} == {"ira"}
    assert all(row["status"] == "CLOSED" for row in cycles["cycles"])
    assert "TRADE_CYCLE_RESULTS_TRUNCATED" not in cycles["warning_codes"]
    assert "TRADE_CYCLE_COVERAGE_INCOMPLETE" in cycles["warning_codes"]
    assert result["filter_options"]["account_refs"] == ["brokerage", "ira"]
    first = next(row for row in cycles["cycles"] if "buy-0" in row["activity_ids"])
    assert datetime.fromisoformat(first["opened_at"]) < start
    behavior = service.get_behavior_summary(
        BehaviorSummaryQueryInput(
            account_refs=("ira",),
            instrument_ids=("equity:US:ABC",),
            start=start,
            end=end,
        )
    )
    assert behavior.ok and behavior.data is not None
    assert set(behavior.data.cohort_cycle_ids) == {row["cycle_id"] for row in cycles["cycles"]}
    empty = await journal_history(
        service, account_refs=["missing"], instrument_ids=[], start=start, end=end
    )
    assert empty["transactions"]["data"]["transactions"] == []
    assert empty["trade_cycles"]["data"]["cycles"] == []
    engine.dispose()
