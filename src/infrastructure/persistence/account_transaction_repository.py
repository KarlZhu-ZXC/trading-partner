"""SQLAlchemy normalized read-only transaction repository."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountActivityCoverageReceipt, AccountTransaction
from infrastructure.persistence.orm import AccountActivityCoverageReceiptRow, AccountTransactionRow


class SqlAlchemyAccountTransactionRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append_many(
        self, transactions: tuple[AccountTransaction, ...]
    ) -> tuple[AccountTransaction, ...]:
        inserted: list[AccountTransaction] = []
        seen: set[tuple[str, str, str]] = set()
        with Session(self._engine) as session, session.begin():
            for item in transactions:
                key = (item.provider.value, item.account_ref, item.provider_transaction_id)
                if key in seen or session.get(AccountTransactionRow, key) is not None:
                    continue
                seen.add(key)
                session.add(
                    AccountTransactionRow(
                        provider=item.provider.value,
                        account_ref=item.account_ref,
                        provider_transaction_id=item.provider_transaction_id,
                        instrument_id=item.instrument_id,
                        kind=item.kind.value,
                        side=item.side.value if item.side else None,
                        quantity=str(item.quantity) if item.quantity is not None else None,
                        price=str(item.price) if item.price is not None else None,
                        fees=str(item.fees) if item.fees is not None else None,
                        currency=item.currency,
                        occurred_at=item.occurred_at.astimezone(UTC).isoformat(),
                        cash_amount=(
                            str(item.cash_amount) if item.cash_amount is not None else None
                        ),
                        source_type=item.source_type,
                        mapping_version=item.mapping_version,
                    )
                )
                inserted.append(item)
        return tuple(inserted)

    def list(
        self,
        *,
        providers: tuple[VendorId, ...],
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> tuple[AccountTransaction, ...]:
        statement = select(AccountTransactionRow)
        if providers:
            statement = statement.where(
                AccountTransactionRow.provider.in_(item.value for item in providers)
            )
        with Session(self._engine) as session:
            # Historical rows may preserve different UTC offsets. Comparing their
            # ISO strings lexicographically can exclude an instant that is inside
            # the requested window, so filter hydrated aware datetimes instead.
            values = [self._hydrate(item) for item in session.scalars(statement)]
        if start is not None:
            values = [item for item in values if item.occurred_at >= start]
        if end is not None:
            values = [item for item in values if item.occurred_at <= end]
        values.sort(key=lambda item: item.provider_transaction_id)
        values.sort(key=lambda item: item.occurred_at, reverse=True)
        return tuple(values[:limit])

    def append_coverage(
        self, receipts: tuple[AccountActivityCoverageReceipt, ...]
    ) -> tuple[AccountActivityCoverageReceipt, ...]:
        inserted: list[AccountActivityCoverageReceipt] = []
        with Session(self._engine) as session, session.begin():
            for item in receipts:
                if session.get(AccountActivityCoverageReceiptRow, item.receipt_id) is not None:
                    continue
                session.add(
                    AccountActivityCoverageReceiptRow(
                        receipt_id=item.receipt_id,
                        provider=item.provider.value,
                        account_ref=item.account_ref,
                        requested_start=item.requested_start.astimezone(UTC).isoformat(),
                        requested_end=item.requested_end.astimezone(UTC).isoformat(),
                        effective_start=item.effective_start.astimezone(UTC).isoformat(),
                        effective_end=item.effective_end.astimezone(UTC).isoformat(),
                        earliest_event_at=(
                            item.earliest_event_at.astimezone(UTC).isoformat()
                            if item.earliest_event_at
                            else None
                        ),
                        latest_event_at=(
                            item.latest_event_at.astimezone(UTC).isoformat()
                            if item.latest_event_at
                            else None
                        ),
                        event_count=item.event_count,
                        inserted_count=item.inserted_count,
                        duplicate_count=item.duplicate_count,
                        snapshot_count=item.snapshot_count,
                        earliest_snapshot_at=(
                            item.earliest_snapshot_at.astimezone(UTC).isoformat()
                            if item.earliest_snapshot_at
                            else None
                        ),
                        latest_snapshot_at=(
                            item.latest_snapshot_at.astimezone(UTC).isoformat()
                            if item.latest_snapshot_at
                            else None
                        ),
                        mapping_version=item.mapping_version,
                        supported_kinds_json=json.dumps(
                            [kind.value for kind in item.supported_kinds], separators=(",", ":")
                        ),
                        unavailable_kinds_json=json.dumps(
                            [kind.value for kind in item.unavailable_kinds],
                            separators=(",", ":"),
                        ),
                        status=item.status.value,
                        gap_codes_json=json.dumps(item.gap_codes, separators=(",", ":")),
                        fetched_at=item.fetched_at.astimezone(UTC).isoformat(),
                    )
                )
                inserted.append(item)
        return tuple(inserted)

    def list_coverage(
        self,
        *,
        providers: tuple[VendorId, ...],
        account_refs: tuple[str, ...],
        limit: int,
    ) -> tuple[AccountActivityCoverageReceipt, ...]:
        statement = select(AccountActivityCoverageReceiptRow)
        if providers:
            statement = statement.where(
                AccountActivityCoverageReceiptRow.provider.in_(item.value for item in providers)
            )
        if account_refs:
            statement = statement.where(
                AccountActivityCoverageReceiptRow.account_ref.in_(account_refs)
            )
        statement = statement.order_by(
            AccountActivityCoverageReceiptRow.fetched_at.desc(),
            AccountActivityCoverageReceiptRow.receipt_id,
        ).limit(limit)
        with Session(self._engine) as session:
            return tuple(self._hydrate_coverage(row) for row in session.scalars(statement))

    @staticmethod
    def _hydrate(row: AccountTransactionRow) -> AccountTransaction:
        return AccountTransaction(
            provider_transaction_id=row.provider_transaction_id,
            account_ref=row.account_ref,
            provider=VendorId(row.provider),
            instrument_id=row.instrument_id,
            kind=AccountTransactionKind(row.kind),
            side=AccountTransactionSide(row.side) if row.side else None,
            quantity=Decimal(row.quantity) if row.quantity is not None else None,
            price=Decimal(row.price) if row.price is not None else None,
            fees=Decimal(row.fees) if row.fees is not None else None,
            currency=row.currency,
            occurred_at=datetime.fromisoformat(row.occurred_at),
            cash_amount=Decimal(row.cash_amount) if row.cash_amount is not None else None,
            source_type=row.source_type,
            mapping_version=row.mapping_version,
        )

    @staticmethod
    def _hydrate_coverage(
        row: AccountActivityCoverageReceiptRow,
    ) -> AccountActivityCoverageReceipt:
        return AccountActivityCoverageReceipt(
            receipt_id=row.receipt_id,
            provider=VendorId(row.provider),
            account_ref=row.account_ref,
            requested_start=datetime.fromisoformat(row.requested_start),
            requested_end=datetime.fromisoformat(row.requested_end),
            effective_start=datetime.fromisoformat(row.effective_start),
            effective_end=datetime.fromisoformat(row.effective_end),
            earliest_event_at=(
                datetime.fromisoformat(row.earliest_event_at) if row.earliest_event_at else None
            ),
            latest_event_at=(
                datetime.fromisoformat(row.latest_event_at) if row.latest_event_at else None
            ),
            event_count=row.event_count,
            inserted_count=row.inserted_count,
            duplicate_count=row.duplicate_count,
            snapshot_count=row.snapshot_count,
            earliest_snapshot_at=(
                datetime.fromisoformat(row.earliest_snapshot_at)
                if row.earliest_snapshot_at
                else None
            ),
            latest_snapshot_at=(
                datetime.fromisoformat(row.latest_snapshot_at) if row.latest_snapshot_at else None
            ),
            mapping_version=row.mapping_version,
            supported_kinds=tuple(
                AccountTransactionKind(value) for value in json.loads(row.supported_kinds_json)
            ),
            unavailable_kinds=tuple(
                AccountTransactionKind(value) for value in json.loads(row.unavailable_kinds_json)
            ),
            status=AccountActivityCoverageStatus(row.status),
            gap_codes=tuple(json.loads(row.gap_codes_json)),
            fetched_at=datetime.fromisoformat(row.fetched_at),
        )
