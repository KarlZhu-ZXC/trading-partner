"""Add bounded secret-safe Provider route receipts.

Revision ID: 0028_provider_route_history
Revises: 0027_account_activity_coverage
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0028_provider_route_history"
down_revision: str | Sequence[str] | None = "0027_account_activity_coverage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_route_receipts",
        sa.Column("route_id", sa.Text(), primary_key=True),
        sa.Column("recorded_at", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("operation_name", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text()),
        sa.Column("criticality", sa.Text(), nullable=False),
        sa.Column("requested_chain_json", sa.Text(), nullable=False),
        sa.Column("ok", sa.Integer(), nullable=False),
        sa.Column("selected_vendor", sa.Text()),
        sa.Column("selected_role", sa.Text()),
        sa.Column("cache_disposition", sa.Text()),
        sa.Column("attempts_json", sa.Text(), nullable=False),
        sa.Column("warning_codes_json", sa.Text(), nullable=False),
        sa.Column("final_error_code", sa.Text()),
        sa.CheckConstraint("ok IN (0, 1)", name="ck_provider_route_receipts_ok"),
        sa.CheckConstraint(
            "(ok = 1 AND selected_vendor IS NOT NULL AND selected_role IS NOT NULL "
            "AND final_error_code IS NULL) OR "
            "(ok = 0 AND selected_vendor IS NULL AND selected_role IS NULL "
            "AND final_error_code IS NOT NULL)",
            name="ck_provider_route_receipts_outcome",
        ),
    )
    op.create_index(
        "ix_provider_route_receipts_recorded_at",
        "provider_route_receipts",
        ["recorded_at"],
    )
    op.create_index(
        "ix_provider_route_receipts_scope",
        "provider_route_receipts",
        ["market", "category", "recorded_at"],
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=revision,
            applied_at=datetime.now(UTC).isoformat(),
            description="bounded secret-safe Provider route history",
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_route_receipts_scope",
        table_name="provider_route_receipts",
    )
    op.drop_index(
        "ix_provider_route_receipts_recorded_at",
        table_name="provider_route_receipts",
    )
    op.drop_table("provider_route_receipts")
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=revision
        )
    )
