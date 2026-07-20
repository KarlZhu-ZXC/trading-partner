"""Phase 1K persistent Challenge Reviews.

Revision ID: 0007_phase1k_challenge_reviews
Revises: 0006_phase1i_account_portfolio
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_phase1k_challenge_reviews"
down_revision: str | Sequence[str] | None = "0006_phase1i_account_portfolio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "challenge_reviews",
        sa.Column("review_id", sa.Text(), primary_key=True),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("proposed_action", sa.Text(), nullable=False),
        sa.Column("related_candidate_id", sa.Text()),
        sa.Column("related_evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("position_context_snapshot_id", sa.Text()),
        sa.Column("context_as_of", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text()),
        sa.Column("resolution_rationale", sa.Text()),
        sa.Column("resolved_at", sa.Text()),
        sa.Column("confirmed_by", sa.Text()),
        sa.ForeignKeyConstraint(["case_id"], ["investment_cases.case_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("mode = 'strict_review'", name="ck_challenge_reviews_mode"),
        sa.CheckConstraint("status IN ('open','resolved')", name="ck_challenge_reviews_status"),
    )
    op.create_index(
        "ix_challenge_reviews_case_created", "challenge_reviews", ["case_id", "created_at"]
    )
    op.create_table(
        "challenge_questions",
        sa.Column("question_id", sa.Text(), primary_key=True),
        sa.Column("review_id", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("question_set_version", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_id"], ["challenge_reviews.review_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("review_id", "ordinal", name="uq_challenge_question_ordinal"),
    )
    op.create_table(
        "challenge_findings",
        sa.Column("finding_id", sa.Text(), primary_key=True),
        sa.Column("review_id", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_id"], ["challenge_reviews.review_id"], ondelete="RESTRICT"
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES ('0007_phase1k_challenge_reviews', '2026-07-18T00:00:00+00:00', "
            "'Phase 1K persistent Challenge Reviews')"
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0007_phase1k_challenge_reviews'")
    op.drop_table("challenge_findings")
    op.drop_table("challenge_questions")
    op.drop_index("ix_challenge_reviews_case_created", table_name="challenge_reviews")
    op.drop_table("challenge_reviews")
