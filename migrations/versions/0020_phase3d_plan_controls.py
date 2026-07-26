"""Phase 3D Trade Plans, sizing policy, and Monitoring v2 links.

Revision ID: 0020_phase3d_plan_controls
Revises: 0019_phase3a_futures_statistics
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_phase3d_plan_controls"
down_revision: str | Sequence[str] | None = "0019_phase3a_futures_statistics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_thesis_revisions") as batch:
        batch.drop_constraint("ck_candidate_kind", type_="check")
        batch.create_check_constraint(
            "ck_candidate_kind",
            "kind IN ("
            "'thesis_revision','assumption','invalidation_condition',"
            "'open_question','watchlist_item','case_status_change','trade_plan'"
            ")",
        )
    op.create_table(
        "trade_plan_identities",
        sa.Column("plan_id", sa.Text(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("investment_cases.case_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("case_id", name="uq_trade_plan_identities_case_id"),
    )
    op.create_table(
        "trade_plan_versions",
        sa.Column(
            "plan_id",
            sa.Text(),
            sa.ForeignKey("trade_plan_identities.plan_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column(
            "thesis_id",
            sa.Text(),
            sa.ForeignKey("theses.thesis_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.Text()),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("reference_price", sa.Text(), nullable=False),
        sa.Column("reference_price_at", sa.Text(), nullable=False),
        sa.Column("target_position_percent", sa.Text(), nullable=False),
        sa.Column("max_position_percent", sa.Text(), nullable=False),
        sa.Column("risk_budget_percent", sa.Text(), nullable=False),
        sa.Column("stop_price", sa.Text()),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_trade_plan_versions_idempotency_key"),
        sa.CheckConstraint("version >= 1", name="ck_trade_plan_versions_version"),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED')",
            name="ck_trade_plan_versions_status",
        ),
        sa.CheckConstraint(
            "confirmed_by IN ('user','external_agent')",
            name="ck_trade_plan_versions_confirmed_by",
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_trade_plan_versions_schema"),
    )
    op.create_index(
        "ix_trade_plan_versions_plan_version",
        "trade_plan_versions",
        ["plan_id", "version"],
    )
    op.create_table(
        "trade_plan_conditions",
        sa.Column("plan_id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("condition_code", sa.Text(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("fact_type", sa.Text()),
        sa.Column("metric_key", sa.Text()),
        sa.Column("comparator", sa.Text()),
        sa.Column("threshold", sa.Text()),
        sa.Column("unit", sa.Text()),
        sa.Column("instrument_id", sa.Text()),
        sa.Column("max_fact_age_seconds", sa.Integer()),
        sa.Column("event_after", sa.Text()),
        sa.ForeignKeyConstraint(
            ["plan_id", "version"],
            ["trade_plan_versions.plan_id", "trade_plan_versions.version"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("position >= 0", name="ck_trade_plan_conditions_position"),
        sa.CheckConstraint(
            "phase IN ('ENTRY','SCALE','EXIT','INVALIDATION','REVIEW')",
            name="ck_trade_plan_conditions_phase",
        ),
        sa.CheckConstraint(
            "mode IN ('MANUAL','MONITORABLE')",
            name="ck_trade_plan_conditions_mode",
        ),
    )
    with op.batch_alter_table("monitor_versions") as batch:
        batch.add_column(sa.Column("trade_plan_id", sa.Text()))
        batch.add_column(sa.Column("trade_plan_version", sa.Integer()))
        batch.create_foreign_key(
            "fk_monitor_versions_trade_plan_version",
            "trade_plan_versions",
            ["trade_plan_id", "trade_plan_version"],
            ["plan_id", "version"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("risk_policies") as batch:
        batch.add_column(
            sa.Column("risk_budget_max_percent", sa.Text(), nullable=False, server_default="2")
        )
        batch.add_column(
            sa.Column(
                "theme_exposure_max_percent", sa.Text(), nullable=False, server_default="40"
            )
        )
        batch.add_column(
            sa.Column("drawdown_max_percent", sa.Text(), nullable=False, server_default="20")
        )
        batch.add_column(
            sa.Column(
                "liquidity_participation_max_percent",
                sa.Text(),
                nullable=False,
                server_default="10",
            )
        )
        batch.add_column(
            sa.Column(
                "correlation_max_absolute", sa.Text(), nullable=False, server_default="0.85"
            )
        )
        batch.add_column(
            sa.Column("event_blackout_days", sa.Integer(), nullable=False, server_default="3")
        )
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES (
            '0020_phase3d_plan_controls',
            '2026-07-26T00:00:00+00:00',
            'Phase 3D versioned Trade Plans and deterministic control chain'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0020_phase3d_plan_controls'")
    with op.batch_alter_table("monitor_versions") as batch:
        batch.drop_constraint(
            "fk_monitor_versions_trade_plan_version", type_="foreignkey"
        )
        batch.drop_column("trade_plan_version")
        batch.drop_column("trade_plan_id")
    with op.batch_alter_table("risk_policies") as batch:
        batch.drop_column("event_blackout_days")
        batch.drop_column("correlation_max_absolute")
        batch.drop_column("liquidity_participation_max_percent")
        batch.drop_column("drawdown_max_percent")
        batch.drop_column("theme_exposure_max_percent")
        batch.drop_column("risk_budget_max_percent")
    op.drop_table("trade_plan_conditions")
    op.drop_index("ix_trade_plan_versions_plan_version", table_name="trade_plan_versions")
    op.drop_table("trade_plan_versions")
    op.drop_table("trade_plan_identities")
    with op.batch_alter_table("candidate_thesis_revisions") as batch:
        batch.drop_constraint("ck_candidate_kind", type_="check")
        batch.create_check_constraint(
            "ck_candidate_kind",
            "kind IN ("
            "'thesis_revision','assumption','invalidation_condition',"
            "'open_question','watchlist_item','case_status_change'"
            ")",
        )
