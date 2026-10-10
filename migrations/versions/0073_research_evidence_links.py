"""Persist confirmed evidence impacts and research-question metadata."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_research_evidence_links"
down_revision: str | None = "0072_external_note_review_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PREVIOUS_CANDIDATE_KINDS = (
    "'thesis_revision','assumption','invalidation_condition',"
    "'open_question','watchlist_item','case_status_change','trade_plan'"
)
_CURRENT_CANDIDATE_KINDS = (
    "'thesis_revision','assumption','invalidation_condition',"
    "'open_question','evidence_impact','watchlist_item','case_status_change','trade_plan'"
)
_PREVIOUS_REVIEW_SOURCES = (
    "'CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
    "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT','DECISION_REVIEW_DUE',"
    "'OBSERVATION_REVIEW_DUE','UNLINKED_ACTIVITY'"
)
_CURRENT_REVIEW_SOURCES = _PREVIOUS_REVIEW_SOURCES + ",'OPEN_QUESTION_DUE'"


def upgrade() -> None:
    with op.batch_alter_table("open_questions") as batch:
        batch.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column(
                "evidence_requirements_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )
        batch.add_column(sa.Column("due_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("progress_note", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("source_refs_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column("decision_ids_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch.create_check_constraint("ck_open_questions_version", "version >= 1")
    op.create_index(
        "ix_open_questions_due", "open_questions", ["status", "due_at"]
    )

    with op.batch_alter_table("candidate_thesis_revisions") as batch:
        batch.drop_constraint("ck_candidate_kind", type_="check")
        batch.create_check_constraint(
            "ck_candidate_kind", f"kind IN ({_CURRENT_CANDIDATE_KINDS})"
        )

    # The ORM already advertises this source type; keep the existing database
    # CHECK in sync so due-question projections can be materialized safely.
    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            f"source_type IN ({_CURRENT_REVIEW_SOURCES})",
        )

    op.create_table(
        "evidence_impacts",
        sa.Column("impact_id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "thesis_id",
            sa.Text(),
            sa.ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "thesis_revision_id",
            sa.Text(),
            sa.ForeignKey("thesis_revisions.revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assumption_id",
            sa.Text(),
            sa.ForeignKey("assumptions.assumption_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_monitor_id", sa.Text(), nullable=True),
        sa.Column("source_rule_code", sa.Text(), nullable=True),
        sa.Column("stance", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column(
            "candidate_id",
            sa.Text(),
            sa.ForeignKey("candidate_thesis_revisions.candidate_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint("candidate_id", name="uq_evidence_impacts_candidate"),
        sa.CheckConstraint("version >= 1", name="ck_evidence_impacts_version"),
        sa.CheckConstraint(
            "stance IN ('supports','contradicts','uncertain')",
            name="ck_evidence_impacts_stance",
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE','RETRACTED')",
            name="ck_evidence_impacts_state",
        ),
        sa.CheckConstraint(
            "source_kind IN ('EVIDENCE','OBSERVATION','MONITOR')",
            name="ck_evidence_impacts_source_kind",
        ),
        sa.CheckConstraint(
            "(source_kind = 'MONITOR' AND source_monitor_id IS NOT NULL "
            "AND source_rule_code IS NOT NULL) OR "
            "(source_kind != 'MONITOR' AND source_monitor_id IS NULL "
            "AND source_rule_code IS NULL)",
            name="ck_evidence_impacts_source_scope",
        ),
    )
    op.create_index(
        "ix_evidence_impacts_subject",
        "evidence_impacts",
        ["case_id", "confirmed_at"],
    )
    op.create_index(
        "ix_evidence_impacts_latest",
        "evidence_impacts",
        ["case_id", "impact_id", "version"],
    )


def downgrade() -> None:
    # Dropping the new columns/tables and restoring the old CHECKs would lose
    # confirmed links or make a later SQLite batch recreation fail.  Refuse
    # before *any* DDL when the feature has durable data; operators must first
    # restore a verified backup or explicitly archive those rows through a
    # future compatibility migration.
    connection = op.get_bind()
    checks = (
        (
            "evidence impact rows",
            "SELECT 1 FROM evidence_impacts LIMIT 1",
        ),
        (
            "evidence impact candidates",
            "SELECT 1 FROM candidate_thesis_revisions "
            "WHERE kind = 'evidence_impact' LIMIT 1",
        ),
        (
            "research question candidate metadata",
            "SELECT 1 FROM candidate_thesis_revisions WHERE kind = 'open_question' "
            "AND (json_type(payload_json, '$.evidence_requirements') IS NOT NULL "
            "OR json_type(payload_json, '$.expected_version') IS NOT NULL "
            "OR json_type(payload_json, '$.source_refs') IS NOT NULL) LIMIT 1",
        ),
        (
            "research question metadata",
            "SELECT 1 FROM open_questions WHERE version != 1 "
            "OR evidence_requirements_json NOT IN ('[]','') "
            "OR due_at IS NOT NULL OR progress_note IS NOT NULL "
            "OR source_refs_json NOT IN ('[]','') "
            "OR decision_ids_json NOT IN ('[]','') LIMIT 1",
        ),
        (
            "open-question due review items",
            "SELECT 1 FROM review_items "
            "WHERE source_type = 'OPEN_QUESTION_DUE' LIMIT 1",
        ),
    )
    for label, statement in checks:
        if connection.execute(sa.text(statement)).first() is not None:
            raise RuntimeError(
                "Cannot downgrade 0073 while durable "
                f"{label} exist; restore a verified backup before retrying."
            )

    op.drop_index("ix_evidence_impacts_latest", table_name="evidence_impacts")
    op.drop_index("ix_evidence_impacts_subject", table_name="evidence_impacts")
    op.drop_table("evidence_impacts")

    with op.batch_alter_table("review_items") as batch:
        batch.drop_constraint("ck_review_items_source_type", type_="check")
        batch.create_check_constraint(
            "ck_review_items_source_type",
            f"source_type IN ({_PREVIOUS_REVIEW_SOURCES})",
        )

    with op.batch_alter_table("candidate_thesis_revisions") as batch:
        batch.drop_constraint("ck_candidate_kind", type_="check")
        batch.create_check_constraint(
            "ck_candidate_kind", f"kind IN ({_PREVIOUS_CANDIDATE_KINDS})"
        )

    op.drop_index("ix_open_questions_due", table_name="open_questions")
    with op.batch_alter_table("open_questions") as batch:
        batch.drop_constraint("ck_open_questions_version", type_="check")
        batch.drop_column("decision_ids_json")
        batch.drop_column("source_refs_json")
        batch.drop_column("progress_note")
        batch.drop_column("due_at")
        batch.drop_column("evidence_requirements_json")
        batch.drop_column("version")
