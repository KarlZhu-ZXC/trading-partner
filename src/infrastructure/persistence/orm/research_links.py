"""ORM rows for confirmed evidence-impact links."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class EvidenceImpactRow(Base):
    __tablename__ = "evidence_impacts"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_evidence_impacts_candidate"),
        CheckConstraint("version >= 1", name="ck_evidence_impacts_version"),
        CheckConstraint(
            "stance IN ('supports','contradicts','uncertain')",
            name="ck_evidence_impacts_stance",
        ),
        CheckConstraint(
            "state IN ('ACTIVE','RETRACTED')",
            name="ck_evidence_impacts_state",
        ),
        CheckConstraint(
            "source_kind IN ('EVIDENCE','OBSERVATION','MONITOR')",
            name="ck_evidence_impacts_source_kind",
        ),
        CheckConstraint(
            "(source_kind = 'MONITOR' AND source_monitor_id IS NOT NULL "
            "AND source_rule_code IS NOT NULL) OR "
            "(source_kind != 'MONITOR' AND source_monitor_id IS NULL "
            "AND source_rule_code IS NULL)",
            name="ck_evidence_impacts_source_scope",
        ),
        Index("ix_evidence_impacts_subject", "case_id", "confirmed_at"),
        Index("ix_evidence_impacts_latest", "case_id", "impact_id", "version"),
    )

    impact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    thesis_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=False,
    )
    thesis_revision_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("thesis_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    assumption_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("assumptions.assumption_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_monitor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_rule_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    stance: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("candidate_thesis_revisions.candidate_id", ondelete="RESTRICT"),
        nullable=False,
    )


__all__ = ["EvidenceImpactRow"]
