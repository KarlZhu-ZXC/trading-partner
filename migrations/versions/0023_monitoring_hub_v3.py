"""Monitoring Hub schedules and immutable run observations.

Revision ID: 0023_monitoring_hub_v3
Revises: 0022_workflow_execution_replay
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_monitoring_hub_v3"
down_revision: str | Sequence[str] | None = "0022_workflow_execution_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("monitor_versions") as batch:
        batch.drop_constraint("ck_monitor_versions_cadence", type_="check")
        batch.drop_constraint("ck_monitor_versions_schema", type_="check")
        batch.add_column(sa.Column("interval_minutes", sa.Integer()))
        batch.create_check_constraint(
            "ck_monitor_versions_cadence",
            "cadence IN ("
            "'ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET','US_POST_MARKET'"
            ")",
        )
        batch.create_check_constraint(
            "ck_monitor_versions_schema",
            "schema_version IN (1,2)",
        )
        batch.create_check_constraint(
            "ck_monitor_versions_interval",
            "(cadence = 'INTERVAL' AND interval_minutes >= 60 "
            "AND interval_minutes % 60 = 0) OR "
            "(cadence != 'INTERVAL' AND interval_minutes IS NULL)",
        )
    with op.batch_alter_table("monitor_runs") as batch:
        batch.add_column(
            sa.Column(
                "selected_monitor_ids",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )
        batch.add_column(sa.Column("cadence", sa.Text()))
        batch.add_column(
            sa.Column(
                "observation_history_complete",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_check_constraint(
            "ck_monitor_runs_cadence",
            "cadence IS NULL OR cadence IN ("
            "'ON_DEMAND','INTERVAL','A_SHARE_POST_MARKET','US_POST_MARKET'"
            ")",
        )
    op.create_table(
        "monitor_run_observations",
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("monitor_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "monitor_id",
            sa.Text(),
            sa.ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("rule_code", sa.Text(), primary_key=True),
        sa.Column("monitor_version", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Text()),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("observed_value", sa.Text()),
        sa.Column("threshold_value", sa.Text()),
        sa.Column("distance_value", sa.Text()),
        sa.Column("distance_percent", sa.Text()),
        sa.Column("fact_as_of", sa.Text()),
        sa.Column("fact_age_seconds", sa.Integer()),
        sa.Column("warning_codes", sa.Text(), nullable=False),
        sa.Column("error_codes", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "state IN ('QUIET','TRIGGERED','NOT_EVALUATED')",
            name="ck_monitor_run_observations_state",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO','MEDIUM','HIGH')",
            name="ck_monitor_run_observations_severity",
        ),
    )
    op.create_index(
        "ix_monitor_run_observations_monitor_run",
        "monitor_run_observations",
        ["monitor_id", "run_id"],
    )
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES (
            '0023_monitoring_hub_v3',
            '2026-07-29T00:00:00+00:00',
            'Monitoring Hub interval schedules and immutable run observations'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0023_monitoring_hub_v3'")
    op.drop_index(
        "ix_monitor_run_observations_monitor_run",
        table_name="monitor_run_observations",
    )
    op.drop_table("monitor_run_observations")
    with op.batch_alter_table("monitor_runs") as batch:
        batch.drop_constraint("ck_monitor_runs_cadence", type_="check")
        batch.drop_column("observation_history_complete")
        batch.drop_column("cadence")
        batch.drop_column("selected_monitor_ids")
    # The prior schema has no interval cadence or v2 definition representation.
    # Preserve the definition/rules while degrading its schedule to ON_DEMAND.
    op.execute(
        "UPDATE monitor_versions SET cadence = 'ON_DEMAND' "
        "WHERE cadence = 'INTERVAL'"
    )
    op.execute("UPDATE monitor_versions SET schema_version = 1")
    with op.batch_alter_table("monitor_versions") as batch:
        batch.drop_constraint("ck_monitor_versions_interval", type_="check")
        batch.drop_constraint("ck_monitor_versions_schema", type_="check")
        batch.drop_constraint("ck_monitor_versions_cadence", type_="check")
        batch.drop_column("interval_minutes")
        batch.create_check_constraint(
            "ck_monitor_versions_cadence",
            "cadence IN ('ON_DEMAND','A_SHARE_POST_MARKET','US_POST_MARKET')",
        )
        batch.create_check_constraint(
            "ck_monitor_versions_schema",
            "schema_version = 1",
        )
