"""Persist Shared Agent Runtime turn lifecycle state.

Revision ID: 0049_agent_turns
Revises: 0048_review_item_occurrences
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_agent_turns"
down_revision: str | None = "0048_review_item_occurrences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the additive turn table; existing conversations/messages stay intact."""

    op.create_table(
        "agent_turns",
        sa.Column("turn_id", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            sa.Text(),
            sa.ForeignKey("agent_messages.message_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assistant_message_id",
            sa.Text(),
            sa.ForeignKey("agent_messages.message_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("reasoning_effort", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_turns_channel",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING','WAITING_TOOL','COMPLETED','FAILED','CANCELLED')",
            name="ck_agent_turns_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_turns_version"),
        sa.CheckConstraint("updated_at >= started_at", name="ck_agent_turns_updated_at"),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_agent_turns_completed_at",
        ),
    )
    op.create_index(
        "ix_agent_turns_conversation_started",
        "agent_turns",
        ["conversation_id", "started_at"],
    )
    op.create_index(
        "ix_agent_turns_conversation_status",
        "agent_turns",
        ["conversation_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_turns_conversation_status", table_name="agent_turns")
    op.drop_index("ix_agent_turns_conversation_started", table_name="agent_turns")
    op.drop_table("agent_turns")
