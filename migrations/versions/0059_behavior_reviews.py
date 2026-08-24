"""Add append-only cross-period Behavior Review Runs and action observations.

Revision ID: 0059_behavior_reviews
Revises: 0058_trade_cycle_overrides
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_behavior_reviews"
down_revision: str | None = "0058_trade_cycle_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "behavior_review_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("period_kind", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Text(), nullable=False),
        sa.Column("period_end", sa.Text(), nullable=False),
        sa.Column("cohort_key", sa.Text(), nullable=False),
        sa.Column("cohort_json", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_read_complete", sa.Integer(), nullable=False),
        sa.Column("source_error_code", sa.Text(), nullable=True),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("execution_effect", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "period_kind IN ('WEEKLY','MONTHLY','QUARTERLY')",
            name="behavior_review_runs_period_kind",
        ),
        sa.CheckConstraint(
            "status IN ('COMPLETE','INCOMPLETE','UNAVAILABLE')",
            name="behavior_review_runs_status",
        ),
        sa.CheckConstraint(
            "source_read_complete IN (0,1)",
            name="behavior_review_runs_source_complete",
        ),
        sa.CheckConstraint("schema_version = 1", name="behavior_review_runs_schema"),
        sa.CheckConstraint("execution_effect = 0", name="behavior_review_runs_no_execution"),
        sa.UniqueConstraint("idempotency_key", name="uq_behavior_review_runs_idempotency"),
    )
    op.create_index(
        "ix_behavior_review_runs_period",
        "behavior_review_runs",
        ["period_kind", "period_start", "period_end"],
    )
    op.create_index(
        "ix_behavior_review_runs_cohort_generated",
        "behavior_review_runs",
        ["cohort_key", "generated_at"],
    )
    op.create_table(
        "behavior_action_observations",
        sa.Column("observation_id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("behavior_review_runs.run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("action_text", sa.Text(), nullable=False),
        sa.Column("action_code", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.Text(), nullable=False),
        sa.Column("cohort_key", sa.Text(), nullable=False),
        sa.Column("review_item_source_keys_json", sa.Text(), nullable=False),
        sa.Column("retro_review_ids_json", sa.Text(), nullable=False),
        sa.Column("cycle_ids_json", sa.Text(), nullable=False),
        sa.Column("decision_ids_json", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("previous_observation_id", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.Text(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('NEW','PERSISTENT','RESOLVED','RECURRED')",
            name="behavior_action_status",
        ),
        sa.CheckConstraint("occurrence_count >= 1", name="behavior_action_occurrence"),
        sa.UniqueConstraint("run_id", "stable_key", name="uq_behavior_action_run_key"),
    )
    op.create_index(
        "ix_behavior_action_stable_observed",
        "behavior_action_observations",
        ["stable_key", "observed_at"],
    )
    op.create_index(
        "ix_behavior_action_run_status",
        "behavior_action_observations",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_behavior_action_run_status",
        table_name="behavior_action_observations",
    )
    op.drop_index(
        "ix_behavior_action_stable_observed",
        table_name="behavior_action_observations",
    )
    op.drop_table("behavior_action_observations")
    op.drop_index(
        "ix_behavior_review_runs_cohort_generated",
        table_name="behavior_review_runs",
    )
    op.drop_index("ix_behavior_review_runs_period", table_name="behavior_review_runs")
    op.drop_table("behavior_review_runs")
