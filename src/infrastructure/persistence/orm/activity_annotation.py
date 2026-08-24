"""ORM rows for append-only transaction/decision links."""

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class TransactionDecisionLinkRow(Base):
    __tablename__ = "transaction_decision_links"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "account_ref",
            "provider_transaction_id",
            "version",
            name="uq_transaction_decision_links_key_version",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_transaction_decision_links_idempotency"
        ),
        CheckConstraint(
            "status IN ('LINKED_DECISION_PLAN','UNPLANNED','CASH_MANAGEMENT',"
            "'TRANSFER_OR_CORPORATE_ACTION','PROVIDER_CORRECTION')",
            name="status",
        ),
        CheckConstraint(
            "classification IS NULL OR classification IN "
            "('ACTIVE_TRADE','LONG_TERM_INVESTMENT','HEDGE','CASH_MANAGEMENT',"
            "'TRANSFER_OR_ADMIN','UNCLASSIFIED')",
            name="classification",
        ),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "(trade_plan_id IS NULL) = (trade_plan_version IS NULL)",
            name="plan_pair",
        ),
        ForeignKeyConstraint(
            ["provider", "account_ref", "provider_transaction_id"],
            [
                "account_transactions.provider",
                "account_transactions.account_ref",
                "account_transactions.provider_transaction_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trade_plan_id", "trade_plan_version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_transaction_decision_links_activity",
            "provider",
            "account_ref",
            "provider_transaction_id",
            "version",
        ),
        Index("ix_transaction_decision_links_status", "status", "created_at"),
    )

    annotation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    provider_transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str | None] = mapped_column(Text)
    order_intent_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("broker_order_intents.order_intent_id", ondelete="RESTRICT"),
    )
    decision_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("decision_records.decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    trade_plan_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_id: Mapped[str | None] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_note: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# A descriptive compatibility name for callers that use ActivityAnnotation.
ActivityAnnotationRow = TransactionDecisionLinkRow
