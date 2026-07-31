from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine

from application.dto.performance_attribution import PerformanceAttributionInput
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from domain.attribution.enums import AttributionStatus, CostBasisMethod
from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountEnvironment,
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import (
    AccountActivityCoverageReceipt,
    AccountPosition,
    AccountSnapshot,
    AccountTransaction,
)
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.metadata import Base

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
START = NOW - timedelta(days=30)


def _trade(
    transaction_id: str,
    side: AccountTransactionSide,
    quantity: str,
    price: str,
    occurred_at: datetime,
) -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id=transaction_id,
        account_ref="account_1",
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:NVDA",
        kind=AccountTransactionKind.TRADE,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal("1"),
        currency="USD",
        occurred_at=occurred_at,
        source_type="TRADE",
        mapping_version="test_v1",
    )


def test_durable_performance_summary_keeps_fifo_provenance_explicit(
    id_generator: object,
    fixed_clock: object,
    secret_redactor: object,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    activities = SqlAlchemyAccountTransactionRepository(engine)
    snapshots = SqlAlchemyAccountSnapshotRepository(engine)
    activities.append_many(
        (
            _trade(
                "buy_1",
                AccountTransactionSide.BUY,
                "10",
                "100",
                START - timedelta(days=10),
            ),
            _trade(
                "sell_1",
                AccountTransactionSide.SELL,
                "4",
                "120",
                START + timedelta(days=10),
            ),
        )
    )
    activities.append_coverage(
        (
            AccountActivityCoverageReceipt(
                receipt_id="activity_coverage_1",
                provider=VendorId.SCHWAB,
                account_ref="account_1",
                requested_start=START,
                requested_end=NOW,
                effective_start=START,
                effective_end=NOW,
                earliest_event_at=START + timedelta(days=10),
                latest_event_at=START + timedelta(days=10),
                event_count=1,
                inserted_count=1,
                duplicate_count=0,
                snapshot_count=1,
                earliest_snapshot_at=NOW,
                latest_snapshot_at=NOW,
                mapping_version="test_v1",
                supported_kinds=tuple(AccountTransactionKind),
                unavailable_kinds=(),
                status=AccountActivityCoverageStatus.COMPLETE,
                gap_codes=(),
                fetched_at=NOW,
            ),
        )
    )
    snapshots.append_account(
        AccountSnapshot(
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
                    side=AccountPositionSide.LONG,
                    quantity=Decimal("6"),
                    sellable_quantity=None,
                    average_cost=Decimal("100"),
                    diluted_cost=None,
                    market_price=Decimal("130"),
                    market_price_at=NOW,
                    market_value=Decimal("780"),
                    unrealized_pnl=Decimal("180"),
                    realized_pnl=None,
                    currency="USD",
                ),
            ),
            open_orders=(),
            degraded=False,
            warning_codes=(),
        )
    )
    coordinator = AccountTransactionCoordinator(
        {},
        activities,
        snapshots,
        fixed_clock,  # type: ignore[arg-type]
        id_generator,  # type: ignore[arg-type]
        secret_redactor,  # type: ignore[arg-type]
    )

    envelope = coordinator.get_performance_attribution(
        PerformanceAttributionInput(
            start=START,
            end=NOW,
            cost_basis_method=CostBasisMethod.FIFO,
        )
    )

    assert envelope.ok and envelope.degraded and envelope.data is not None
    assert envelope.data.status is AttributionStatus.INCOMPLETE
    account = envelope.data.accounts[0]
    assert account.realized_pnl_before_fees == Decimal("80")
    assert account.unrealized_pnl_before_fees == Decimal("180")
    assert account.instruments[0].activity_ids == ("sell_1",)
    assert "FIFO_OPENING_HISTORY_UNVERIFIED" in account.warning_codes
    engine.dispose()
