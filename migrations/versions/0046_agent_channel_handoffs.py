"""Persist one-time Console/Telegram Agent channel handoff tokens.

Revision ID: 0046_agent_channel_handoffs
Revises: 0045_agent_pending_action_controls
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_agent_channel_handoffs"
down_revision: str | None = "0045_agent_pending_action_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_channel_handoffs",
        sa.Column("handoff_id", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_principal", sa.Text(), nullable=False),
        sa.Column("target_channel", sa.Text(), nullable=False),
        sa.Column("token_sha256", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("consumed_at", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "target_channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_channel_handoffs_target_channel",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_channel_handoffs_version"),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_agent_channel_handoffs_expiry",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="ck_agent_channel_handoffs_consumed_at",
        ),
        sa.CheckConstraint(
            "length(token_sha256) = 64 AND token_sha256 = lower(token_sha256) "
            "AND token_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_channel_handoffs_token_sha256",
        ),
        sa.UniqueConstraint("token_sha256", name="uq_agent_channel_handoffs_token_sha256"),
    )
    op.create_index(
        "ix_agent_channel_handoffs_conversation",
        "agent_channel_handoffs",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_channel_handoffs_expiry",
        "agent_channel_handoffs",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_channel_handoffs_expiry", table_name="agent_channel_handoffs")
    op.drop_index(
        "ix_agent_channel_handoffs_conversation",
        table_name="agent_channel_handoffs",
    )
    op.drop_table("agent_channel_handoffs")
