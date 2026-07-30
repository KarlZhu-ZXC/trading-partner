"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm.common import HEX64_CHECK, JsonStringTuple


class ChallengeReviewRow(Base):
    __tablename__ = "challenge_reviews"
    __table_args__ = (
        UniqueConstraint(
            "start_idempotency_key",
            name="uq_challenge_reviews_start_idempotency_key",
        ),
        CheckConstraint(
            "(start_idempotency_key IS NULL AND start_payload_sha256 IS NULL) OR "
            "(start_idempotency_key IS NOT NULL AND "
            f"{HEX64_CHECK.format(col='start_payload_sha256')})",
            name="ck_challenge_reviews_start_idempotency",
        ),
    )

    review_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text, ForeignKey("investment_cases.case_id", ondelete="RESTRICT"), nullable=False
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_action: Mapped[str] = mapped_column(Text, nullable=False)
    related_candidate_id: Mapped[str | None] = mapped_column(Text)
    related_evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    position_context_snapshot_id: Mapped[str | None] = mapped_column(Text)
    context_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolution_rationale: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(Text)
    start_idempotency_key: Mapped[str | None] = mapped_column(Text)
    start_payload_sha256: Mapped[str | None] = mapped_column(Text)


class ChallengeReviewResolutionRow(Base):
    __tablename__ = "challenge_review_resolutions"
    __table_args__ = (
        UniqueConstraint("review_id", name="uq_challenge_review_resolutions_review"),
        UniqueConstraint(
            "idempotency_key",
            name="uq_challenge_review_resolutions_idempotency_key",
        ),
        CheckConstraint(
            "resolution IN ('accept','revise','reject','defer')",
            name="ck_challenge_review_resolutions_resolution",
        ),
        CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_challenge_review_resolutions_confirmed_by",
        ),
        CheckConstraint(
            HEX64_CHECK.format(col="payload_sha256"),
            name="ck_challenge_review_resolutions_payload_sha256",
        ),
    )

    resolution_id: Mapped[str] = mapped_column(Text, primary_key=True)
    review_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("challenge_reviews.review_id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[str] = mapped_column(Text, nullable=False)


class ChallengeQuestionRow(Base):
    __tablename__ = "challenge_questions"

    question_id: Mapped[str] = mapped_column(Text, primary_key=True)
    review_id: Mapped[str] = mapped_column(
        Text, ForeignKey("challenge_reviews.review_id", ondelete="RESTRICT"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    question_set_version: Mapped[str] = mapped_column(Text, nullable=False)


class ChallengeFindingRow(Base):
    __tablename__ = "challenge_findings"

    finding_id: Mapped[str] = mapped_column(Text, primary_key=True)
    review_id: Mapped[str] = mapped_column(
        Text, ForeignKey("challenge_reviews.review_id", ondelete="RESTRICT"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 1L workflow receipts and historical account transactions ---


class WorkflowRunRow(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_research_runs_idempotency_key"),
        CheckConstraint(
            "status IN ('started','running','succeeded','partial','failed')",
            name="ck_research_runs_status",
        ),
        CheckConstraint(
            "(status IN ('started','running') AND completed_at IS NULL) OR "
            "(status IN ('succeeded','partial','failed') AND completed_at IS NOT NULL)",
            name="ck_research_runs_terminal_time",
        ),
        CheckConstraint(
            HEX64_CHECK.format(col="request_payload_sha256"),
            name="ck_research_runs_request_payload_sha256",
        ),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workflow_type: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("investment_cases.case_id", ondelete="RESTRICT")
    )
    instrument_id: Mapped[str | None] = mapped_column(Text)
    requested_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    report_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    heartbeat_at: Mapped[str] = mapped_column(Text, nullable=False)
    lease_expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    missing_capabilities: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)


class WorkflowRunStepRow(Base):
    __tablename__ = "research_run_steps"

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("research_runs.run_id", ondelete="RESTRICT"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    required: Mapped[int] = mapped_column(Integer, nullable=False)
    ok: Mapped[int] = mapped_column(Integer, nullable=False)
    degraded: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    source_names: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    error_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)


class WorkflowRunFactArtifactRow(Base):
    __tablename__ = "research_run_fact_artifacts"
    __table_args__ = (
        CheckConstraint(
            HEX64_CHECK.format(col="payload_sha256"),
            name="ck_research_run_fact_artifacts_payload_sha256",
        ),
        CheckConstraint(
            "size_bytes >= 0 AND size_bytes <= 1048576",
            name="ck_research_run_fact_artifacts_size",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("research_runs.run_id", ondelete="RESTRICT"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
