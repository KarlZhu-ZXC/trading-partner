"""Trade Retro persistence rows."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class TradeRetroPlanSnapshotRow(Base):
    __tablename__ = "trade_retro_plan_snapshots"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_trade_retro_plan_snapshot_key"),
        CheckConstraint("schema_version = 1", name="trade_retro_plan_snapshot_schema"),
        Index("ix_trade_retro_plan_period", "period_start", "period_end", "captured_at"),
    )

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    period_start: Mapped[str] = mapped_column(Text, nullable=False)
    period_end: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[str] = mapped_column(Text, nullable=False)
    entries_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


class TradeRetroRunRow(Base):
    __tablename__ = "trade_retro_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_trade_retro_run_key"),
        CheckConstraint("status IN ('COMPLETE','INCOMPLETE')", name="trade_retro_run_status"),
        CheckConstraint("schema_version = 1", name="trade_retro_run_schema"),
        CheckConstraint("execution_effect = 0", name="trade_retro_no_execution"),
        Index("ix_trade_retro_runs_period", "period_end", "generated_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    period_start: Mapped[str] = mapped_column(Text, nullable=False)
    period_end: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    plan_snapshot_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("trade_retro_plan_snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=True,
    )
    transaction_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    findings_json: Mapped[str] = mapped_column(Text, nullable=False)
    warning_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    summary_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    llm_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_effect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TradeRetroExportReceiptRow(Base):
    __tablename__ = "trade_retro_export_receipts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_trade_retro_export_key"),
        Index("ix_trade_retro_exports_run", "run_id", "exported_at"),
    )

    receipt_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("trade_retro_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    exported_at: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    review_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TradeRetroReviewRevisionRow(Base):
    __tablename__ = "trade_retro_review_revisions"
    __table_args__ = (
        UniqueConstraint("run_id", "version", name="uq_trade_retro_review_run_version"),
        UniqueConstraint("idempotency_key", name="uq_trade_retro_review_key"),
        CheckConstraint("version >= 1", name="trade_retro_review_positive_version"),
        CheckConstraint(
            "status IN ('OPEN','ACCEPTED','DISPUTED','RESOLVED')",
            name="trade_retro_review_status",
        ),
        CheckConstraint(
            "reviewed_by IN ('user','external_agent')",
            name="trade_retro_review_confirmer",
        ),
        CheckConstraint("schema_version = 1", name="trade_retro_review_schema"),
        CheckConstraint("execution_effect = 0", name="trade_retro_review_no_execution"),
        Index("ix_trade_retro_reviews_run", "run_id", "version"),
    )

    review_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("trade_retro_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    note_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    action_items_json: Mapped[str] = mapped_column(Text, nullable=False)
    finding_reviews_json: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_effect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
