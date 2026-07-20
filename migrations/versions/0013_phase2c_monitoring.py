"""Phase 2C durable Monitoring definitions, states, events, resolutions, and runs.

Revision ID: 0013_phase2c_monitoring
Revises: 0012_phase2b_risk_engine
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_phase2c_monitoring"
down_revision: str | Sequence[str] | None = "0012_phase2b_risk_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_identities",
        sa.Column("monitor_id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "monitor_versions",
        sa.Column(
            "monitor_id",
            sa.Text(),
            sa.ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
        ),
        sa.Column("primary_instrument_id", sa.Text()),
        sa.Column("cadence", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rules_json", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_monitor_versions_idempotency_key"),
        sa.CheckConstraint("version >= 1", name="ck_monitor_versions_version"),
        sa.CheckConstraint(
            "cadence IN ('ON_DEMAND','A_SHARE_POST_MARKET','US_POST_MARKET')",
            name="ck_monitor_versions_cadence",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','PAUSED','ARCHIVED')",
            name="ck_monitor_versions_status",
        ),
        sa.CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_monitor_versions_confirmed_by",
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_monitor_versions_schema"),
    )
    op.create_index(
        "ix_monitor_versions_monitor_version", "monitor_versions", ["monitor_id", "version"]
    )
    op.create_table(
        "monitor_rule_states",
        sa.Column(
            "monitor_id",
            sa.Text(),
            sa.ForeignKey("monitor_identities.monitor_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rule_code", sa.Text(), primary_key=True),
        sa.Column("monitor_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("observed_value", sa.Text()),
        sa.Column("fact_as_of", sa.Text()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "monitor_version >= 1", name="ck_monitor_rule_states_version"
        ),
        sa.CheckConstraint(
            "state IN ('QUIET','TRIGGERED','NOT_EVALUATED')",
            name="ck_monitor_rule_states_state",
        ),
    )
    op.create_table(
        "monitor_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column(
            "monitor_id",
            sa.Text(),
            sa.ForeignKey("monitor_identities.monitor_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("monitor_version", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("observed_value", sa.Text()),
        sa.Column("threshold_value", sa.Text()),
        sa.Column("fact_as_of", sa.Text()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("monitor_version >= 1", name="ck_monitor_events_version"),
        sa.CheckConstraint(
            "event_type IN ('TRIGGERED','RECOVERED','NOT_EVALUATED')",
            name="ck_monitor_events_type",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO','MEDIUM','HIGH')", name="ck_monitor_events_severity"
        ),
    )
    op.create_index(
        "ix_monitor_events_monitor_created", "monitor_events", ["monitor_id", "created_at"]
    )
    op.create_table(
        "monitor_event_resolutions",
        sa.Column("resolution_id", sa.Text(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Text(),
            sa.ForeignKey("monitor_events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_monitor_event_resolutions_idempotency_key"
        ),
        sa.CheckConstraint(
            "action IN ('ACKNOWLEDGE','RESOLVE')",
            name="ck_monitor_event_resolutions_action",
        ),
        sa.CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_monitor_event_resolutions_confirmed_by",
        ),
    )
    op.create_index(
        "ix_monitor_event_resolutions_event",
        "monitor_event_resolutions",
        ["event_id", "created_at"],
    )
    op.create_table(
        "monitor_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("requested_monitor_ids", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("monitors_evaluated", sa.Integer(), nullable=False),
        sa.Column("rules_evaluated", sa.Integer(), nullable=False),
        sa.Column("events_created", sa.Integer(), nullable=False),
        sa.Column("warning_codes", sa.Text(), nullable=False),
        sa.Column("error_codes", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED')", name="ck_monitor_runs_status"
        ),
        sa.CheckConstraint("completed_at >= started_at", name="ck_monitor_runs_time_order"),
    )
    op.create_index("ix_monitor_runs_completed", "monitor_runs", ["completed_at"])
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES (
            '0013_phase2c_monitoring',
            '2026-07-20T00:00:00+00:00',
            'Durable Phase 2C Monitoring definitions and state transitions'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0013_phase2c_monitoring'")
    op.drop_index("ix_monitor_runs_completed", table_name="monitor_runs")
    op.drop_table("monitor_runs")
    op.drop_index(
        "ix_monitor_event_resolutions_event", table_name="monitor_event_resolutions"
    )
    op.drop_table("monitor_event_resolutions")
    op.drop_index("ix_monitor_events_monitor_created", table_name="monitor_events")
    op.drop_table("monitor_events")
    op.drop_table("monitor_rule_states")
    op.drop_index("ix_monitor_versions_monitor_version", table_name="monitor_versions")
    op.drop_table("monitor_versions")
    op.drop_table("monitor_identities")
