"""Durable US post-market synchronization receipts.

Revision ID: 0010_post_market_sync_runs
Revises: 0009_phase2_watchlist_hub
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_post_market_sync_runs"
down_revision: str | Sequence[str] | None = "0009_phase2_watchlist_hub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_market_sync_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("market_session_date", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("portfolio_status", sa.Text(), nullable=False),
        sa.Column("watchlist_status", sa.Text(), nullable=False),
        sa.Column("account_snapshot_ids", sa.Text(), nullable=False),
        sa.Column("watchlist_groups_synced", sa.Integer(), nullable=True),
        sa.Column("watchlist_membership_relations_synced", sa.Integer(), nullable=True),
        sa.Column("warning_codes", sa.Text(), nullable=False),
        sa.Column("error_codes", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "market_session_date", name="uq_post_market_sync_session_date"
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED')",
            name="ck_post_market_sync_status",
        ),
        sa.CheckConstraint(
            "portfolio_status IN ('SUCCEEDED','FAILED')",
            name="ck_post_market_sync_portfolio_status",
        ),
        sa.CheckConstraint(
            "watchlist_status IN ('SUCCEEDED','FAILED')",
            name="ck_post_market_sync_watchlist_status",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at", name="ck_post_market_sync_time_order"
        ),
        sa.CheckConstraint(
            "attempt_count >= 1", name="ck_post_market_sync_attempt_count"
        ),
        sa.CheckConstraint(
            "watchlist_groups_synced IS NULL OR watchlist_groups_synced >= 0",
            name="ck_post_market_sync_group_count",
        ),
        sa.CheckConstraint(
            "watchlist_membership_relations_synced IS NULL"
            " OR watchlist_membership_relations_synced >= 0",
            name="ck_post_market_sync_membership_count",
        ),
    )
    op.create_index(
        "ix_post_market_sync_completed_at",
        "post_market_sync_runs",
        ["completed_at"],
    )
    op.execute(
        """
        INSERT INTO schema_versions(version, applied_at, description)
        VALUES ('0010_post_market_sync_runs', '2026-07-19T00:00:00+00:00',
        'Durable receipts for the US post-market account and watchlist sync')
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_versions WHERE version = '0010_post_market_sync_runs'")
    op.drop_index(
        "ix_post_market_sync_completed_at", table_name="post_market_sync_runs"
    )
    op.drop_table("post_market_sync_runs")
