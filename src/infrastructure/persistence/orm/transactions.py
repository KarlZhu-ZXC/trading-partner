"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class AccountTransactionRow(Base):
    __tablename__ = "account_transactions"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    account_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    provider_transaction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[str | None] = mapped_column(Text)
    fees: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 3B durable industry-cycle observations ---


# --- Phase 3A formal futures definitions ---
