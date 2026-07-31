from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from application.dto.account_transactions import (
    AccountGetActivityCoverageInput,
    AccountGetTransactionsInput,
)
from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import (
    AccountActivityBatch,
    AccountTransaction,
    ProviderAccountActivityCoverage,
)
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.metadata import Base

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
START = NOW - timedelta(days=30)


def _trade() -> AccountTransaction:
    return AccountTransaction(
        provider_transaction_id="txn_1",
        account_ref="schwab_account_1",
        provider=VendorId.SCHWAB,
        instrument_id="equity:US:NVDA",
        kind=AccountTransactionKind.TRADE,
        side=AccountTransactionSide.BUY,
        quantity=Decimal("2"),
        price=Decimal("100"),
        fees=Decimal("1"),
        currency="USD",
        occurred_at=NOW - timedelta(days=5),
        cash_amount=Decimal("-201"),
        source_type="TRADE",
        mapping_version="schwab_activity_v1",
    )


class _Provider:
    def is_configured(self) -> bool:
        return True

    async def get_account_transactions(
        self, **_kwargs: object
    ) -> ProviderSuccess[AccountActivityBatch]:
        return ProviderSuccess(
            AccountActivityBatch(
                transactions=(_trade(),),
                coverage=(
                    ProviderAccountActivityCoverage(
                        account_ref="schwab_account_1",
                        requested_start=START,
                        requested_end=NOW,
                        effective_start=START,
                        effective_end=NOW,
                        mapping_version="schwab_activity_v1",
                        supported_kinds=tuple(AccountTransactionKind),
                        unavailable_kinds=(),
                        gap_codes=(),
                        truncated=False,
                    ),
                ),
            ),
            ProviderResultMeta(
                vendor=VendorId.SCHWAB,
                category=DataCategory.ACCOUNT,
                role=SourceRole.PRIMARY,
                as_of=NOW,
                fetched_at=NOW,
                freshness=Freshness.UNKNOWN,
                session=TradingSession.UNKNOWN,
                latency_ms=None,
                cache_disposition=CacheDisposition.MISS,
                adjustment=None,
                data_delay_seconds=None,
                warnings=(),
            ),
        )


class _Snapshots:
    def list_account_history(self, **_kwargs: object) -> tuple[object, ...]:
        return (SimpleNamespace(account_as_of=NOW - timedelta(days=1)),)


@pytest.mark.asyncio
async def test_repeated_sync_is_deduplicated_and_coverage_is_machine_readable(
    id_generator: object,
    fixed_clock: object,
    secret_redactor: object,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAccountTransactionRepository(engine)
    coordinator = AccountTransactionCoordinator(
        {VendorId.SCHWAB: _Provider()},  # type: ignore[dict-item]
        repository,
        _Snapshots(),  # type: ignore[arg-type]
        fixed_clock,  # type: ignore[arg-type]
        id_generator,  # type: ignore[arg-type]
        secret_redactor,  # type: ignore[arg-type]
    )
    request = AccountGetTransactionsInput(
        providers=(VendorId.SCHWAB,), start=START, end=NOW
    )

    first = await coordinator.get_transactions(request)
    second = await coordinator.get_transactions(request)

    assert first.ok and second.ok
    assert first.data is not None and second.data is not None
    assert first.data.coverage_receipts[0].inserted_count == 1
    assert first.data.coverage_receipts[0].duplicate_count == 0
    assert first.data.coverage_receipts[0].status is AccountActivityCoverageStatus.COMPLETE
    assert second.data.coverage_receipts[0].inserted_count == 0
    assert second.data.coverage_receipts[0].duplicate_count == 1
    durable = coordinator.get_coverage(
        AccountGetActivityCoverageInput(account_refs=("schwab_account_1",))
    )
    assert durable.ok and durable.data is not None
    assert durable.data.overall_status is AccountActivityCoverageStatus.COMPLETE
    assert len(durable.data.receipts) == 2
    engine.dispose()


def test_cash_activity_and_unavailable_fee_round_trip() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAccountTransactionRepository(engine)
    cash = AccountTransaction(
        provider_transaction_id="cash_1",
        account_ref="schwab_account_1",
        provider=VendorId.SCHWAB,
        instrument_id=None,
        kind=AccountTransactionKind.TRANSFER,
        side=None,
        quantity=None,
        price=None,
        fees=None,
        currency="USD",
        occurred_at=NOW,
        cash_amount=Decimal("1770"),
        source_type="JOURNAL",
        mapping_version="schwab_activity_v1",
    )

    assert repository.append_many((cash, cash)) == (cash,)
    restored = repository.list(
        providers=(VendorId.SCHWAB,), start=NOW, end=NOW, limit=10
    )
    assert restored == (cash,)
    engine.dispose()
