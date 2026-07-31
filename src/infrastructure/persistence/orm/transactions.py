"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class AccountTransactionRow(Base):
    __tablename__ = "account_transactions"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    account_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    provider_transaction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[str | None] = mapped_column(Text)
    price: Mapped[str | None] = mapped_column(Text)
    fees: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)
    cash_amount: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="legacy")
    mapping_version: Mapped[str] = mapped_column(
        Text, nullable=False, default="account_activity_v1"
    )


class AccountActivityCoverageReceiptRow(Base):
    __tablename__ = "account_activity_coverage_receipts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('COMPLETE','INCOMPLETE')",
            name="ck_account_activity_coverage_status",
        ),
        CheckConstraint(
            "event_count >= 0 AND inserted_count >= 0 AND duplicate_count >= 0 "
            "AND snapshot_count >= 0",
            name="ck_account_activity_coverage_counts",
        ),
        CheckConstraint(
            "inserted_count + duplicate_count = event_count",
            name="ck_account_activity_coverage_reconcile",
        ),
        Index(
            "ix_account_activity_coverage_account_window",
            "provider",
            "account_ref",
            "effective_start",
            "effective_end",
        ),
    )

    receipt_id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    requested_start: Mapped[str] = mapped_column(Text, nullable=False)
    requested_end: Mapped[str] = mapped_column(Text, nullable=False)
    effective_start: Mapped[str] = mapped_column(Text, nullable=False)
    effective_end: Mapped[str] = mapped_column(Text, nullable=False)
    earliest_event_at: Mapped[str | None] = mapped_column(Text)
    latest_event_at: Mapped[str | None] = mapped_column(Text)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    earliest_snapshot_at: Mapped[str | None] = mapped_column(Text)
    latest_snapshot_at: Mapped[str | None] = mapped_column(Text)
    mapping_version: Mapped[str] = mapped_column(Text, nullable=False)
    supported_kinds_json: Mapped[str] = mapped_column(Text, nullable=False)
    unavailable_kinds_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    gap_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 3B durable industry-cycle observations ---


# --- Phase 3A formal futures definitions ---
