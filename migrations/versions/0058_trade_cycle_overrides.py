"""Add append-only manual Trade Cycle split/merge/relink revisions.

Revision ID: 0058_trade_cycle_overrides
Revises: 0057_unlinked_activity_annotations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_trade_cycle_overrides"
down_revision: str | None = "0057_unlinked_activity_annotations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_cycle_override_revisions",
        sa.Column("override_id", sa.Text(), nullable=False),
        sa.Column("root_cycle_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("cycle_ids_json", sa.Text(), nullable=False),
        sa.Column("activity_ids_json", sa.Text(), nullable=False),
        sa.Column("split_groups_json", sa.Text(), nullable=False),
        sa.Column("target_cycle_id", sa.Text(), nullable=True),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("authorization_note", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("override_id", name="pk_trade_cycle_override_revisions"),
        sa.UniqueConstraint(
            "root_cycle_id",
            "version",
            name="uq_trade_cycle_override_root_version",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_trade_cycle_override_idempotency"
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_trade_cycle_override_revisions_version",
        ),
        sa.CheckConstraint(
            "operation IN ('SPLIT','MERGE','RELINK')",
            name="ck_trade_cycle_override_revisions_operation",
        ),
        sa.CheckConstraint(
            "actor IN ('user','external_agent')",
            name="ck_trade_cycle_override_revisions_actor",
        ),
        sa.CheckConstraint(
            "expected_version IS NULL OR expected_version >= 0",
            name="ck_trade_cycle_override_revisions_expected_version",
        ),
    )
    op.create_index(
        "ix_trade_cycle_override_root_created",
        "trade_cycle_override_revisions",
        ["root_cycle_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trade_cycle_override_root_created",
        table_name="trade_cycle_override_revisions",
    )
    op.drop_table("trade_cycle_override_revisions")
