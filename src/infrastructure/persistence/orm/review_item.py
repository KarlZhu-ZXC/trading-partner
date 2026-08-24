"""Durable current ReviewItem state and human transition receipts."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class ReviewItemRow(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("source_key", name="uq_review_items_source_key"),
        CheckConstraint(
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT','DECISION_REVIEW_DUE',"
            "'UNLINKED_ACTIVITY')",
            name="ck_review_items_source_type",
        ),
        CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED','AUTO_RESOLVED')",
            name="ck_review_items_status",
        ),
        CheckConstraint(
            "severity IN ('INFO','ATTENTION','ERROR')",
            name="ck_review_items_severity",
        ),
        CheckConstraint("active_at_source IN (0,1)", name="ck_review_items_active"),
        CheckConstraint("occurrence_count >= 1", name="ck_review_items_occurrence"),
        CheckConstraint("version >= 1", name="ck_review_items_version"),
        Index("ix_review_items_status_last_seen", "status", "last_seen_at"),
        Index("ix_review_items_subject_status", "subject_id", "status"),
        Index("ix_review_items_source_type_active", "source_type", "active_at_source"),
    )

    review_item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    href: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    active_at_source: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class ReviewItemActionRow(Base):
    __tablename__ = "review_item_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_review_item_actions_idempotency"),
        CheckConstraint(
            "status IN ('ACKNOWLEDGED','RESOLVED')",
            name="ck_review_item_actions_status",
        ),
        CheckConstraint("expected_version >= 1", name="ck_review_item_actions_expected"),
        CheckConstraint("result_version >= 2", name="ck_review_item_actions_result"),
        CheckConstraint("occurrence_no >= 1", name="ck_review_item_actions_occurrence"),
        Index("ix_review_item_actions_item_created", "review_item_id", "created_at"),
    )

    action_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_item_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("review_items.review_item_id", ondelete="RESTRICT"),
        nullable=False,
    )
    occurrence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_note: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ReviewItemOccurrenceRow(Base):
    __tablename__ = "review_item_occurrences"
    __table_args__ = (
        CheckConstraint("occurrence_no >= 1", name="ck_review_item_occurrences_number"),
        CheckConstraint(
            "resolution_mode IS NULL OR resolution_mode IN ('MANUAL','AUTO')",
            name="ck_review_item_occurrences_resolution_mode",
        ),
        CheckConstraint(
            "last_seen_at >= opened_at",
            name="ck_review_item_occurrences_last_seen",
        ),
        CheckConstraint(
            "(first_acknowledged_at IS NULL AND first_acknowledged_by IS NULL) OR "
            "(first_acknowledged_at >= opened_at AND first_acknowledged_by IS NOT NULL)",
            name="ck_review_item_occurrences_acknowledgment",
        ),
        CheckConstraint(
            "(resolved_at IS NULL AND resolved_by IS NULL AND resolution_mode IS NULL) OR "
            "(resolved_at >= opened_at AND resolved_by IS NOT NULL "
            "AND resolution_mode IS NOT NULL)",
            name="ck_review_item_occurrences_resolution",
        ),
        Index("ix_review_item_occurrences_opened", "opened_at"),
        Index("ix_review_item_occurrences_resolved", "resolved_at"),
    )

    review_item_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("review_items.review_item_id", ondelete="CASCADE"),
        primary_key=True,
    )
    occurrence_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    opened_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    first_acknowledged_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_acknowledged_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
