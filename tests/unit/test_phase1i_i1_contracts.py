"""Lean Phase 1I I1 domain, DTO, and persistence acceptance."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from application.dto.portfolio import AccountSnapshotDTO, PortfolioSimulateAdditionInput
from domain.common.enums import VendorId
from domain.common.errors import DataContractError, PersistenceError
from domain.portfolio.enums import AccountEnvironment, AccountPositionSide
from domain.portfolio.models import AccountPosition, AccountSnapshot
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.persistence.metadata import Base

FETCHED = datetime(2026, 7, 18, 12, tzinfo=UTC)
PRICE_AT = FETCHED - timedelta(minutes=3)


def _snapshot(snapshot_id: str = "snapshot_one") -> AccountSnapshot:
    return AccountSnapshot(
        snapshot_id=snapshot_id,
        account_ref="account_hash",
        provider=VendorId.MANUAL_CSV,
        environment=AccountEnvironment.MANUAL,
        base_currency="USD",
        account_as_of=FETCHED - timedelta(minutes=1),
        fetched_at=FETCHED,
        cash=Decimal("1000.00"),
        buying_power=None,
        net_assets=Decimal("1200.00"),
        margin_used=None,
        positions=(
            AccountPosition(
                instrument_id="equity:US:NVDA",
                side=AccountPositionSide.LONG,
                quantity=Decimal("2"),
                sellable_quantity=Decimal("2"),
                average_cost=Decimal("100"),
                diluted_cost=None,
                market_price=Decimal("110"),
                market_price_at=PRICE_AT,
                market_value=Decimal("220"),
                unrealized_pnl=Decimal("20"),
                realized_pnl=None,
                currency="USD",
            ),
        ),
        open_orders=(),
        degraded=False,
        warning_codes=(),
    )


def test_account_contract_separates_price_and_account_times() -> None:
    snapshot = _snapshot()
    assert snapshot.positions[0].market_price_at == PRICE_AT
    assert snapshot.account_as_of != snapshot.positions[0].market_price_at
    with pytest.raises(DataContractError):
        AccountPosition(
            "equity:US:NVDA",
            AccountPositionSide.LONG,
            Decimal(1),
            None,
            None,
            None,
            Decimal(100),
            None,
            None,
            None,
            None,
            "USD",
        )


def test_portfolio_dto_uses_decimal_and_never_implies_execution() -> None:
    wire = AccountSnapshotDTO.from_domain(_snapshot()).model_dump(mode="json")
    assert wire["cash"] == "1000.00"
    request = PortfolioSimulateAdditionInput(
        instrument_id="equity:US:NVDA",
        quantity=Decimal("1.5"),
        assumed_price=Decimal("125.25"),
        currency="USD",
    )
    assert request.quantity == Decimal("1.5")


def test_account_repository_is_append_only_and_fingerprint_idempotent(tmp_path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'account.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAccountSnapshotRepository(engine)

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    first = repository.append_account(_snapshot("snapshot_first"))
    replay = repository.append_account(_snapshot("snapshot_replayed"))

    assert replay.snapshot_id == first.snapshot_id
    assert repository.get_account(first.snapshot_id) == first
    assert repository.latest_accounts() == (first,)
    engine.dispose()


def test_account_repository_reports_snapshot_id_conflict_safely(tmp_path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'account-conflict.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAccountSnapshotRepository(engine)
    first = replace(_snapshot("snapshot_same"), positions=())
    repository.append_account(first)

    with pytest.raises(PersistenceError) as exc_info:
        repository.append_account(
            replace(
                first,
                account_as_of=first.account_as_of - timedelta(minutes=1),
            )
        )

    error = exc_info.value
    assert error.message == "account snapshot id conflict"
    assert error.details == {
        "entity": "account_snapshot",
        "conflict_type": "snapshot_id",
    }
    assert error.retryable is True
    assert "account_hash" not in repr(error.details)
    engine.dispose()
