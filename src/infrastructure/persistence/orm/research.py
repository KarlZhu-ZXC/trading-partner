"""SQLAlchemy ORM declarations grouped by persistence capability."""

from __future__ import annotations

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
from infrastructure.persistence.orm.common import JsonStringTuple


class ResearchSubjectRow(Base):
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

    subject_id: Mapped[str] = mapped_column("case_id", Text, primary_key=True)
    subject_type: Mapped[str] = mapped_column("case_type", Text, nullable=False)
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
    linked_subject_ids_json: Mapped[tuple[str, ...]] = mapped_column(
        "linked_case_ids_json", JsonStringTuple(), nullable=False, default=()
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
    subject_id: Mapped[str] = mapped_column(
        "case_id",
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
        UniqueConstraint("thesis_id", "revision_no", name="uq_thesis_revisions_thesis_revision"),
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
    subject_id: Mapped[str] = mapped_column(
        "case_id",
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
    subject_id: Mapped[str] = mapped_column(
        "case_id",
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
    subject_id: Mapped[str] = mapped_column(
        "case_id",
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
    subject_id: Mapped[str] = mapped_column(
        "case_id",
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
    subject_id: Mapped[str | None] = mapped_column(
        "case_id",
        Text,
        ForeignKey("investment_cases.case_id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    promoted_to_subject_id: Mapped[str | None] = mapped_column(
        "promoted_to_case_id",
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
    subject_id: Mapped[str | None] = mapped_column(
        "case_id",
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
