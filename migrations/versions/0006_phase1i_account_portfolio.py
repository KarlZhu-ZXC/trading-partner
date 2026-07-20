"""Phase 1I append-only account and portfolio snapshots.

Revision ID: 0006_phase1i_account_portfolio
Revises: 0005_phase1f_us_proxy_seeds
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_phase1i_account_portfolio"
down_revision: str | Sequence[str] | None = "0005_phase1f_us_proxy_seeds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_snapshots",
        sa.Column("snapshot_id", sa.Text(), primary_key=True),
        sa.Column("fingerprint", sa.Text(), nullable=False, unique=True),
        sa.Column("account_ref", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("base_currency", sa.Text(), nullable=False),
        sa.Column("account_as_of", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("cash", sa.Text()),
        sa.Column("buying_power", sa.Text()),
        sa.Column("net_assets", sa.Text()),
        sa.Column("margin_used", sa.Text()),
        sa.Column("open_orders_json", sa.Text(), nullable=False),
        sa.Column("degraded", sa.Integer(), nullable=False),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.CheckConstraint("degraded IN (0,1)", name="ck_account_snapshots_degraded"),
    )
    op.create_index(
        "ix_account_snapshots_account_as_of",
        "account_snapshots",
        ["account_ref", "account_as_of"],
    )
    op.create_table(
        "account_positions",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("sellable_quantity", sa.Text()),
        sa.Column("average_cost", sa.Text()),
        sa.Column("diluted_cost", sa.Text()),
        sa.Column("market_price", sa.Text()),
        sa.Column("market_price_at", sa.Text()),
        sa.Column("market_value", sa.Text()),
        sa.Column("unrealized_pnl", sa.Text()),
        sa.Column("realized_pnl", sa.Text()),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["account_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "instrument_id"),
    )
    op.create_table(
        "portfolio_snapshots",
        sa.Column("portfolio_snapshot_id", sa.Text(), primary_key=True),
        sa.Column("fingerprint", sa.Text(), nullable=False, unique=True),
        sa.Column("account_snapshot_ids_json", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Text(), nullable=False),
        sa.Column("base_currency", sa.Text(), nullable=False),
        sa.Column("total_value", sa.Text()),
        sa.Column("exposures_json", sa.Text(), nullable=False),
        sa.Column("missing_instrument_ids_json", sa.Text(), nullable=False),
        sa.Column("degraded", sa.Integer(), nullable=False),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.CheckConstraint("degraded IN (0,1)", name="ck_portfolio_snapshots_degraded"),
    )
    op.create_table(
        "case_position_links",
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("account_ref", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["investment_cases.case_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("case_id", "account_ref", "instrument_id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=revision,
            applied_at="2026-07-18T00:00:00+00:00",
            description="Phase 1I account and portfolio snapshots",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(version=revision)
    )
    op.drop_table("case_position_links")
    op.drop_table("portfolio_snapshots")
    op.drop_table("account_positions")
    op.drop_index("ix_account_snapshots_account_as_of", table_name="account_snapshots")
    op.drop_table("account_snapshots")
