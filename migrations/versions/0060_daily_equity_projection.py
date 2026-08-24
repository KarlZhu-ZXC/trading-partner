"""Persist Journal activation and source-referenced Daily Equity projections.

Revision ID: 0060_daily_equity_projection
Revises: 0059_behavior_reviews
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_daily_equity_projection"
down_revision: str | None = "0059_behavior_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_activations",
        sa.Column("activation_id", sa.Text(), nullable=False),
        sa.Column("journal_activation_at", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("activation_id", name="pk_journal_activations"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_journal_activations_idempotency_key",
        ),
        sa.CheckConstraint(
            "activation_id = 'journal_activation'",
            name="ck_journal_activations_singleton",
        ),
    )
    op.create_index(
        "ix_journal_activations_at",
        "journal_activations",
        ["journal_activation_at"],
    )

    op.create_table(
        "daily_equity_snapshots",
        sa.Column("daily_equity_snapshot_id", sa.Text(), nullable=False),
        sa.Column("account_ref", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("valuation_at", sa.Text(), nullable=False),
        sa.Column("market_session_date", sa.Text(), nullable=False),
        sa.Column("equity_value", sa.Text(), nullable=True),
        sa.Column("cash_value", sa.Text(), nullable=True),
        sa.Column("gross_position_value", sa.Text(), nullable=True),
        sa.Column("net_external_cash_flow_since_previous", sa.Text(), nullable=True),
        sa.Column("valuation_basis", sa.Text(), nullable=False),
        sa.Column("source_snapshot_id", sa.Text(), nullable=False),
        sa.Column("source_snapshot_as_of", sa.Text(), nullable=False),
        sa.Column("source_fetched_at", sa.Text(), nullable=False),
        sa.Column("journal_activation_at", sa.Text(), nullable=True),
        sa.Column("coverage_status", sa.Text(), nullable=False),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("materialized_at", sa.Text(), nullable=False),
        sa.Column("warning_codes", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "daily_equity_snapshot_id",
            name="pk_daily_equity_snapshots",
        ),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "algorithm_version",
            name="uq_daily_equity_source_algorithm",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["account_snapshots.snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "valuation_basis = 'BROKER_NET_ASSETS'",
            name="ck_daily_equity_valuation_basis",
        ),
        sa.CheckConstraint(
            "coverage_status IN ('COMPLETE','PARTIAL','INCOMPLETE','UNAVAILABLE')",
            name="ck_daily_equity_coverage_status",
        ),
        sa.CheckConstraint(
            "quality_status IN ('COMPLETE','PARTIAL','INCOMPLETE','UNAVAILABLE')",
            name="ck_daily_equity_quality_status",
        ),
    )
    op.create_index(
        "ix_daily_equity_account_currency_valuation",
        "daily_equity_snapshots",
        ["account_ref", "currency", "valuation_at"],
    )
    op.create_index(
        "ix_daily_equity_source_snapshot",
        "daily_equity_snapshots",
        ["source_snapshot_id", "algorithm_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_daily_equity_source_snapshot",
        table_name="daily_equity_snapshots",
    )
    op.drop_index(
        "ix_daily_equity_account_currency_valuation",
        table_name="daily_equity_snapshots",
    )
    op.drop_table("daily_equity_snapshots")
    op.drop_index("ix_journal_activations_at", table_name="journal_activations")
    op.drop_table("journal_activations")
