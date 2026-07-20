"""Phase 2B risk-policy table and seeded system-default policy.

Revision ID: 0012_phase2b_risk_engine
Revises: 0010_post_market_sync_runs
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_phase2b_risk_engine"
down_revision: str | Sequence[str] | None = "0010_post_market_sync_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SYSTEM_DEFAULT_POLICY_ID = "risk_policy_00000000-0000-7000-8000-000000000001"
SYSTEM_DEFAULT_IDEMPOTENCY_KEY = "system_default_policy_0012"
SYSTEM_DEFAULT_CREATED_AT = "2026-07-20T00:00:00+00:00"


def upgrade() -> None:
    op.create_table(
        "risk_policies",
        sa.Column("policy_id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("single_position_max_percent", sa.Text(), nullable=False),
        sa.Column("gross_exposure_max_percent", sa.Text(), nullable=False),
        sa.Column("minimum_cash_percent", sa.Text(), nullable=False),
        sa.Column("margin_usage_max_percent", sa.Text(), nullable=False),
        sa.Column("max_account_age_seconds", sa.Integer(), nullable=False),
        sa.Column("max_price_age_seconds", sa.Integer(), nullable=False),
        sa.Column("is_system_default", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("version", name="uq_risk_policies_version"),
        sa.UniqueConstraint("idempotency_key", name="uq_risk_policies_idempotency_key"),
        sa.CheckConstraint("version >= 1", name="ck_risk_policies_version"),
        sa.CheckConstraint(
            "CAST(single_position_max_percent AS REAL) >= 0"
            " AND CAST(single_position_max_percent AS REAL) <= 100",
            name="ck_risk_policies_single_position_max_percent",
        ),
        sa.CheckConstraint(
            "CAST(gross_exposure_max_percent AS REAL) > 0"
            " AND CAST(gross_exposure_max_percent AS REAL) <= 1000",
            name="ck_risk_policies_gross_exposure_max_percent",
        ),
        sa.CheckConstraint(
            "CAST(minimum_cash_percent AS REAL) >= 0"
            " AND CAST(minimum_cash_percent AS REAL) <= 100",
            name="ck_risk_policies_minimum_cash_percent",
        ),
        sa.CheckConstraint(
            "CAST(margin_usage_max_percent AS REAL) >= 0"
            " AND CAST(margin_usage_max_percent AS REAL) <= 1000",
            name="ck_risk_policies_margin_usage_max_percent",
        ),
        sa.CheckConstraint(
            "max_account_age_seconds >= 1", name="ck_risk_policies_account_age"
        ),
        sa.CheckConstraint(
            "max_price_age_seconds >= 1", name="ck_risk_policies_price_age"
        ),
        sa.CheckConstraint(
            "is_system_default IN (0, 1)", name="ck_risk_policies_system_default"
        ),
        sa.CheckConstraint(
            "confirmed_by IN ('system_default', 'user', 'external_agent')",
            name="ck_risk_policies_confirmed_by",
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_risk_policies_schema_version"),
    )
    op.create_index("ix_risk_policies_version", "risk_policies", ["version"])
    op.execute(
        sa.text(
            """
        INSERT INTO risk_policies(
            policy_id,
            version,
            single_position_max_percent,
            gross_exposure_max_percent,
            minimum_cash_percent,
            margin_usage_max_percent,
            max_account_age_seconds,
            max_price_age_seconds,
            is_system_default,
            confirmed_by,
            created_at,
            idempotency_key,
            schema_version
        )
        VALUES (
            :policy_id,
            1,
            '20',
            '120',
            '5',
            '25',
            3600,
            900,
            1,
            'system_default',
            :created_at,
            :idempotency_key,
            1
        )
        """
        ).bindparams(
            policy_id=SYSTEM_DEFAULT_POLICY_ID,
            created_at=SYSTEM_DEFAULT_CREATED_AT,
            idempotency_key=SYSTEM_DEFAULT_IDEMPOTENCY_KEY,
        )
    )
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES (
            '0012_phase2b_risk_engine',
            '2026-07-20T00:00:00+00:00',
            'Seeded system-default Phase 2B risk policy snapshot'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0012_phase2b_risk_engine'")
    op.drop_index("ix_risk_policies_version", table_name="risk_policies")
    op.drop_table("risk_policies")
