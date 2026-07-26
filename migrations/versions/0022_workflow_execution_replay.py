"""Durable workflow execution claims and replayable fact artifacts.

Revision ID: 0022_workflow_execution_replay
Revises: 0021_challenge_review_idempotency
Create Date: 2026-07-26
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_workflow_execution_replay"
down_revision: str | Sequence[str] | None = "0021_challenge_review_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HEX64_CHECK = "length({col}) = 64 AND {col} = lower({col}) AND {col} NOT GLOB '*[^0-9a-f]*'"


def upgrade() -> None:
    with op.batch_alter_table("research_runs") as batch:
        batch.drop_constraint("ck_research_runs_status", type_="check")
        batch.alter_column("completed_at", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("idempotency_key", sa.Text()))
        batch.add_column(sa.Column("request_payload_sha256", sa.Text()))
        batch.add_column(sa.Column("heartbeat_at", sa.Text()))
        batch.add_column(sa.Column("lease_expires_at", sa.Text()))
        batch.add_column(
            sa.Column("missing_capabilities", sa.Text(), nullable=False, server_default="[]")
        )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT run_id, completed_at, started_at FROM research_runs")
    ).mappings()
    for row in rows:
        run_id = str(row["run_id"])
        payload_sha256 = hashlib.sha256(f"legacy-workflow:{run_id}".encode()).hexdigest()
        heartbeat = row["completed_at"] or row["started_at"]
        connection.execute(
            sa.text(
                "UPDATE research_runs SET idempotency_key=:key, "
                "request_payload_sha256=:sha, heartbeat_at=:heartbeat, "
                "lease_expires_at=:heartbeat, "
                "status=CASE WHEN status='complete' THEN 'succeeded' ELSE status END "
                "WHERE run_id=:run_id"
            ),
            {
                "key": f"legacy-workflow-{run_id}",
                "sha": payload_sha256,
                "heartbeat": heartbeat,
                "run_id": run_id,
            },
        )

    with op.batch_alter_table("research_runs") as batch:
        batch.alter_column("idempotency_key", existing_type=sa.Text(), nullable=False)
        batch.alter_column("request_payload_sha256", existing_type=sa.Text(), nullable=False)
        batch.alter_column("heartbeat_at", existing_type=sa.Text(), nullable=False)
        batch.alter_column("lease_expires_at", existing_type=sa.Text(), nullable=False)
        batch.create_unique_constraint("uq_research_runs_idempotency_key", ["idempotency_key"])
        batch.create_check_constraint(
            "ck_research_runs_status",
            "status IN ('started','running','succeeded','partial','failed')",
        )
        batch.create_check_constraint(
            "ck_research_runs_terminal_time",
            "(status IN ('started','running') AND completed_at IS NULL) OR "
            "(status IN ('succeeded','partial','failed') AND completed_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_research_runs_request_payload_sha256",
            _HEX64_CHECK.format(col="request_payload_sha256"),
        )

    op.create_table(
        "research_run_fact_artifacts",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "ordinal"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.run_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            _HEX64_CHECK.format(col="payload_sha256"),
            name="ck_research_run_fact_artifacts_payload_sha256",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0 AND size_bytes <= 1048576",
            name="ck_research_run_fact_artifacts_size",
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) VALUES "
            "('0022_workflow_execution_replay', '2026-07-26T00:00:00+00:00', "
            "'Idempotent workflow execution state and replayable fact artifacts')"
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0022_workflow_execution_replay'")
    op.drop_table("research_run_fact_artifacts")
    with op.batch_alter_table("research_runs") as batch:
        batch.drop_constraint("ck_research_runs_request_payload_sha256", type_="check")
        batch.drop_constraint("ck_research_runs_terminal_time", type_="check")
        batch.drop_constraint("ck_research_runs_status", type_="check")
        batch.drop_constraint("uq_research_runs_idempotency_key", type_="unique")
    op.execute(
        "UPDATE research_runs SET completed_at=COALESCE(completed_at, heartbeat_at), "
        "status=CASE WHEN status='succeeded' THEN 'complete' "
        "WHEN status IN ('started','running') THEN 'failed' ELSE status END"
    )
    with op.batch_alter_table("research_runs") as batch:
        batch.drop_column("missing_capabilities")
        batch.drop_column("lease_expires_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("request_payload_sha256")
        batch.drop_column("idempotency_key")
        batch.alter_column("completed_at", existing_type=sa.Text(), nullable=False)
        batch.create_check_constraint(
            "ck_research_runs_status",
            "status IN ('complete','partial','failed')",
        )
