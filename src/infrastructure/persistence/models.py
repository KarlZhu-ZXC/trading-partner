"""SQLAlchemy ORM models for Phase 1A/1B/1D foundation + Phase 1C research memory."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base


class JsonStringTuple(TypeDecorator[tuple[str, ...]]):
    """Store tuple[str, ...] as a JSON array text column."""

    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: tuple[str, ...] | list[str] | None, dialect: Dialect
    ) -> str:
        if value is None:
            return "[]"
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> tuple[str, ...]:
        if value is None or value == "":
            return ()
        parsed: Any = json.loads(value)
        if not isinstance(parsed, list):
            msg = "JSON string tuple column must decode to a list"
            raise TypeError(msg)
        return tuple(str(item) for item in parsed)


class SchemaVersionRow(Base):
    __tablename__ = "schema_versions"

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    applied_at: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class SystemAuditLogRow(Base):
    __tablename__ = "system_audit_log"

    audit_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 1B research-state tables ---


class InvestmentCaseRow(Base):
    __tablename__ = "investment_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','strengthened','weakened','invalidated','archived')",
            name="ck_investment_cases_status",
        ),
        CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)",
            name="ck_investment_cases_archived_at",
        ),
        CheckConstraint(
            "(status = 'archived') = (archived_reason IS NOT NULL)",
            name="ck_investment_cases_archived_reason",
        ),
        CheckConstraint("schema_version = 1", name="ck_investment_cases_schema_version"),
        CheckConstraint("updated_at >= created_at", name="ck_investment_cases_updated_at"),
        Index("ix_investment_cases_status", "status"),
        Index("ix_investment_cases_case_type", "case_type"),
        Index("ix_investment_cases_primary_instrument_id", "primary_instrument_id"),
    )

    case_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    primary_instrument_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_tags_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_case_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    evidence_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    report_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    event_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    decision_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ThesisRow(Base):
    __tablename__ = "theses"
    __table_args__ = (
        CheckConstraint(
            "role IN ('primary','sub','competitor','bear')",
            name="ck_theses_role",
        ),
        CheckConstraint(
            "status IN ('draft','active','strengthened','weakened','invalidated','archived')",
            name="ck_theses_status",
        ),
        CheckConstraint("current_revision_no >= 1", name="ck_theses_revision_no"),
        CheckConstraint(
            "(role = 'sub') = (parent_thesis_id IS NOT NULL)",
            name="ck_theses_parent",
        ),
        CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)",
            name="ck_theses_archived_at",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_theses_updated_at"),
        Index("ix_theses_case_id", "case_id"),
        Index("ix_theses_status", "status"),
        Index("ix_theses_parent", "parent_thesis_id"),
    )

    thesis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    latest_revision_id: Mapped[str] = mapped_column(Text, nullable=False)
    parent_thesis_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rival_thesis_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class ThesisRevisionRow(Base):
    __tablename__ = "thesis_revisions"
    __table_args__ = (
        UniqueConstraint(
            "thesis_id", "revision_no", name="uq_thesis_revisions_thesis_revision"
        ),
        CheckConstraint("revision_no >= 1", name="ck_thesis_revisions_revision_no"),
        CheckConstraint(
            "(revision_no = 1) = (supersedes_revision_no IS NULL)",
            name="ck_thesis_revisions_supersedes_first",
        ),
        CheckConstraint(
            "supersedes_revision_no IS NULL OR supersedes_revision_no < revision_no",
            name="ck_thesis_revisions_supersedes_value",
        ),
        CheckConstraint(
            "confirmed_at >= proposed_at",
            name="ck_thesis_revisions_confirmed_at",
        ),
        CheckConstraint("schema_version = 1", name="ck_thesis_revisions_schema_version"),
        CheckConstraint(
            "confirmation_mode IN ('normal','strict_review')",
            name="ck_thesis_revisions_confirmation_mode",
        ),
        CheckConstraint(
            "confidence_band IN ('low','medium','high')",
            name="ck_thesis_revisions_confidence_band",
        ),
        CheckConstraint(
            "rating IN ('avoid','watch','speculative_buy','buy','sell','hold')",
            name="ck_thesis_revisions_rating",
        ),
        Index("ix_thesis_revisions_thesis_id", "thesis_id"),
        Index("ix_thesis_revisions_case_id", "case_id"),
        Index("ix_thesis_revisions_confirmed_at", "confirmed_at"),
    )

    revision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    thesis_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_band: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_mode: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_at: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[str] = mapped_column(Text, nullable=False)
    observation_window_start: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_window_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation_check_note: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AssumptionRow(Base):
    __tablename__ = "assumptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["thesis_id", "revision_no"],
            ["thesis_revisions.thesis_id", "thesis_revisions.revision_no"],
            name="fk_assumptions_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('proposed','accepted','disputed','rejected','retired')",
            name="ck_assumptions_status",
        ),
        CheckConstraint(
            "(status = 'retired') = (retired_at IS NOT NULL)",
            name="ck_assumptions_retired",
        ),
        CheckConstraint(
            "(status = 'retired') = (retired_reason IS NOT NULL)",
            name="ck_assumptions_retired_reason",
        ),
        CheckConstraint(
            "confirmed_at >= proposed_at",
            name="ck_assumptions_confirmed_at",
        ),
        Index("ix_assumptions_thesis_revision", "thesis_id", "revision_no"),
        Index("ix_assumptions_case_id", "case_id"),
        Index("ix_assumptions_status", "status"),
    )

    assumption_id: Mapped[str] = mapped_column(Text, primary_key=True)
    thesis_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    falsifiability: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_at: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    retired_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class InvalidationConditionRow(Base):
    __tablename__ = "invalidation_conditions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["thesis_id", "revision_no"],
            ["thesis_revisions.thesis_id", "thesis_revisions.revision_no"],
            name="fk_invalidations_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("severity IN ('soft','hard')", name="ck_invalidations_severity"),
        CheckConstraint(
            "status IN ('armed','partially_triggered','triggered','rearmed','retired')",
            name="ck_invalidations_status",
        ),
        CheckConstraint(
            "(status = 'triggered') = (triggered_at IS NOT NULL)",
            name="ck_invalidations_triggered",
        ),
        CheckConstraint(
            "(status = 'triggered') = (triggered_reason IS NOT NULL)",
            name="ck_invalidations_triggered_reason",
        ),
        CheckConstraint(
            "confirmed_at >= proposed_at",
            name="ck_invalidations_confirmed_at",
        ),
        Index("ix_invalidations_thesis_revision", "thesis_id", "revision_no"),
        Index("ix_invalidations_case_id", "case_id"),
        Index("ix_invalidations_status", "status"),
    )

    invalidation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    thesis_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    observable: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_at: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_checked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)


class OpenQuestionRow(Base):
    __tablename__ = "open_questions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','answered','stale','closed_without_answer')",
            name="ck_open_questions_status",
        ),
        CheckConstraint(
            "(status = 'answered') = (answered_at IS NOT NULL)",
            name="ck_open_questions_answered",
        ),
        CheckConstraint(
            "(status = 'answered') = (answer_summary IS NOT NULL)",
            name="ck_open_questions_answer_summary",
        ),
        CheckConstraint(
            "(status = 'closed_without_answer') = (closed_without_answer_reason IS NOT NULL)",
            name="ck_open_questions_closed_reason",
        ),
        CheckConstraint(
            "answered_at IS NULL OR answered_at >= asked_at",
            name="ck_open_questions_answered_at",
        ),
        Index("ix_open_questions_case_id", "case_id"),
        Index("ix_open_questions_status", "status"),
    )

    question_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    asked_at: Mapped[str] = mapped_column(Text, nullable=False)
    answered_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_without_answer_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False)


class WatchlistItemRow(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('watching','triggered','promoted_to_case','expired','archived')",
            name="ck_watchlist_status",
        ),
        CheckConstraint(
            "(status = 'promoted_to_case') = (promoted_to_case_id IS NOT NULL)",
            name="ck_watchlist_promoted",
        ),
        CheckConstraint(
            "(status = 'triggered') = (triggered_at IS NOT NULL)",
            name="ck_watchlist_triggered",
        ),
        CheckConstraint(
            "(status = 'triggered') = (triggered_reason IS NOT NULL)",
            name="ck_watchlist_triggered_reason",
        ),
        CheckConstraint("updated_at >= created_at", name="ck_watchlist_updated_at"),
        CheckConstraint("market IN ('A_SHARE','US')", name="ck_watchlist_market"),
        Index("ix_watchlist_status", "status"),
        Index("ix_watchlist_case_id", "case_id"),
        Index("ix_watchlist_market_symbol", "market", "symbol"),
    )

    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    thesis_hint: Mapped[str] = mapped_column(Text, nullable=False)
    triggers_json: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False, default=()
    )
    case_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    promoted_to_case_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="SET NULL"),
        nullable=True,
    )
    triggered_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class WatchlistGroupRow(Base):
    __tablename__ = "watchlist_groups"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_group_key",
            name="uq_watchlist_group_source_key",
        ),
        CheckConstraint(
            "source IN ('MOOMOO','MANUAL_CSV')",
            name="ck_watchlist_groups_source",
        ),
        CheckConstraint(
            "group_type IN ('SYSTEM','CUSTOM','MANUAL')",
            name="ck_watchlist_groups_type",
        ),
        CheckConstraint("writable IN (0, 1)", name="ck_watchlist_groups_writable"),
        CheckConstraint("active IN (0, 1)", name="ck_watchlist_groups_active_bool"),
        CheckConstraint(
            "(active = 1) = (removed_at IS NULL)",
            name="ck_watchlist_groups_active",
        ),
        CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_watchlist_groups_seen_order",
        ),
        CheckConstraint(
            "last_synced_at >= last_seen_at",
            name="ck_watchlist_groups_synced_after_seen",
        ),
        CheckConstraint(
            "removed_at IS NULL OR removed_at >= last_seen_at",
            name="ck_watchlist_groups_removed_after_seen",
        ),
        Index("ix_watchlist_groups_source", "source"),
        Index("ix_watchlist_groups_source_active", "source", "active"),
        Index("ix_watchlist_groups_last_synced", "last_synced_at"),
    )

    group_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_group_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    group_type: Mapped[str] = mapped_column(Text, nullable=False)
    writable: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    removed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=False)


class WatchlistMembershipRow(Base):
    __tablename__ = "watchlist_memberships"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "provider_code",
            name="uq_watchlist_memberships_group_code",
        ),
        CheckConstraint(
            "source IN ('MOOMOO','MANUAL_CSV')",
            name="ck_watchlist_memberships_source",
        ),
        CheckConstraint("active IN (0, 1)", name="ck_watchlist_memberships_active_bool"),
        CheckConstraint(
            "research_supported IN (0, 1)",
            name="ck_watchlist_memberships_research_bool",
        ),
        CheckConstraint(
            "(active = 1) = (removed_at IS NULL)",
            name="ck_watchlist_memberships_active",
        ),
        CheckConstraint(
            "(research_supported = 1 AND instrument_id IS NOT NULL) OR (research_supported = 0)",
            name="ck_watchlist_memberships_research_instrument",
        ),
        CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_watchlist_memberships_seen_order",
        ),
        CheckConstraint(
            "last_synced_at >= last_seen_at",
            name="ck_watchlist_memberships_synced_after_seen",
        ),
        CheckConstraint(
            "removed_at IS NULL OR removed_at >= last_seen_at",
            name="ck_watchlist_memberships_removed_after_seen",
        ),
        Index("ix_watchlist_memberships_group", "group_id"),
        Index("ix_watchlist_memberships_group_active", "group_id", "active"),
        Index("ix_watchlist_memberships_provider_code", "provider_code"),
    )

    membership_id: Mapped[str] = mapped_column(Text, primary_key=True)
    group_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("watchlist_groups.group_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    provider_code: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_asset_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_supported: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    removed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[str] = mapped_column(Text, nullable=False)


class WatchlistMutationRow(Base):
    __tablename__ = "watchlist_mutations"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_watchlist_mutations_idempotency_key",
        ),
        CheckConstraint(
            "source IN ('MOOMOO','MANUAL_CSV')",
            name="ck_watchlist_mutations_source",
        ),
        CheckConstraint(
            "action IN ('ADD','REMOVE')",
            name="ck_watchlist_mutations_action",
        ),
        CheckConstraint(
            "requested_by IN ('user','external_agent')",
            name="ck_watchlist_mutations_requested_by",
        ),
        CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','PARTIAL','FAILED')",
            name="ck_watchlist_mutations_status",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="ck_watchlist_mutations_completed_after_requested",
        ),
        CheckConstraint(
            "(((status = 'PENDING') AND completed_at IS NULL AND error_code IS NULL)"
            " OR ((status = 'SUCCEEDED') AND completed_at IS NOT NULL AND error_code IS NULL)"
            " OR ((status IN ('PARTIAL','FAILED')) AND completed_at IS NOT NULL"
            " AND error_code IS NOT NULL))",
            name="ck_watchlist_mutations_status_state",
        ),
        Index("ix_watchlist_mutations_status", "status"),
        Index("ix_watchlist_mutations_requested", "requested_at"),
    )

    mutation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    group_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_code: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class CandidateThesisRevisionRow(Base):
    __tablename__ = "candidate_thesis_revisions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_candidate_idempotency_key"),
        CheckConstraint(
            "kind IN ("
            "'thesis_revision','assumption','invalidation_condition',"
            "'open_question','watchlist_item','case_status_change','trade_plan'"
            ")",
            name="ck_candidate_kind",
        ),
        CheckConstraint(
            "kind = 'watchlist_item' OR case_id IS NOT NULL",
            name="ck_candidate_case_scope",
        ),
        CheckConstraint(
            "kind NOT IN ('assumption','invalidation_condition') OR thesis_id IS NOT NULL",
            name="ck_candidate_thesis_scope",
        ),
        CheckConstraint(
            "status IN ('proposed','confirmed','rejected','withdrawn','expired')",
            name="ck_candidate_status",
        ),
        CheckConstraint(
            "confirmation_mode IN ('normal','strict_review')",
            name="ck_candidate_confirmation_mode",
        ),
        CheckConstraint(
            "("
            "status IN ('proposed','expired') "
            "AND reviewed_at IS NULL AND reviewed_by IS NULL"
            ") OR ("
            "status IN ('confirmed','rejected','withdrawn') "
            "AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL"
            ")",
            name="ck_candidate_review_state",
        ),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="ck_candidate_rejection_reason",
        ),
        CheckConstraint(
            "status != 'withdrawn' OR review_note IS NOT NULL",
            name="ck_candidate_withdraw_note",
        ),
        Index("ix_candidate_case_id", "case_id"),
        Index("ix_candidate_status", "status"),
        Index("ix_candidate_kind", "kind"),
        Index("ix_candidate_expires_at", "expires_at"),
    )

    candidate_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=True,
    )
    thesis_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_revision_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 1D instrument master + provider state tables ---


class InstrumentRow(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint(
            "asset_type",
            "market",
            "symbol",
            name="uq_instruments_asset_type_market_symbol",
        ),
        CheckConstraint(
            "market IN ('A_SHARE','US','CME','DCE','OTC','LME')",
            name="ck_instruments_market",
        ),
        CheckConstraint(
            "asset_type IN ("
            "'equity','etf','index','option','future',"
            "'commodity_spot','cfd','benchmark')",
            name="ck_instruments_asset_type",
        ),
        CheckConstraint("is_active IN (0, 1)", name="ck_instruments_is_active"),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_instruments_updated_at",
        ),
        Index("ix_instruments_market_name", "market", "name"),
    )

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False)
    listing_status: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    mic: Mapped[str | None] = mapped_column(Text, nullable=True)
    underlying_instrument_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
        nullable=True,
    )
    multiplier: Mapped[str | None] = mapped_column(Text, nullable=True)
    tick_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    lot_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class InstrumentAliasRow(Base):
    __tablename__ = "instrument_aliases"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "alias_type",
            "alias_value",
            name="uq_instrument_aliases_instrument_type_value",
        ),
        CheckConstraint(
            "is_primary IN (0, 1)",
            name="ck_instrument_aliases_is_primary",
        ),
        CheckConstraint(
            "market IN ('A_SHARE','US','CME','DCE','OTC','LME')",
            name="ck_instrument_aliases_market",
        ),
        Index("ix_instrument_aliases_value", "market", "alias_value"),
        Index("ix_instrument_aliases_instrument", "instrument_id"),
        Index(
            "uq_instrument_aliases_one_primary",
            "instrument_id",
            "alias_type",
            unique=True,
            sqlite_where=text("is_primary = 1"),
        ),
    )

    alias_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    alias_type: Mapped[str] = mapped_column(Text, nullable=False)
    alias_value: Mapped[str] = mapped_column(Text, nullable=False)
    alias_value_raw: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderCacheRow(Base):
    __tablename__ = "provider_cache"
    __table_args__ = (
        Index("ix_provider_cache_expires", "expires_at"),
        Index("ix_provider_cache_lookup", "market", "category", "instrument_id"),
    )

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    freshness: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderHealthRow(Base):
    __tablename__ = "provider_health"
    __table_args__ = (
        CheckConstraint(
            "state IN ('ok','degraded','error')",
            name="ck_provider_health_state",
        ),
        CheckConstraint(
            "circuit_state IN ('closed','open','half_open')",
            name="ck_provider_health_circuit_state",
        ),
    )

    vendor: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_failure_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    circuit_state: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ProviderRateLimitRow(Base):
    __tablename__ = "provider_rate_limits"

    vendor: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[str] = mapped_column(Text, primary_key=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    limit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class RedditSampleCacheRow(Base):
    __tablename__ = "reddit_sample_cache"
    __table_args__ = (
        Index("ix_reddit_sample_cache_expires", "expires_at"),
    )

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    config_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class RedditCooldownRow(Base):
    __tablename__ = "reddit_provider_cooldown"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    cooldown_until: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 1C research-memory business tables (no Search ORM rows) ---

_HEX64_CHECK = (
    "length({col}) = 64 AND {col} = lower({col}) "
    "AND {col} NOT GLOB '*[^0-9a-f]*'"
)

_EVIDENCE_TYPE_IN = (
    "'market_snapshot','fundamental_snapshot','financial_statement',"
    "'company_action','company_news','global_news','research_report',"
    "'technical_signal','sentiment','macro','account_snapshot',"
    "'portfolio_snapshot','user_observation',"
    "'a_share_announcement','a_share_interactive_qa','a_share_analyst_report',"
    "'a_share_consensus_estimate','a_share_capital_flow',"
    "'a_share_northbound_flow','a_share_chip_distribution',"
    "'a_share_dragon_tiger','a_share_margin_financing','a_share_block_trade',"
    "'a_share_shareholder_count','a_share_unlock','a_share_dividend',"
    "'a_share_order_book','a_share_tick','a_share_limit_ecology',"
    "'a_share_market_heat','a_share_concept_heat','a_share_option_snapshot',"
    "'sec_filing','sec_company_fact','us_insider_activity','us_10b5_1',"
    "'us_pre_post_market','us_news_sentiment','fred_macro',"
    "'stocktwits_sentiment','reddit_sentiment','prediction_market',"
    "'correction'"
)


class ResearchEvidenceRow(Base):
    __tablename__ = "research_evidence"
    __table_args__ = (
        UniqueConstraint(
            "content_sha256", name="uq_research_evidence_content_sha256"
        ),
        CheckConstraint(
            f"evidence_type IN ({_EVIDENCE_TYPE_IN})",
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
            "effective_to IS NULL OR effective_from IS NULL "
            "OR effective_to >= effective_from",
            name="effective_order",
        ),
        CheckConstraint(
            "evidence_type != 'correction' OR supersedes_evidence_id IS NOT NULL",
            name="correction_supersedes",
        ),
        CheckConstraint(
            _HEX64_CHECK.format(col="content_sha256"),
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


class CaseEvidenceLinkRow(Base):
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
    case_id: Mapped[str] = mapped_column(
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
            "CAST(materiality_decimal AS REAL) >= 0 "
            "AND CAST(materiality_decimal AS REAL) <= 1",
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
    case_id: Mapped[str] = mapped_column(
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
        UniqueConstraint(
            "content_sha256", name="uq_research_reports_content_sha256"
        ),
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
            _HEX64_CHECK.format(col="content_sha256"),
            name="content_sha256",
        ),
        Index("ix_reports_case_created_at", "case_id", "created_at"),
        Index("ix_reports_supersedes", "supersedes_report_id"),
    )

    report_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
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
    case_id: Mapped[str] = mapped_column(
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
        UniqueConstraint(
            "idempotency_key", name="uq_decision_records_idempotency_key"
        ),
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
            _HEX64_CHECK.format(col="idempotency_payload_sha256"),
            name="idempotency_hash",
        ),
        Index("ix_decisions_case_recorded_at", "case_id", "recorded_at"),
        Index("ix_decisions_supersedes", "supersedes_decision_id"),
    )

    decision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
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
    position_context_snapshot_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class JournalEntryRow(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_journal_entries_idempotency_key"
        ),
        CheckConstraint(
            "entry_type IN ("
            "'note','observation','reflection','postmortem','question')",
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
            _HEX64_CHECK.format(col="idempotency_payload_sha256"),
            name="idempotency_hash",
        ),
        Index("ix_journal_case_created_at", "case_id", "created_at"),
        Index("ix_journal_supersedes", "supersedes_journal_id"),
    )

    journal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
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


class AccountSnapshotRow(Base):
    __tablename__ = "account_snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    account_ref: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False)
    account_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    cash: Mapped[str | None] = mapped_column(Text)
    buying_power: Mapped[str | None] = mapped_column(Text)
    net_assets: Mapped[str | None] = mapped_column(Text)
    margin_used: Mapped[str | None] = mapped_column(Text)
    open_orders_json: Mapped[str] = mapped_column(Text, nullable=False)
    degraded: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_codes_json: Mapped[str] = mapped_column(Text, nullable=False)


class AccountPositionRow(Base):
    __tablename__ = "account_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id"], ["account_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[str] = mapped_column(Text, nullable=False)
    sellable_quantity: Mapped[str | None] = mapped_column(Text)
    average_cost: Mapped[str | None] = mapped_column(Text)
    diluted_cost: Mapped[str | None] = mapped_column(Text)
    market_price: Mapped[str | None] = mapped_column(Text)
    market_price_at: Mapped[str | None] = mapped_column(Text)
    market_value: Mapped[str | None] = mapped_column(Text)
    unrealized_pnl: Mapped[str | None] = mapped_column(Text)
    realized_pnl: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)


class PortfolioSnapshotRow(Base):
    __tablename__ = "portfolio_snapshots"

    portfolio_snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    account_snapshot_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False)
    total_value: Mapped[str | None] = mapped_column(Text)
    exposures_json: Mapped[str] = mapped_column(Text, nullable=False)
    missing_instrument_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    degraded: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_codes_json: Mapped[str] = mapped_column(Text, nullable=False)


class CasePositionLinkRow(Base):
    __tablename__ = "case_position_links"

    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    account_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# --- Phase 2B risk-engine policy snapshots ---


class RiskPolicyRow(Base):
    __tablename__ = "risk_policies"
    __table_args__ = (
        UniqueConstraint("version", name="uq_risk_policies_version"),
        UniqueConstraint("idempotency_key", name="uq_risk_policies_idempotency_key"),
        CheckConstraint("version >= 1", name="ck_risk_policies_version"),
        CheckConstraint(
            "CAST(single_position_max_percent AS REAL) >= 0"
            " AND CAST(single_position_max_percent AS REAL) <= 100",
            name="ck_risk_policies_single_position_max_percent",
        ),
        CheckConstraint(
            "CAST(gross_exposure_max_percent AS REAL) > 0"
            " AND CAST(gross_exposure_max_percent AS REAL) <= 1000",
            name="ck_risk_policies_gross_exposure_max_percent",
        ),
        CheckConstraint(
            "CAST(minimum_cash_percent AS REAL) >= 0"
            " AND CAST(minimum_cash_percent AS REAL) <= 100",
            name="ck_risk_policies_minimum_cash_percent",
        ),
        CheckConstraint(
            "CAST(margin_usage_max_percent AS REAL) >= 0"
            " AND CAST(margin_usage_max_percent AS REAL) <= 1000",
            name="ck_risk_policies_margin_usage_max_percent",
        ),
        CheckConstraint("max_account_age_seconds >= 1", name="ck_risk_policies_account_age"),
        CheckConstraint("max_price_age_seconds >= 1", name="ck_risk_policies_price_age"),
        CheckConstraint("is_system_default IN (0, 1)", name="ck_risk_policies_system_default"),
        CheckConstraint(
            "confirmed_by IN ('system_default', 'user', 'external_agent')",
            name="ck_risk_policies_confirmed_by",
        ),
        CheckConstraint("schema_version = 1", name="ck_risk_policies_schema_version"),
        Index("ix_risk_policies_version", "version"),
    )

    policy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    single_position_max_percent: Mapped[str] = mapped_column(Text, nullable=False)
    gross_exposure_max_percent: Mapped[str] = mapped_column(Text, nullable=False)
    minimum_cash_percent: Mapped[str] = mapped_column(Text, nullable=False)
    margin_usage_max_percent: Mapped[str] = mapped_column(Text, nullable=False)
    max_account_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_price_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    is_system_default: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    risk_budget_max_percent: Mapped[str] = mapped_column(Text, nullable=False, default="2")
    theme_exposure_max_percent: Mapped[str] = mapped_column(
        Text, nullable=False, default="40"
    )
    drawdown_max_percent: Mapped[str] = mapped_column(Text, nullable=False, default="20")
    liquidity_participation_max_percent: Mapped[str] = mapped_column(
        Text, nullable=False, default="10"
    )
    correlation_max_absolute: Mapped[str] = mapped_column(
        Text, nullable=False, default="0.85"
    )
    event_blackout_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# --- Phase 3D versioned Trade Plans ---


class TradePlanIdentityRow(Base):
    __tablename__ = "trade_plan_identities"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_trade_plan_identities_case_id"),
    )

    plan_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class TradePlanVersionRow(Base):
    __tablename__ = "trade_plan_versions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_trade_plan_versions_idempotency_key"),
        CheckConstraint("version >= 1", name="ck_trade_plan_versions_version"),
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED')",
            name="ck_trade_plan_versions_status",
        ),
        CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_trade_plan_versions_confirmed_by",
        ),
        CheckConstraint("schema_version = 1", name="ck_trade_plan_versions_schema"),
        Index("ix_trade_plan_versions_plan_version", "plan_id", "version"),
    )

    plan_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("trade_plan_identities.plan_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    thesis_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    reference_price: Mapped[str] = mapped_column(Text, nullable=False)
    reference_price_at: Mapped[str] = mapped_column(Text, nullable=False)
    target_position_percent: Mapped[str] = mapped_column(Text, nullable=False)
    max_position_percent: Mapped[str] = mapped_column(Text, nullable=False)
    risk_budget_percent: Mapped[str] = mapped_column(Text, nullable=False)
    stop_price: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TradePlanConditionRow(Base):
    __tablename__ = "trade_plan_conditions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 0", name="ck_trade_plan_conditions_position"),
        CheckConstraint(
            "phase IN ('ENTRY','SCALE','EXIT','INVALIDATION','REVIEW')",
            name="ck_trade_plan_conditions_phase",
        ),
        CheckConstraint(
            "mode IN ('MANUAL','MONITORABLE')",
            name="ck_trade_plan_conditions_mode",
        ),
    )

    plan_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    condition_code: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    fact_type: Mapped[str | None] = mapped_column(Text)
    metric_key: Mapped[str | None] = mapped_column(Text)
    comparator: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    instrument_id: Mapped[str | None] = mapped_column(Text)
    max_fact_age_seconds: Mapped[int | None] = mapped_column(Integer)
    event_after: Mapped[str | None] = mapped_column(Text)



# --- Phase 2C Monitoring ---


class MonitorIdentityRow(Base):
    __tablename__ = "monitor_identities"

    monitor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorVersionRow(Base):
    __tablename__ = "monitor_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trade_plan_id", "trade_plan_version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("idempotency_key", name="uq_monitor_versions_idempotency_key"),
        CheckConstraint("version >= 1", name="ck_monitor_versions_version"),
        CheckConstraint(
            "cadence IN ('ON_DEMAND','A_SHARE_POST_MARKET','US_POST_MARKET')",
            name="ck_monitor_versions_cadence",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','PAUSED','ARCHIVED')",
            name="ck_monitor_versions_status",
        ),
        CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_monitor_versions_confirmed_by",
        ),
        CheckConstraint("schema_version = 1", name="ck_monitor_versions_schema"),
        Index("ix_monitor_versions_monitor_version", "monitor_id", "version"),
    )

    monitor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("investment_cases.case_id", ondelete="RESTRICT")
    )
    primary_instrument_id: Mapped[str | None] = mapped_column(Text)
    trade_plan_id: Mapped[str | None] = mapped_column(Text)
    trade_plan_version: Mapped[int | None] = mapped_column(Integer)
    cadence: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MonitorRuleStateRow(Base):
    __tablename__ = "monitor_rule_states"
    __table_args__ = (
        CheckConstraint("monitor_version >= 1", name="ck_monitor_rule_states_version"),
        CheckConstraint(
            "state IN ('QUIET','TRIGGERED','NOT_EVALUATED')",
            name="ck_monitor_rule_states_state",
        ),
    )

    monitor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_identities.monitor_id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule_code: Mapped[str] = mapped_column(Text, primary_key=True)
    monitor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[str | None] = mapped_column(Text)
    fact_as_of: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorEventRow(Base):
    __tablename__ = "monitor_events"
    __table_args__ = (
        CheckConstraint("monitor_version >= 1", name="ck_monitor_events_version"),
        CheckConstraint(
            "event_type IN ('TRIGGERED','RECOVERED','NOT_EVALUATED')",
            name="ck_monitor_events_type",
        ),
        CheckConstraint(
            "severity IN ('INFO','MEDIUM','HIGH')", name="ck_monitor_events_severity"
        ),
        Index("ix_monitor_events_monitor_created", "monitor_id", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    monitor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
        nullable=False,
    )
    monitor_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[str | None] = mapped_column(Text)
    threshold_value: Mapped[str | None] = mapped_column(Text)
    fact_as_of: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorEventResolutionRow(Base):
    __tablename__ = "monitor_event_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_monitor_event_resolutions_idempotency_key"
        ),
        CheckConstraint(
            "action IN ('ACKNOWLEDGE','RESOLVE')",
            name="ck_monitor_event_resolutions_action",
        ),
        CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_monitor_event_resolutions_confirmed_by",
        ),
        Index("ix_monitor_event_resolutions_event", "event_id", "created_at"),
    )

    resolution_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        Text, ForeignKey("monitor_events.event_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class MonitorRunRow(Base):
    __tablename__ = "monitor_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED')",
            name="ck_monitor_runs_status",
        ),
        CheckConstraint("completed_at >= started_at", name="ck_monitor_runs_time_order"),
        Index("ix_monitor_runs_completed", "completed_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    requested_monitor_ids: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False
    )
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    monitors_evaluated: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_evaluated: Mapped[int] = mapped_column(Integer, nullable=False)
    events_created: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    error_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)


# --- Phase 1K persistent Challenge Reviews ---


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
            f"{_HEX64_CHECK.format(col='start_payload_sha256')})",
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
            _HEX64_CHECK.format(col="payload_sha256"),
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
            _HEX64_CHECK.format(col="request_payload_sha256"),
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
    missing_capabilities: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False
    )


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
            _HEX64_CHECK.format(col="payload_sha256"),
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


class FuturesProductRow(Base):
    __tablename__ = "futures_products"
    __table_args__ = (
        UniqueConstraint("product_key", name="uq_futures_products_product_key"),
        CheckConstraint(
            "market IN ('CME','DCE','US','LME')",
            name="ck_futures_products_market",
        ),
        Index("ix_futures_products_market_root", "market", "root"),
    )

    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_key: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    root: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesProductVersionRow(Base):
    __tablename__ = "futures_product_versions"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "version",
            name="uq_futures_product_versions_product_version",
        ),
        CheckConstraint("version >= 1", name="ck_futures_product_versions_version"),
        CheckConstraint(
            "settlement_method IN ('physical','cash','unknown')",
            name="ck_futures_product_versions_settlement_method",
        ),
        Index(
            "ix_futures_product_versions_product_valid",
            "product_id",
            "valid_from",
        ),
    )

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    commodity: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    price_unit: Mapped[str] = mapped_column(Text, nullable=False)
    multiplier: Mapped[str] = mapped_column(Text, nullable=False)
    tick_size: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_method: Mapped[str] = mapped_column(Text, nullable=False)
    session_calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str] = mapped_column(Text, nullable=False)
    valid_to: Mapped[str | None] = mapped_column(Text)
    definition_as_of: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesContractRow(Base):
    __tablename__ = "futures_contracts"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "contract_month",
            name="uq_futures_contracts_product_month",
        ),
        Index("ix_futures_contracts_product", "product_id", "contract_month"),
    )

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_month: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesContractVersionRow(Base):
    __tablename__ = "futures_contract_versions"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "version",
            name="uq_futures_contract_versions_instrument_version",
        ),
        CheckConstraint("version >= 1", name="ck_futures_contract_versions_version"),
        CheckConstraint(
            "status IN ('listed','active','expired','delisted','unknown')",
            name="ck_futures_contract_versions_status",
        ),
        Index(
            "ix_futures_contract_versions_instrument_asof",
            "instrument_id",
            "definition_as_of",
        ),
    )

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    listed_at: Mapped[str | None] = mapped_column(Text)
    first_trade_at: Mapped[str | None] = mapped_column(Text)
    last_trade_at: Mapped[str | None] = mapped_column(Text)
    expiration_at: Mapped[str | None] = mapped_column(Text)
    first_notice_at: Mapped[str | None] = mapped_column(Text)
    delivery_start: Mapped[str | None] = mapped_column(Text)
    delivery_end: Mapped[str | None] = mapped_column(Text)
    settlement_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    definition_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class FuturesContractStatisticsRow(Base):
    __tablename__ = "futures_contract_statistics"
    __table_args__ = (
        CheckConstraint(
            "settlement_status IN ('preliminary','final','unknown')",
            name="ck_futures_contract_statistics_status",
        ),
        Index(
            "ix_futures_contract_statistics_trade_date",
            "trade_date",
            "instrument_id",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    trade_date: Mapped[str] = mapped_column(Text, primary_key=True)
    published_at: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    settlement: Mapped[str | None] = mapped_column(Text)
    settlement_status: Mapped[str] = mapped_column(Text, nullable=False)
    session_volume: Mapped[str | None] = mapped_column(Text)
    open_interest: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)


class ContinuousSeriesDefinitionRow(Base):
    __tablename__ = "continuous_series_definitions"
    __table_args__ = (
        CheckConstraint("rank >= 0", name="ck_continuous_series_rank"),
        CheckConstraint(
            "roll_rule IN ('calendar','volume','open_interest')",
            name="ck_continuous_series_roll_rule",
        ),
        CheckConstraint("adjustment = 'none'", name="ck_continuous_series_adjustment"),
        Index(
            "ix_continuous_series_product",
            "product_id",
            "roll_rule",
            "rank",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_products.product_id", ondelete="RESTRICT"),
        nullable=False,
    )
    roll_rule: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    adjustment: Mapped[str] = mapped_column(Text, nullable=False)
    provider_methodology_version: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str] = mapped_column(Text, nullable=False)
    valid_to: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ContinuousContractMappingRow(Base):
    __tablename__ = "continuous_contract_mappings"
    __table_args__ = (
        UniqueConstraint(
            "continuous_instrument_id",
            "effective_from",
            name="uq_continuous_mapping_effective_from",
        ),
        Index(
            "ix_continuous_contract_mappings_series",
            "continuous_instrument_id",
            "effective_from",
        ),
    )

    mapping_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    continuous_instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("continuous_series_definitions.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_instrument_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    effective_from: Mapped[str] = mapped_column(Text, nullable=False)
    effective_to: Mapped[str | None] = mapped_column(Text)
    mapping_source: Mapped[str] = mapped_column(Text, nullable=False)


class IndustryMetricObservationRow(Base):
    __tablename__ = "industry_metric_observations"
    __table_args__ = (
        UniqueConstraint(
            "cycle",
            "dataset_code",
            "metric_code",
            "period_end",
            "published_at",
            name="uq_industry_metric_vintage",
        ),
        Index(
            "ix_industry_metric_series",
            "cycle",
            "metric_code",
            "period_end",
        ),
        Index("ix_industry_metric_publication", "published_at"),
    )

    observation_key: Mapped[str] = mapped_column(Text, primary_key=True)
    cycle: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_code: Mapped[str] = mapped_column(Text, nullable=False)
    metric_code: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    geography: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[str] = mapped_column(Text, nullable=False)
    period_end: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    measurement_basis: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_estimated: Mapped[int] = mapped_column(Integer, nullable=False)
    methodology_version: Mapped[str] = mapped_column(Text, nullable=False)
    methodology_break: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[str] = mapped_column(Text, nullable=False)


# --- Scheduled operational synchronization receipts ---


class PostMarketSyncRunRow(Base):
    __tablename__ = "post_market_sync_runs"
    __table_args__ = (
        UniqueConstraint(
            "market_session_date",
            name="uq_post_market_sync_session_date",
        ),
        CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED')",
            name="ck_post_market_sync_status",
        ),
        CheckConstraint(
            "portfolio_status IN ('SUCCEEDED','FAILED')",
            name="ck_post_market_sync_portfolio_status",
        ),
        CheckConstraint(
            "watchlist_status IN ('SUCCEEDED','FAILED')",
            name="ck_post_market_sync_watchlist_status",
        ),
        CheckConstraint(
            "completed_at >= started_at", name="ck_post_market_sync_time_order"
        ),
        CheckConstraint(
            "attempt_count >= 1", name="ck_post_market_sync_attempt_count"
        ),
        CheckConstraint(
            "watchlist_groups_synced IS NULL OR watchlist_groups_synced >= 0",
            name="ck_post_market_sync_group_count",
        ),
        CheckConstraint(
            "watchlist_membership_relations_synced IS NULL"
            " OR watchlist_membership_relations_synced >= 0",
            name="ck_post_market_sync_membership_count",
        ),
        Index("ix_post_market_sync_completed_at", "completed_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    market_session_date: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    portfolio_status: Mapped[str] = mapped_column(Text, nullable=False)
    watchlist_status: Mapped[str] = mapped_column(Text, nullable=False)
    account_snapshot_ids: Mapped[tuple[str, ...]] = mapped_column(
        JsonStringTuple(), nullable=False
    )
    watchlist_groups_synced: Mapped[int | None] = mapped_column(Integer)
    watchlist_membership_relations_synced: Mapped[int | None] = mapped_column(Integer)
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    error_codes: Mapped[tuple[str, ...]] = mapped_column(JsonStringTuple(), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
