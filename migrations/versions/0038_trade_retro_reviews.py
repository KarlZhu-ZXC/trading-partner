"""Add append-only Trade Retro review revisions.

Revision ID: 0038_trade_retro_reviews
Revises: 0037_trade_retro
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_trade_retro_reviews"
down_revision: str | None = "0037_trade_retro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_retro_review_revisions",
        sa.Column("review_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("note_markdown", sa.Text(), nullable=False),
        sa.Column("action_items_json", sa.Text(), nullable=False),
        sa.Column("finding_reviews_json", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=False),
        sa.Column("authorization_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("execution_effect", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("version >= 1", name="trade_retro_review_positive_version"),
        sa.CheckConstraint(
            "status IN ('OPEN','ACCEPTED','DISPUTED','RESOLVED')",
            name="trade_retro_review_status",
        ),
        sa.CheckConstraint(
            "reviewed_by IN ('user','external_agent')",
            name="trade_retro_review_confirmer",
        ),
        sa.CheckConstraint("schema_version = 1", name="trade_retro_review_schema"),
        sa.CheckConstraint("execution_effect = 0", name="trade_retro_review_no_execution"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["trade_retro_runs.run_id"],
            name="fk_trade_retro_review_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("review_id"),
        sa.UniqueConstraint(
            "run_id",
            "version",
            name="uq_trade_retro_review_run_version",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_trade_retro_review_key",
        ),
    )
    op.create_index(
        "ix_trade_retro_reviews_run",
        "trade_retro_review_revisions",
        ["run_id", "version"],
    )
    with op.batch_alter_table("trade_retro_export_receipts") as batch:
        batch.add_column(sa.Column("review_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trade_retro_export_receipts") as batch:
        batch.drop_column("review_version")
    op.drop_index(
        "ix_trade_retro_reviews_run",
        table_name="trade_retro_review_revisions",
    )
    op.drop_table("trade_retro_review_revisions")
