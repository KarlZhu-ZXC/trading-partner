"""SQLAlchemy rows for the shared Console/Telegram Agent Runtime."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm.common import HEX64_CHECK, JsonStringTuple


class AgentConversationRow(Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','ARCHIVED')",
            name="ck_agent_conversations_status",
        ),
        CheckConstraint(
            "summary_through_sequence >= 0",
            name="ck_agent_conversations_summary_sequence",
        ),
        CheckConstraint(
            "next_message_sequence >= 1",
            name="ck_agent_conversations_next_sequence",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_agent_conversations_version",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_agent_conversations_updated_at",
        ),
        Index("ix_agent_conversations_owner_status", "owner_principal", "status"),
        Index("ix_agent_conversations_updated_at", "updated_at"),
    )

    conversation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_principal: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ACTIVE")
    rolling_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary_through_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_message_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class AgentChannelBindingRow(Base):
    __tablename__ = "agent_channel_bindings"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_channel_bindings_channel",
        ),
        Index(
            "uq_agent_channel_bindings_active_external",
            "channel",
            "external_conversation_ref",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = TRUE"),
        ),
        Index(
            "uq_agent_channel_bindings_active_conversation_channel",
            "conversation_id",
            "channel",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = TRUE"),
        ),
        Index(
            "ix_agent_channel_bindings_external",
            "channel",
            "external_conversation_ref",
        ),
    )

    binding_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    external_conversation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class AgentMessageRow(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('USER','ASSISTANT')",
            name="ck_agent_messages_role",
        ),
        CheckConstraint(
            "channel IS NULL OR channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_messages_channel",
        ),
        CheckConstraint(
            "external_message_ref IS NULL OR channel IS NOT NULL",
            name="ck_agent_messages_external_ref_channel",
        ),
        CheckConstraint("sequence >= 1", name="ck_agent_messages_sequence"),
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_agent_messages_conversation_sequence",
        ),
        Index(
            "uq_agent_messages_channel_external_ref",
            "channel",
            "external_message_ref",
            unique=True,
            sqlite_where=text("external_message_ref IS NOT NULL"),
            postgresql_where=text("external_message_ref IS NOT NULL"),
        ),
        Index("ix_agent_messages_conversation_sequence", "conversation_id", "sequence"),
    )

    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str | None] = mapped_column(Text)
    external_message_ref: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    model_receipt_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class AgentToolReceiptRow(Base):
    __tablename__ = "agent_tool_receipts"
    __table_args__ = (
        UniqueConstraint("receipt_id", name="uq_agent_tool_receipts_receipt_id"),
        Index("ix_agent_tool_receipts_conversation_created", "conversation_id", "created_at"),
    )

    receipt_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    message_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("agent_messages.message_id", ondelete="RESTRICT"),
    )
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_codes: Mapped[tuple[str, ...]] = mapped_column(
        "source_codes_json", JsonStringTuple(), nullable=False
    )
    warning_codes: Mapped[tuple[str, ...]] = mapped_column(
        "warning_codes_json", JsonStringTuple(), nullable=False
    )
    error_codes: Mapped[tuple[str, ...]] = mapped_column(
        "error_codes_json", JsonStringTuple(), nullable=False
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class AgentPendingActionRow(Base):
    __tablename__ = "agent_pending_actions"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_pending_actions_channel",
        ),
        CheckConstraint(
            "status IN ('PROPOSED','PRESENTED','CONFIRMED','EXECUTING','SUCCEEDED',"
            "'REJECTED','EXPIRED','FAILED','UNKNOWN')",
            name="ck_agent_pending_actions_status",
        ),
        CheckConstraint("version >= 1", name="ck_agent_pending_actions_version"),
        CheckConstraint("expires_at > created_at", name="ck_agent_pending_actions_expiry"),
        CheckConstraint("updated_at >= created_at", name="ck_agent_pending_actions_updated_at"),
        CheckConstraint(
            "token_sha256 IS NULL OR "
            "(length(token_sha256) = 64 AND token_sha256 = lower(token_sha256) "
            "AND token_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_agent_pending_actions_token_sha256",
        ),
        Index(
            "uq_agent_pending_actions_token_sha256",
            "token_sha256",
            unique=True,
            sqlite_where=text("token_sha256 IS NOT NULL"),
            postgresql_where=text("token_sha256 IS NOT NULL"),
        ),
        Index("ix_agent_pending_actions_conversation_status", "conversation_id", "status"),
        Index("ix_agent_pending_actions_expiry", "expires_at"),
    )

    action_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    principal: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    presented_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    # Added by migration 0045.  Nullable preserves readability of 0044 rows;
    # new Agent-D proposals always populate capability/operation and token hash.
    capability: Mapped[str | None] = mapped_column(Text)
    operation: Mapped[str | None] = mapped_column(Text)
    token_sha256: Mapped[str | None] = mapped_column(Text)
    result_receipt_json: Mapped[str | None] = mapped_column(Text)


class AgentChannelCursorRow(Base):
    __tablename__ = "agent_channel_cursors"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_channel_cursors_channel",
        ),
        CheckConstraint(
            "last_update_id >= -1",
            name="ck_agent_channel_cursors_update_id",
        ),
        CheckConstraint("version >= 1", name="ck_agent_channel_cursors_version"),
        UniqueConstraint(
            "channel",
            "cursor_key",
            name="uq_agent_channel_cursors_channel_key",
        ),
        Index("ix_agent_channel_cursors_updated_at", "updated_at"),
    )

    cursor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    cursor_key: Mapped[str] = mapped_column(Text, nullable=False)
    last_update_id: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class AgentChannelHandoffRow(Base):
    __tablename__ = "agent_channel_handoffs"
    __table_args__ = (
        CheckConstraint(
            "target_channel IN ('CONSOLE','TELEGRAM')",
            name="ck_agent_channel_handoffs_target_channel",
        ),
        CheckConstraint("version >= 1", name="ck_agent_channel_handoffs_version"),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_agent_channel_handoffs_expiry",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="ck_agent_channel_handoffs_consumed_at",
        ),
        CheckConstraint(
            HEX64_CHECK.format(col="token_sha256"),
            name="ck_agent_channel_handoffs_token_sha256",
        ),
        UniqueConstraint("token_sha256", name="uq_agent_channel_handoffs_token_sha256"),
        Index("ix_agent_channel_handoffs_conversation", "conversation_id"),
        Index("ix_agent_channel_handoffs_expiry", "expires_at"),
    )

    handoff_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("agent_conversations.conversation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_principal: Mapped[str] = mapped_column(Text, nullable=False)
    target_channel: Mapped[str] = mapped_column(Text, nullable=False)
    token_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    consumed_at: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
