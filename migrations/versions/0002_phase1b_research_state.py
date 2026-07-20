"""Phase 1B research state tables.

Revision ID: 0002_phase1b_research_state
Revises: 0001_phase1a_foundation
Create Date: 2026-07-16

Creates investment_cases, theses, thesis_revisions (append-only), assumptions,
invalidation_conditions, open_questions, watchlist_items, candidate_thesis_revisions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase1b_research_state"
down_revision: str | Sequence[str] | None = "0001_phase1a_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PHASE1B_VERSION = "phase1b_research_state"
_PHASE1B_DESCRIPTION = (
    "Phase 1B research state: investment_cases, theses, thesis_revisions, "
    "assumptions, invalidation_conditions, open_questions, watchlist_items, "
    "candidate_thesis_revisions"
)


def upgrade() -> None:
    op.create_table(
        "investment_cases",
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("case_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("primary_instrument_id", sa.Text(), nullable=True),
        sa.Column("topic_tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.Column("archived_reason", sa.Text(), nullable=True),
        sa.Column("linked_case_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("report_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("event_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("decision_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("case_id", name="pk_investment_cases"),
        sa.CheckConstraint(
            "status IN ('draft','active','strengthened','weakened','invalidated','archived')",
            name="ck_investment_cases_status",
        ),
        sa.CheckConstraint(
            "(status='archived') = (archived_at IS NOT NULL)",
            name="ck_investment_cases_archived_at",
        ),
        sa.CheckConstraint(
            "(status='archived') = (archived_reason IS NOT NULL)",
            name="ck_investment_cases_archived_reason",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_investment_cases_schema_version",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_investment_cases_updated_at",
        ),
    )
    op.create_index("ix_investment_cases_status", "investment_cases", ["status"])
    op.create_index("ix_investment_cases_case_type", "investment_cases", ["case_type"])
    op.create_index(
        "ix_investment_cases_primary_instrument_id",
        "investment_cases",
        ["primary_instrument_id"],
    )

    op.create_table(
        "theses",
        sa.Column("thesis_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_revision_no", sa.Integer(), nullable=False),
        sa.Column("latest_revision_id", sa.Text(), nullable=False),
        sa.Column("parent_thesis_id", sa.Text(), nullable=True),
        sa.Column("rival_thesis_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("thesis_id", name="pk_theses"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_theses_case_id_investment_cases",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "role IN ('primary','sub','competitor','bear')",
            name="ck_theses_role",
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','strengthened','weakened','invalidated','archived')",
            name="ck_theses_status",
        ),
        sa.CheckConstraint(
            "current_revision_no >= 1",
            name="ck_theses_revision_no",
        ),
        sa.CheckConstraint(
            "(role='sub') = (parent_thesis_id IS NOT NULL)",
            name="ck_theses_parent",
        ),
        sa.CheckConstraint(
            "(status='archived') = (archived_at IS NOT NULL)",
            name="ck_theses_archived_at",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_theses_updated_at",
        ),
    )
    op.create_index("ix_theses_case_id", "theses", ["case_id"])
    op.create_index("ix_theses_status", "theses", ["status"])
    op.create_index("ix_theses_parent", "theses", ["parent_thesis_id"])

    op.create_table(
        "thesis_revisions",
        sa.Column("revision_id", sa.Text(), nullable=False),
        sa.Column("thesis_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("supersedes_revision_no", sa.Integer(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence_band", sa.Text(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("confirmation_mode", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("proposed_at", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.Text(), nullable=False),
        sa.Column("observation_window_start", sa.Text(), nullable=True),
        sa.Column("observation_window_end", sa.Text(), nullable=True),
        sa.Column("invalidation_check_note", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("revision_id", name="pk_thesis_revisions"),
        sa.ForeignKeyConstraint(
            ["thesis_id"],
            ["theses.thesis_id"],
            name="fk_thesis_revisions_thesis_id_theses",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_thesis_revisions_case_id_investment_cases",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "thesis_id",
            "revision_no",
            name="uq_thesis_revisions_thesis_revision",
        ),
        sa.CheckConstraint(
            "revision_no >= 1",
            name="ck_thesis_revisions_revision_no",
        ),
        sa.CheckConstraint(
            "(revision_no=1) = (supersedes_revision_no IS NULL)",
            name="ck_thesis_revisions_supersedes_first",
        ),
        sa.CheckConstraint(
            "supersedes_revision_no IS NULL OR supersedes_revision_no < revision_no",
            name="ck_thesis_revisions_supersedes_value",
        ),
        sa.CheckConstraint(
            "confirmed_at >= proposed_at",
            name="ck_thesis_revisions_confirmed_at",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_thesis_revisions_schema_version",
        ),
        sa.CheckConstraint(
            "confirmation_mode IN ('normal','strict_review')",
            name="ck_thesis_revisions_confirmation_mode",
        ),
        sa.CheckConstraint(
            "confidence_band IN ('low','medium','high')",
            name="ck_thesis_revisions_confidence_band",
        ),
        sa.CheckConstraint(
            "rating IN ('avoid','watch','speculative_buy','buy','sell','hold')",
            name="ck_thesis_revisions_rating",
        ),
    )
    op.create_index("ix_thesis_revisions_thesis_id", "thesis_revisions", ["thesis_id"])
    op.create_index("ix_thesis_revisions_case_id", "thesis_revisions", ["case_id"])
    op.create_index(
        "ix_thesis_revisions_confirmed_at", "thesis_revisions", ["confirmed_at"]
    )

    op.create_table(
        "assumptions",
        sa.Column("assumption_id", sa.Text(), nullable=False),
        sa.Column("thesis_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("falsifiability", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proposed_at", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.Text(), nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("assumption_id", name="pk_assumptions"),
        sa.ForeignKeyConstraint(
            ["thesis_id"],
            ["theses.thesis_id"],
            name="fk_assumptions_thesis_id_theses",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_assumptions_case_id_investment_cases",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id", "revision_no"],
            ["thesis_revisions.thesis_id", "thesis_revisions.revision_no"],
            name="fk_assumptions_revision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','accepted','disputed','rejected','retired')",
            name="ck_assumptions_status",
        ),
        sa.CheckConstraint(
            "(status='retired') = (retired_at IS NOT NULL)",
            name="ck_assumptions_retired",
        ),
        sa.CheckConstraint(
            "(status='retired') = (retired_reason IS NOT NULL)",
            name="ck_assumptions_retired_reason",
        ),
        sa.CheckConstraint(
            "confirmed_at >= proposed_at",
            name="ck_assumptions_confirmed_at",
        ),
    )
    op.create_index(
        "ix_assumptions_thesis_revision", "assumptions", ["thesis_id", "revision_no"]
    )
    op.create_index("ix_assumptions_case_id", "assumptions", ["case_id"])
    op.create_index("ix_assumptions_status", "assumptions", ["status"])

    op.create_table(
        "invalidation_conditions",
        sa.Column("invalidation_id", sa.Text(), nullable=False),
        sa.Column("thesis_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("observable", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proposed_at", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.Text(), nullable=False),
        sa.Column("last_checked_at", sa.Text(), nullable=True),
        sa.Column("triggered_at", sa.Text(), nullable=True),
        sa.Column("triggered_reason", sa.Text(), nullable=True),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("invalidation_id", name="pk_invalidation_conditions"),
        sa.ForeignKeyConstraint(
            ["thesis_id"],
            ["theses.thesis_id"],
            name="fk_invalidation_conditions_thesis_id_theses",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_invalidation_conditions_case_id_investment_cases",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id", "revision_no"],
            ["thesis_revisions.thesis_id", "thesis_revisions.revision_no"],
            name="fk_invalidations_revision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "severity IN ('soft','hard')",
            name="ck_invalidations_severity",
        ),
        sa.CheckConstraint(
            "status IN ('armed','partially_triggered','triggered','rearmed','retired')",
            name="ck_invalidations_status",
        ),
        sa.CheckConstraint(
            "(status='triggered') = (triggered_at IS NOT NULL)",
            name="ck_invalidations_triggered",
        ),
        sa.CheckConstraint(
            "(status='triggered') = (triggered_reason IS NOT NULL)",
            name="ck_invalidations_triggered_reason",
        ),
        sa.CheckConstraint(
            "confirmed_at >= proposed_at",
            name="ck_invalidations_confirmed_at",
        ),
    )
    op.create_index(
        "ix_invalidations_thesis_revision",
        "invalidation_conditions",
        ["thesis_id", "revision_no"],
    )
    op.create_index(
        "ix_invalidations_case_id", "invalidation_conditions", ["case_id"]
    )
    op.create_index(
        "ix_invalidations_status", "invalidation_conditions", ["status"]
    )

    op.create_table(
        "open_questions",
        sa.Column("question_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("asked_at", sa.Text(), nullable=False),
        sa.Column("answered_at", sa.Text(), nullable=True),
        sa.Column("answer_summary", sa.Text(), nullable=True),
        sa.Column("closed_without_answer_reason", sa.Text(), nullable=True),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("question_id", name="pk_open_questions"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_open_questions_case_id_investment_cases",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('open','answered','stale','closed_without_answer')",
            name="ck_open_questions_status",
        ),
        sa.CheckConstraint(
            "(status='answered') = (answered_at IS NOT NULL)",
            name="ck_open_questions_answered",
        ),
        sa.CheckConstraint(
            "(status='answered') = (answer_summary IS NOT NULL)",
            name="ck_open_questions_answer_summary",
        ),
        sa.CheckConstraint(
            "(status='closed_without_answer') = (closed_without_answer_reason IS NOT NULL)",
            name="ck_open_questions_closed_reason",
        ),
        sa.CheckConstraint(
            "answered_at IS NULL OR answered_at >= asked_at",
            name="ck_open_questions_answered_at",
        ),
    )
    op.create_index("ix_open_questions_case_id", "open_questions", ["case_id"])
    op.create_index("ix_open_questions_status", "open_questions", ["status"])

    op.create_table(
        "watchlist_items",
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("thesis_hint", sa.Text(), nullable=False),
        sa.Column("triggers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("promoted_to_case_id", sa.Text(), nullable=True),
        sa.Column("triggered_at", sa.Text(), nullable=True),
        sa.Column("triggered_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("item_id", name="pk_watchlist_items"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_watchlist_items_case_id_investment_cases",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["promoted_to_case_id"],
            ["investment_cases.case_id"],
            name="fk_watchlist_items_promoted_to_case_id_investment_cases",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('watching','triggered','promoted_to_case','expired','archived')",
            name="ck_watchlist_status",
        ),
        sa.CheckConstraint(
            "(status='promoted_to_case') = (promoted_to_case_id IS NOT NULL)",
            name="ck_watchlist_promoted",
        ),
        sa.CheckConstraint(
            "(status='triggered') = (triggered_at IS NOT NULL)",
            name="ck_watchlist_triggered",
        ),
        sa.CheckConstraint(
            "(status='triggered') = (triggered_reason IS NOT NULL)",
            name="ck_watchlist_triggered_reason",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_watchlist_updated_at",
        ),
        sa.CheckConstraint(
            "market IN ('A_SHARE','US')",
            name="ck_watchlist_market",
        ),
    )
    op.create_index("ix_watchlist_status", "watchlist_items", ["status"])
    op.create_index("ix_watchlist_case_id", "watchlist_items", ["case_id"])
    op.create_index(
        "ix_watchlist_market_symbol", "watchlist_items", ["market", "symbol"]
    )

    op.create_table(
        "candidate_thesis_revisions",
        sa.Column("candidate_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("thesis_id", sa.Text(), nullable=True),
        sa.Column("target_revision_no", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("confirmation_mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proposed_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.Column("proposed_by_rationale", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("candidate_id", name="pk_candidate_thesis_revisions"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_candidate_thesis_revisions_case_id_investment_cases",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_candidate_idempotency_key",
        ),
        sa.CheckConstraint(
            "kind IN ("
            "'thesis_revision','assumption','invalidation_condition',"
            "'open_question','watchlist_item','case_status_change'"
            ")",
            name="ck_candidate_kind",
        ),
        sa.CheckConstraint(
            "kind='watchlist_item' OR case_id IS NOT NULL",
            name="ck_candidate_case_scope",
        ),
        sa.CheckConstraint(
            "kind NOT IN ('assumption','invalidation_condition') OR thesis_id IS NOT NULL",
            name="ck_candidate_thesis_scope",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','confirmed','rejected','withdrawn','expired')",
            name="ck_candidate_status",
        ),
        sa.CheckConstraint(
            "confirmation_mode IN ('normal','strict_review')",
            name="ck_candidate_confirmation_mode",
        ),
        sa.CheckConstraint(
            "("
            "status IN ('proposed','expired') "
            "AND reviewed_at IS NULL AND reviewed_by IS NULL"
            ") OR ("
            "status IN ('confirmed','rejected','withdrawn') "
            "AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL"
            ")",
            name="ck_candidate_review_state",
        ),
        sa.CheckConstraint(
            "(status='rejected') = (rejection_reason IS NOT NULL)",
            name="ck_candidate_rejection_reason",
        ),
        sa.CheckConstraint(
            "status!='withdrawn' OR review_note IS NOT NULL",
            name="ck_candidate_withdraw_note",
        ),
    )
    op.create_index(
        "ix_candidate_case_id", "candidate_thesis_revisions", ["case_id"]
    )
    op.create_index(
        "ix_candidate_status", "candidate_thesis_revisions", ["status"]
    )
    op.create_index("ix_candidate_kind", "candidate_thesis_revisions", ["kind"])
    op.create_index(
        "ix_candidate_expires_at", "candidate_thesis_revisions", ["expires_at"]
    )

    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
        sa.column("applied_at", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.execute(
        schema_versions.insert().values(
            version=_PHASE1B_VERSION,
            applied_at=datetime.now(UTC).isoformat(),
            description=_PHASE1B_DESCRIPTION,
        )
    )


def downgrade() -> None:
    schema_versions = sa.table(
        "schema_versions",
        sa.column("version", sa.Text()),
    )
    op.execute(
        schema_versions.delete().where(schema_versions.c.version == _PHASE1B_VERSION)
    )

    op.drop_index("ix_candidate_expires_at", table_name="candidate_thesis_revisions")
    op.drop_index("ix_candidate_kind", table_name="candidate_thesis_revisions")
    op.drop_index("ix_candidate_status", table_name="candidate_thesis_revisions")
    op.drop_index("ix_candidate_case_id", table_name="candidate_thesis_revisions")
    op.drop_table("candidate_thesis_revisions")

    op.drop_index("ix_watchlist_market_symbol", table_name="watchlist_items")
    op.drop_index("ix_watchlist_case_id", table_name="watchlist_items")
    op.drop_index("ix_watchlist_status", table_name="watchlist_items")
    op.drop_table("watchlist_items")

    op.drop_index("ix_open_questions_status", table_name="open_questions")
    op.drop_index("ix_open_questions_case_id", table_name="open_questions")
    op.drop_table("open_questions")

    op.drop_index("ix_invalidations_status", table_name="invalidation_conditions")
    op.drop_index("ix_invalidations_case_id", table_name="invalidation_conditions")
    op.drop_index(
        "ix_invalidations_thesis_revision", table_name="invalidation_conditions"
    )
    op.drop_table("invalidation_conditions")

    op.drop_index("ix_assumptions_status", table_name="assumptions")
    op.drop_index("ix_assumptions_case_id", table_name="assumptions")
    op.drop_index("ix_assumptions_thesis_revision", table_name="assumptions")
    op.drop_table("assumptions")

    op.drop_index("ix_thesis_revisions_confirmed_at", table_name="thesis_revisions")
    op.drop_index("ix_thesis_revisions_case_id", table_name="thesis_revisions")
    op.drop_index("ix_thesis_revisions_thesis_id", table_name="thesis_revisions")
    op.drop_table("thesis_revisions")

    op.drop_index("ix_theses_parent", table_name="theses")
    op.drop_index("ix_theses_status", table_name="theses")
    op.drop_index("ix_theses_case_id", table_name="theses")
    op.drop_table("theses")

    op.drop_index(
        "ix_investment_cases_primary_instrument_id", table_name="investment_cases"
    )
    op.drop_index("ix_investment_cases_case_type", table_name="investment_cases")
    op.drop_index("ix_investment_cases_status", table_name="investment_cases")
    op.drop_table("investment_cases")
