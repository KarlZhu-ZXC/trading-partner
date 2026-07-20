"""SQLAlchemy normalized read-only transaction repository."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from domain.common.enums import VendorId
from domain.portfolio.enums import AccountTransactionKind, AccountTransactionSide
from domain.portfolio.models import AccountTransaction
from infrastructure.persistence.models import AccountTransactionRow


class SqlAlchemyAccountTransactionRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append_many(
        self, transactions: tuple[AccountTransaction, ...]
    ) -> tuple[AccountTransaction, ...]:
        inserted: list[AccountTransaction] = []
        with Session(self._engine) as session, session.begin():
            for item in transactions:
                key = (item.provider.value, item.account_ref, item.provider_transaction_id)
                if session.get(AccountTransactionRow, key) is not None:
                    continue
                session.add(
                    AccountTransactionRow(
                        provider=item.provider.value,
                        account_ref=item.account_ref,
                        provider_transaction_id=item.provider_transaction_id,
                        instrument_id=item.instrument_id,
                        kind=item.kind.value,
                        side=item.side.value if item.side else None,
                        quantity=str(item.quantity),
                        price=str(item.price) if item.price is not None else None,
                        fees=str(item.fees),
                        currency=item.currency,
                        occurred_at=item.occurred_at.isoformat(),
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
        if start is not None:
            statement = statement.where(
                AccountTransactionRow.occurred_at >= start.isoformat()
            )
        if end is not None:
            statement = statement.where(AccountTransactionRow.occurred_at <= end.isoformat())
        statement = statement.order_by(
            AccountTransactionRow.occurred_at.desc(),
            AccountTransactionRow.provider_transaction_id,
        ).limit(limit)
        with Session(self._engine) as session:
            return tuple(self._hydrate(item) for item in session.scalars(statement))

    @staticmethod
    def _hydrate(row: AccountTransactionRow) -> AccountTransaction:
        return AccountTransaction(
            provider_transaction_id=row.provider_transaction_id,
            account_ref=row.account_ref,
            provider=VendorId(row.provider),
            instrument_id=row.instrument_id,
            kind=AccountTransactionKind(row.kind),
            side=AccountTransactionSide(row.side) if row.side else None,
            quantity=Decimal(row.quantity),
            price=Decimal(row.price) if row.price is not None else None,
            fees=Decimal(row.fees),
            currency=row.currency,
            occurred_at=datetime.fromisoformat(row.occurred_at),
        )
