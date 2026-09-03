"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm.common import (
    EVIDENCE_TYPE_IN,
    HEX64_CHECK,
    JsonStringTuple,
)


class ResearchEvidenceRow(Base):
    __tablename__ = "research_evidence"
    __table_args__ = (
        UniqueConstraint("content_sha256", name="uq_research_evidence_content_sha256"),
        CheckConstraint(
            f"evidence_type IN ({EVIDENCE_TYPE_IN})",
            name="type",
        ),
        CheckConstraint(
            "origin IN ('external_fact','user_observation','system_derived')",
            name="origin",
        ),
        CheckConstraint(
            "quality IN ('primary','secondary','tertiary','unverified')",
            name="quality",
        ),
        CheckConstraint(
            "reliability IN ('high','medium','low','unknown')",
            name="reliability",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        CheckConstraint(
            "confidence_decimal IS NULL OR ("
            "CAST(confidence_decimal AS REAL) >= 0 "
            "AND CAST(confidence_decimal AS REAL) <= 1)",
            name="confidence",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="effective_order",
        ),
        CheckConstraint(
            "evidence_type != 'correction' OR supersedes_evidence_id IS NOT NULL",
            name="correction_supersedes",
        ),
        CheckConstraint(
            HEX64_CHECK.format(col="content_sha256"),
            name="content_sha256",
        ),
        Index("ix_evidence_observed_at", "observed_at"),
        Index("ix_evidence_type", "evidence_type"),
    )

    evidence_id: Mapped[str] = mapped_column(Text, primary_key=True)
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_data_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_vendor: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrument_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    topic_tags_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    quality: Mapped[str] = mapped_column(Text, nullable=False)
    reliability: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_decimal: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_evidence_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("research_evidence.evidence_id", ondelete="RESTRICT"),
        nullable=True,
    )
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SubjectEvidenceLinkRow(Base):
    __tablename__ = "case_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "evidence_id",
            name="uq_case_evidence_links_case_evidence",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        Index("ix_links_case_linked_at", "case_id", "linked_at"),
    )

    link_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("research_evidence.evidence_id", ondelete="RESTRICT"),
        nullable=False,
    )
    linked_at: Mapped[str] = mapped_column(Text, nullable=False)
    linked_by: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class EvidenceAssessmentRow(Base):
    __tablename__ = "evidence_assessments"
    __table_args__ = (
        CheckConstraint(
            "stance IN ('supports','contradicts','neutral','uncertain')",
            name="stance",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        CheckConstraint(
            "CAST(materiality_decimal AS REAL) >= 0 AND CAST(materiality_decimal AS REAL) <= 1",
            name="materiality",
        ),
        Index("ix_assessment_case_stance", "case_id", "stance"),
        Index("ix_assessment_thesis_stance", "thesis_id", "stance"),
        Index(
            "ix_assessments_evidence_assessed_at",
            "evidence_id",
            "assessed_at",
        ),
    )

    assessment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("research_evidence.evidence_id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    thesis_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=True,
    )
    thesis_revision_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("thesis_revisions.revision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    stance: Mapped[str] = mapped_column(Text, nullable=False)
    materiality_decimal: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_at: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ResearchReportRow(Base):
    __tablename__ = "research_reports"
    __table_args__ = (
        UniqueConstraint("content_sha256", name="uq_research_reports_content_sha256"),
        CheckConstraint(
            "report_type IN ("
            "'deep_dive','catalyst_review','a_share_market_review',"
            "'us_market_review','portfolio_review','ad_hoc')",
            name="type",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        CheckConstraint(
            "as_of <= created_at",
            name="as_of_order",
        ),
        CheckConstraint(
            HEX64_CHECK.format(col="content_sha256"),
            name="content_sha256",
        ),
        Index("ix_reports_case_created_at", "case_id", "created_at"),
        Index("ix_reports_supersedes", "supersedes_report_id"),
    )

    report_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    research_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    thesis_revision_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    supersedes_report_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("research_reports.report_id", ondelete="RESTRICT"),
        nullable=True,
    )
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ResearchEventRow(Base):
    __tablename__ = "research_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'company','earnings','regulatory','corporate_action',"
            "'industry','macro','policy','market_structure',"
            "'capital_market','other')",
            name="type",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        CheckConstraint(
            "(related_entity_type IS NULL) = (related_entity_id IS NULL)",
            name="related_entity_pair",
        ),
        Index("ix_events_case_occurred_at", "case_id", "occurred_at"),
        Index("ix_events_case_recorded_at", "case_id", "recorded_at"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrument_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    evidence_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    report_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    related_entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DecisionRecordRow(Base):
    __tablename__ = "decision_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_decision_records_idempotency_key"),
        CheckConstraint(
            "decision_type IN ("
            "'watch','no_action','initiate_intent','add_intent','hold',"
            "'reduce_intent','exit_intent','avoid','research_more')",
            name="type",
        ),
        CheckConstraint(
            "confirmation_mode IN ('normal','strict_review')",
            name="confirmation_mode",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        CheckConstraint(
            "decided_at <= recorded_at",
            name="decided_order",
        ),
        CheckConstraint(
            "("
            "decision_type IN ("
            "'initiate_intent','add_intent','hold',"
            "'reduce_intent','exit_intent','avoid'"
            ") AND confirmation_mode = 'strict_review'"
            ") OR ("
            "decision_type IN ('watch','no_action','research_more') "
            "AND confirmation_mode IN ('normal','strict_review')"
            ")",
            name="confirmation_matrix",
        ),
        CheckConstraint(
            HEX64_CHECK.format(col="idempotency_payload_sha256"),
            name="idempotency_hash",
        ),
        CheckConstraint(
            "scenario IS NULL OR scenario IN ('UPSIDE','SIDEWAYS','PULLBACK','INVALIDATION')",
            name="scenario",
        ),
        CheckConstraint(
            "(trade_plan_id IS NULL) = (trade_plan_version IS NULL)",
            name="trade_plan_pair",
        ),
        CheckConstraint(
            "trade_plan_version IS NULL OR trade_plan_version >= 1",
            name="trade_plan_version",
        ),
        Index("ix_decisions_external_note_revision", "external_note_revision_id"),
        Index("ix_decisions_case_recorded_at", "case_id", "recorded_at"),
        Index("ix_decisions_supersedes", "supersedes_decision_id"),
    )

    decision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_mode: Mapped[str] = mapped_column(Text, nullable=False)
    primary_instrument_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    thesis_revision_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    evidence_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    report_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    supersedes_decision_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("decision_records.decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    position_context_snapshot_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_plan_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_due_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_note_revision_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("external_note_revisions.note_revision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class JournalEntryRow(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_journal_entries_idempotency_key"),
        CheckConstraint(
            "entry_type IN ('note','observation','reflection','postmortem','question')",
            name="type",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="schema_version",
        ),
        CheckConstraint(
            "(related_entity_type IS NULL) = (related_entity_id IS NULL)",
            name="related_entity_pair",
        ),
        CheckConstraint(
            HEX64_CHECK.format(col="idempotency_payload_sha256"),
            name="idempotency_hash",
        ),
        Index("ix_journal_case_created_at", "case_id", "created_at"),
        Index("ix_journal_supersedes", "supersedes_journal_id"),
    )

    journal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_id: Mapped[str | None] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=True,
    )
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    authored_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    topic_tags_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    related_entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_journal_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("journal_entries.journal_id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# --- Phase 1I append-only account/portfolio snapshots ---
