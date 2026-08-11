"""Persist the shared Console/Telegram Agent Runtime core.

Revision ID: 0044_shared_agent_runtime
Revises: 0043_broker_order_execution
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_shared_agent_runtime"
down_revision: str | None = "0043_broker_order_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("conversation_id", sa.Text(), primary_key=True),
        sa.Column("owner_principal", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("rolling_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary_through_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_message_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE','ARCHIVED')",
            name="ck_agent_conversations_status",
        ),
        sa.CheckConstraint(
            "summary_through_sequence >= 0",
            name="ck_agent_conversations_summary_sequence",
        ),
        sa.CheckConstraint(
            "next_message_sequence >= 1",
            name="ck_agent_conversations_next_sequence",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_conversations_version"),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_agent_conversations_updated_at",
        ),
    )
    op.create_index(
        "ix_agent_conversations_owner_status",
        "agent_conversations",
        ["owner_principal", "status"],
    )
    op.create_index(
        "ix_agent_conversations_updated_at",
        "agent_conversations",
        ["updated_at"],
    )

    op.create_table(
        "agent_channel_bindings",
        sa.Column("binding_id", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("external_conversation_ref", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_channel_bindings_channel",
        ),
    )
    op.create_index(
        "uq_agent_channel_bindings_active_external",
        "agent_channel_bindings",
        ["channel", "external_conversation_ref"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index(
        "uq_agent_channel_bindings_active_conversation_channel",
        "agent_channel_bindings",
        ["conversation_id", "channel"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index(
        "ix_agent_channel_bindings_external",
        "agent_channel_bindings",
        ["channel", "external_conversation_ref"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text()),
        sa.Column("external_message_ref", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("request_id", sa.Text()),
        sa.Column("model_receipt_json", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("role IN ('USER','ASSISTANT')", name="ck_agent_messages_role"),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_messages_channel",
        ),
        sa.CheckConstraint(
            "external_message_ref IS NULL OR channel IS NOT NULL",
            name="ck_agent_messages_external_ref_channel",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_messages_sequence"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_agent_messages_conversation_sequence",
        ),
    )
    op.create_index(
        "ix_agent_messages_conversation_sequence",
        "agent_messages",
        ["conversation_id", "sequence"],
    )
    op.create_index(
        "uq_agent_messages_channel_external_ref",
        "agent_messages",
        ["channel", "external_message_ref"],
        unique=True,
        sqlite_where=sa.text("external_message_ref IS NOT NULL"),
        postgresql_where=sa.text("external_message_ref IS NOT NULL"),
    )

    op.create_table(
        "agent_tool_receipts",
        sa.Column("receipt_id", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Text(),
            sa.ForeignKey("agent_messages.message_id", ondelete="RESTRICT"),
        ),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("arguments_sha256", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("source_codes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("warning_codes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_codes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_agent_tool_receipts_conversation_created",
        "agent_tool_receipts",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "agent_pending_actions",
        sa.Column("action_id", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("principal", sa.Text(), nullable=False),
        sa.Column("normalized_arguments_json", sa.Text(), nullable=False),
        sa.Column("arguments_sha256", sa.Text(), nullable=False),
        sa.Column("presented_summary", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_pending_actions_channel",
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED','PRESENTED','CONFIRMED','EXECUTING','SUCCEEDED',"
            "'REJECTED','EXPIRED','FAILED','UNKNOWN')",
            name="ck_agent_pending_actions_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_pending_actions_version"),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_agent_pending_actions_expiry",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_agent_pending_actions_updated_at",
        ),
    )
    op.create_index(
        "ix_agent_pending_actions_conversation_status",
        "agent_pending_actions",
        ["conversation_id", "status"],
    )
    op.create_index(
        "ix_agent_pending_actions_expiry",
        "agent_pending_actions",
        ["expires_at"],
    )

    op.create_table(
        "agent_channel_cursors",
        sa.Column("cursor_id", sa.Text(), primary_key=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("cursor_key", sa.Text(), nullable=False),
        sa.Column("last_update_id", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_channel_cursors_channel",
        ),
        sa.CheckConstraint(
            "last_update_id >= -1",
            name="ck_agent_channel_cursors_update_id",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_channel_cursors_version"),
        sa.UniqueConstraint(
            "channel",
            "cursor_key",
            name="uq_agent_channel_cursors_channel_key",
        ),
    )
    op.create_index(
        "ix_agent_channel_cursors_updated_at",
        "agent_channel_cursors",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_channel_cursors_updated_at", table_name="agent_channel_cursors")
    op.drop_table("agent_channel_cursors")
    op.drop_index(
        "ix_agent_pending_actions_expiry",
        table_name="agent_pending_actions",
    )
    op.drop_index(
        "ix_agent_pending_actions_conversation_status",
        table_name="agent_pending_actions",
    )
    op.drop_table("agent_pending_actions")
    op.drop_index(
        "ix_agent_tool_receipts_conversation_created",
        table_name="agent_tool_receipts",
    )
    op.drop_table("agent_tool_receipts")
    op.drop_index(
        "uq_agent_messages_channel_external_ref",
        table_name="agent_messages",
    )
    op.drop_index(
        "ix_agent_messages_conversation_sequence",
        table_name="agent_messages",
    )
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_channel_bindings_external", table_name="agent_channel_bindings")
    op.drop_index(
        "uq_agent_channel_bindings_active_conversation_channel",
        table_name="agent_channel_bindings",
    )
    op.drop_index(
        "uq_agent_channel_bindings_active_external",
        table_name="agent_channel_bindings",
    )
    op.drop_table("agent_channel_bindings")
    op.drop_index("ix_agent_conversations_updated_at", table_name="agent_conversations")
    op.drop_index("ix_agent_conversations_owner_status", table_name="agent_conversations")
    op.drop_table("agent_conversations")
