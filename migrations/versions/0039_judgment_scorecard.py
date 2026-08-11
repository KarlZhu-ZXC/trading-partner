"""Add append-only Judgment Scorecard S0 runs.

Revision ID: 0039_judgment_scorecard
Revises: 0038_trade_retro_reviews
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_judgment_scorecard"
down_revision: str | None = "0038_trade_retro_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "judgment_scorecard_runs",
        sa.Column("scorecard_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("subject_title", sa.Text(), nullable=False),
        sa.Column("thesis_id", sa.Text(), nullable=False),
        sa.Column("thesis_title", sa.Text(), nullable=False),
        sa.Column("thesis_revision_id", sa.Text(), nullable=False),
        sa.Column("thesis_revision_no", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("dimensions_json", sa.Text(), nullable=False),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("execution_effect", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('COMPLETE','PARTIAL','NOT_EVALUATED')",
            name="judgment_scorecard_status",
        ),
        sa.CheckConstraint("schema_version = 1", name="judgment_scorecard_schema"),
        sa.CheckConstraint("execution_effect = 0", name="judgment_scorecard_no_execution"),
        sa.CheckConstraint("thesis_revision_no >= 1", name="judgment_scorecard_revision_no"),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["investment_cases.case_id"],
            name="fk_judgment_scorecard_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id"],
            ["theses.thesis_id"],
            name="fk_judgment_scorecard_thesis",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_revision_id"],
            ["thesis_revisions.revision_id"],
            name="fk_judgment_scorecard_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("scorecard_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_judgment_scorecard_idempotency_key"),
    )
    op.create_index(
        "ix_judgment_scorecard_subject_generated",
        "judgment_scorecard_runs",
        ["case_id", "generated_at"],
    )
    op.create_index(
        "ix_judgment_scorecard_thesis_generated",
        "judgment_scorecard_runs",
        ["thesis_id", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_judgment_scorecard_thesis_generated",
        table_name="judgment_scorecard_runs",
    )
    op.drop_index(
        "ix_judgment_scorecard_subject_generated",
        table_name="judgment_scorecard_runs",
    )
    op.drop_table("judgment_scorecard_runs")
