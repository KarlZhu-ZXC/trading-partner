"""Add durable cross-feature ReviewItems and human action receipts.

Revision ID: 0047_review_items
Revises: 0046_agent_channel_handoffs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_review_items"
down_revision: str | None = "0046_agent_channel_handoffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_items",
        sa.Column("review_item_id", sa.Text(), primary_key=True),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("href", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("active_at_source", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("due_at", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolution_ref", sa.Text(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('CATALYST_AGENDA','TRADE_RETRO','SCORECARD_GAP',"
            "'AGENT_PENDING_ACTION','BROKER_ORDER_INTENT')",
            name="ck_review_items_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED','AUTO_RESOLVED')",
            name="ck_review_items_status",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO','ATTENTION','ERROR')",
            name="ck_review_items_severity",
        ),
        sa.CheckConstraint("active_at_source IN (0,1)", name="ck_review_items_active"),
        sa.CheckConstraint("occurrence_count >= 1", name="ck_review_items_occurrence"),
        sa.CheckConstraint("version >= 1", name="ck_review_items_version"),
        sa.UniqueConstraint("source_key", name="uq_review_items_source_key"),
    )
    op.create_index("ix_review_items_status_last_seen", "review_items", ["status", "last_seen_at"])
    op.create_index("ix_review_items_subject_status", "review_items", ["subject_id", "status"])
    op.create_index(
        "ix_review_items_source_type_active",
        "review_items",
        ["source_type", "active_at_source"],
    )
    op.create_table(
        "review_item_actions",
        sa.Column("action_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "review_item_id",
            sa.Text(),
            sa.ForeignKey("review_items.review_item_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("authorization_note", sa.Text(), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolution_ref", sa.Text(), nullable=True),
        sa.Column("due_at", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACKNOWLEDGED','RESOLVED')",
            name="ck_review_item_actions_status",
        ),
        sa.CheckConstraint("expected_version >= 1", name="ck_review_item_actions_expected"),
        sa.CheckConstraint("result_version >= 2", name="ck_review_item_actions_result"),
        sa.UniqueConstraint("idempotency_key", name="uq_review_item_actions_idempotency"),
    )
    op.create_index(
        "ix_review_item_actions_item_created",
        "review_item_actions",
        ["review_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_item_actions_item_created", table_name="review_item_actions")
    op.drop_table("review_item_actions")
    op.drop_index("ix_review_items_source_type_active", table_name="review_items")
    op.drop_index("ix_review_items_subject_status", table_name="review_items")
    op.drop_index("ix_review_items_status_last_seen", table_name="review_items")
    op.drop_table("review_items")
