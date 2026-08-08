"""Add versioned composite Monitor judgment policies and immutable results.

Revision ID: 0032_monitor_llm_judgments
Revises: 0031_live_primary_thesis
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_monitor_llm_judgments"
down_revision: str | None = "0031_live_primary_thesis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("monitor_versions") as batch:
        batch.add_column(sa.Column("judgment_policy_json", sa.Text()))
        batch.drop_constraint("ck_monitor_versions_schema", type_="check")
        batch.create_check_constraint("ck_monitor_versions_schema", "schema_version IN (1,2,3)")
    with op.batch_alter_table("monitor_events") as batch:
        batch.drop_constraint("ck_monitor_events_type", type_="check")
        batch.create_check_constraint(
            "ck_monitor_events_type",
            "event_type IN ('TRIGGERED','RECOVERED','NOT_EVALUATED',"
            "'JUDGMENT_CHANGED','JUDGMENT_UNAVAILABLE')",
        )
    op.create_table(
        "monitor_judgments",
        sa.Column("judgment_id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("monitor_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "monitor_id",
            sa.Text(),
            sa.ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("monitor_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("urgency", sa.Text()),
        sa.Column("phase", sa.Text()),
        sa.Column("market_state", sa.Text()),
        sa.Column("divergence", sa.Text()),
        sa.Column("conclusion", sa.Text()),
        sa.Column("quantity_min", sa.Integer()),
        sa.Column("quantity_max", sa.Integer()),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_feature_ids", sa.Text(), nullable=False),
        sa.Column("next_trigger", sa.Text()),
        sa.Column("invalidation", sa.Text()),
        sa.Column("feature_signature", sa.Text(), nullable=False),
        sa.Column("result_fingerprint", sa.Text()),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("reasoning_effort", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("warning_codes", sa.Text(), nullable=False),
        sa.Column("error_codes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','SKIPPED','FAILED')",
            name="ck_monitor_judgments_status",
        ),
    )
    op.create_index(
        "ix_monitor_judgments_monitor_created",
        "monitor_judgments",
        ["monitor_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_monitor_judgments_monitor_created", table_name="monitor_judgments")
    op.drop_table("monitor_judgments")
    with op.batch_alter_table("monitor_events") as batch:
        batch.drop_constraint("ck_monitor_events_type", type_="check")
        batch.create_check_constraint(
            "ck_monitor_events_type",
            "event_type IN ('TRIGGERED','RECOVERED','NOT_EVALUATED')",
        )
    with op.batch_alter_table("monitor_versions") as batch:
        batch.drop_constraint("ck_monitor_versions_schema", type_="check")
        batch.create_check_constraint("ck_monitor_versions_schema", "schema_version IN (1,2)")
        batch.drop_column("judgment_policy_json")
