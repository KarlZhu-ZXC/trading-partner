"""Add canonical account activities and attribution coverage receipts.

Revision ID: 0027_account_activity_coverage
Revises: 0026_korean_market_support
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0027_account_activity_coverage"
down_revision: str | Sequence[str] | None = "0026_korean_market_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("account_transactions") as batch:
        batch.alter_column("instrument_id", existing_type=sa.Text(), nullable=True)
        batch.alter_column("quantity", existing_type=sa.Text(), nullable=True)
        batch.alter_column("fees", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("cash_amount", sa.Text()))
        batch.add_column(sa.Column("source_type", sa.Text()))
        batch.add_column(sa.Column("mapping_version", sa.Text()))
    op.execute("UPDATE account_transactions SET source_type = 'legacy'")
    op.execute("UPDATE account_transactions SET mapping_version = 'account_activity_v1'")
    with op.batch_alter_table("account_transactions") as batch:
        batch.alter_column("source_type", existing_type=sa.Text(), nullable=False)
        batch.alter_column("mapping_version", existing_type=sa.Text(), nullable=False)

    op.create_table(
        "account_activity_coverage_receipts",
        sa.Column("receipt_id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("account_ref", sa.Text(), nullable=False),
        sa.Column("requested_start", sa.Text(), nullable=False),
        sa.Column("requested_end", sa.Text(), nullable=False),
        sa.Column("effective_start", sa.Text(), nullable=False),
        sa.Column("effective_end", sa.Text(), nullable=False),
        sa.Column("earliest_event_at", sa.Text()),
        sa.Column("latest_event_at", sa.Text()),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        sa.Column("earliest_snapshot_at", sa.Text()),
        sa.Column("latest_snapshot_at", sa.Text()),
        sa.Column("mapping_version", sa.Text(), nullable=False),
        sa.Column("supported_kinds_json", sa.Text(), nullable=False),
        sa.Column("unavailable_kinds_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("gap_codes_json", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('COMPLETE','INCOMPLETE')",
            name="ck_account_activity_coverage_status",
        ),
        sa.CheckConstraint(
            "event_count >= 0 AND inserted_count >= 0 AND duplicate_count >= 0 "
            "AND snapshot_count >= 0",
            name="ck_account_activity_coverage_counts",
        ),
        sa.CheckConstraint(
            "inserted_count + duplicate_count = event_count",
            name="ck_account_activity_coverage_reconcile",
        ),
    )
    op.create_index(
        "ix_account_activity_coverage_account_window",
        "account_activity_coverage_receipts",
        ["provider", "account_ref", "effective_start", "effective_end"],
    )
    op.execute(
        sa.text(
            "INSERT INTO schema_versions(version, applied_at, description) "
            "VALUES (:version, :applied_at, :description)"
        ).bindparams(
            version=revision,
            applied_at=datetime.now(UTC).isoformat(),
            description="canonical account activities and attribution coverage receipts",
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_activity_coverage_account_window",
        table_name="account_activity_coverage_receipts",
    )
    op.drop_table("account_activity_coverage_receipts")
    # Legacy schema cannot express instrument-less cash activities or unavailable
    # quantities/fees. Remove only those new rows before restoring its constraints.
    op.execute(
        "DELETE FROM account_transactions "
        "WHERE instrument_id IS NULL OR quantity IS NULL"
    )
    op.execute("UPDATE account_transactions SET fees = '0' WHERE fees IS NULL")
    with op.batch_alter_table("account_transactions") as batch:
        batch.drop_column("mapping_version")
        batch.drop_column("source_type")
        batch.drop_column("cash_amount")
        batch.alter_column("fees", existing_type=sa.Text(), nullable=False)
        batch.alter_column("quantity", existing_type=sa.Text(), nullable=False)
        batch.alter_column("instrument_id", existing_type=sa.Text(), nullable=False)
    op.execute(
        sa.text("DELETE FROM schema_versions WHERE version = :version").bindparams(
            version=revision
        )
    )
