"""Add immutable Trade Retro plan, run, and export records.

Revision ID: 0037_trade_retro
Revises: 0036_monitor_provider_diagnostics
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_trade_retro"
down_revision: str | None = "0036_monitor_provider_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_retro_plan_snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Text(), nullable=False),
        sa.Column("period_end", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.Text(), nullable=False),
        sa.Column("entries_json", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("schema_version = 1", name="trade_retro_plan_snapshot_schema"),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_trade_retro_plan_snapshot_key"),
    )
    op.create_index(
        "ix_trade_retro_plan_period",
        "trade_retro_plan_snapshots",
        ["period_start", "period_end", "captured_at"],
    )
    op.create_table(
        "trade_retro_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Text(), nullable=False),
        sa.Column("period_end", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("plan_snapshot_id", sa.Text(), nullable=True),
        sa.Column("transaction_ids_json", sa.Text(), nullable=False),
        sa.Column("findings_json", sa.Text(), nullable=False),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.Column("summary_markdown", sa.Text(), nullable=False),
        sa.Column("llm_provider", sa.Text(), nullable=True),
        sa.Column("llm_model", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("execution_effect", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('COMPLETE','INCOMPLETE')", name="trade_retro_run_status"
        ),
        sa.CheckConstraint("schema_version = 1", name="trade_retro_run_schema"),
        sa.CheckConstraint("execution_effect = 0", name="trade_retro_no_execution"),
        sa.ForeignKeyConstraint(
            ["plan_snapshot_id"],
            ["trade_retro_plan_snapshots.snapshot_id"],
            name="fk_trade_retro_run_plan_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_trade_retro_run_key"),
    )
    op.create_index(
        "ix_trade_retro_runs_period",
        "trade_retro_runs",
        ["period_end", "generated_at"],
    )
    op.create_table(
        "trade_retro_export_receipts",
        sa.Column("receipt_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("exported_at", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["trade_retro_runs.run_id"],
            name="fk_trade_retro_export_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("receipt_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_trade_retro_export_key"),
    )
    op.create_index(
        "ix_trade_retro_exports_run",
        "trade_retro_export_receipts",
        ["run_id", "exported_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trade_retro_exports_run", table_name="trade_retro_export_receipts")
    op.drop_table("trade_retro_export_receipts")
    op.drop_index("ix_trade_retro_runs_period", table_name="trade_retro_runs")
    op.drop_table("trade_retro_runs")
    op.drop_index("ix_trade_retro_plan_period", table_name="trade_retro_plan_snapshots")
    op.drop_table("trade_retro_plan_snapshots")
