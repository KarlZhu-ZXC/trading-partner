"""Judgment Scorecard persistence rows."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class JudgmentScorecardRunRow(Base):
    __tablename__ = "judgment_scorecard_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_judgment_scorecard_idempotency_key"),
        CheckConstraint(
            "status IN ('COMPLETE','PARTIAL','NOT_EVALUATED')",
            name="judgment_scorecard_status",
        ),
        CheckConstraint(
            "schema_version IN (1, 2)",
            name="judgment_scorecard_schema",
        ),
        CheckConstraint("execution_effect = 0", name="judgment_scorecard_no_execution"),
        CheckConstraint("thesis_revision_no >= 1", name="judgment_scorecard_revision_no"),
        Index("ix_judgment_scorecard_subject_generated", "case_id", "generated_at"),
        Index("ix_judgment_scorecard_thesis_generated", "thesis_id", "generated_at"),
    )

    scorecard_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_title: Mapped[str] = mapped_column(Text, nullable=False)
    thesis_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=False,
    )
    thesis_title: Mapped[str] = mapped_column(Text, nullable=False)
    thesis_revision_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("thesis_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    thesis_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions_json: Mapped[str] = mapped_column(Text, nullable=False)
    warning_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_effect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
