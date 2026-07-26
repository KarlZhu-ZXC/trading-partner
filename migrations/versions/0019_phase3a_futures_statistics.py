"""Persist explicit Phase 3A futures EOD statistics vintages.

Revision ID: 0019_phase3a_futures_statistics
Revises: 0018_phase3a_otc_spot_seeds
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0019_phase3a_futures_statistics"
down_revision: str | Sequence[str] | None = "0018_phase3a_otc_spot_seeds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "futures_contract_statistics",
        sa.Column(
            "instrument_id",
            sa.Text(),
            sa.ForeignKey("futures_contracts.instrument_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("trade_date", sa.Text(), primary_key=True),
        sa.Column("published_at", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("settlement", sa.Text(), nullable=True),
        sa.Column("settlement_status", sa.Text(), nullable=False),
        sa.Column("session_volume", sa.Text(), nullable=True),
        sa.Column("open_interest", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "settlement_status IN ('preliminary','final','unknown')",
            name="ck_futures_contract_statistics_status",
        ),
    )
    op.create_index(
        "ix_futures_contract_statistics_trade_date",
        "futures_contract_statistics",
        ["trade_date", "instrument_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=revision,
            applied_at=datetime.now(UTC).isoformat(),
            description="Phase 3A explicit futures EOD statistics vintages",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=revision
        )
    )
    op.drop_index(
        "ix_futures_contract_statistics_trade_date",
        table_name="futures_contract_statistics",
    )
    op.drop_table("futures_contract_statistics")
