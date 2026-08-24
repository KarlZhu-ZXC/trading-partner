"""Add durable lease/heartbeat receipts for scheduled operational jobs.

Revision ID: 0052_operational_job_runs
Revises: 0051_moomoo_margin_semantics
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_operational_job_runs"
down_revision: str | None = "0051_moomoo_margin_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_job_runs",
        sa.Column("job_run_id", sa.Text(), primary_key=True),
        sa.Column("job_name", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_owner_hash", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.Text(), nullable=False),
        sa.Column("heartbeat_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
        sa.Column("result_code", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("job_name", "idempotency_key", name="uq_operational_job_key"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','SKIPPED','FAILED','INTERRUPTED')",
            name="ck_operational_job_status",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_operational_job_attempt"),
        sa.CheckConstraint("version >= 1", name="ck_operational_job_version"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_operational_job_terminal_time",
        ),
    )
    op.create_index(
        "ix_operational_job_status_lease",
        "operational_job_runs",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_operational_job_updated",
        "operational_job_runs",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_operational_job_updated", table_name="operational_job_runs")
    op.drop_index("ix_operational_job_status_lease", table_name="operational_job_runs")
    op.drop_table("operational_job_runs")
