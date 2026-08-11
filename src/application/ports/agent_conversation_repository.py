"""Durable conversation, message, receipt, and channel cursor port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.agent.enums import AgentChannel
from domain.agent.models import (
    AgentChannelBinding,
    AgentChannelCursor,
    AgentConversation,
    AgentMessage,
    AgentToolReceipt,
)


class AgentConversationRepository(Protocol):
    def create_conversation(self, value: AgentConversation) -> AgentConversation: ...

    def get_conversation(self, conversation_id: str) -> AgentConversation | None: ...

    def list_conversations(
        self,
        owner_principal: str | None = None,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[AgentConversation, ...]: ...

    def archive_conversation(
        self,
        conversation_id: str,
        *,
        owner_principal: str | None = None,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> AgentConversation: ...

    def bind_channel(self, value: AgentChannelBinding) -> AgentChannelBinding: ...

    def get_binding(
        self,
        channel: AgentChannel,
        external_conversation_ref: str,
        *,
        active_only: bool = True,
    ) -> AgentChannelBinding | None: ...

    def deactivate_channel(
        self,
        channel: AgentChannel,
        external_conversation_ref: str,
        *,
        now: datetime | None = None,
    ) -> AgentChannelBinding | None: ...

    def append_message(self, value: AgentMessage) -> AgentMessage: ...

    def list_messages(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        newest_first: bool = False,
    ) -> tuple[AgentMessage, ...]: ...

    def get_message_by_external_ref(
        self,
        channel: AgentChannel,
        external_message_ref: str,
    ) -> AgentMessage | None: ...

    def append_tool_receipt(self, value: AgentToolReceipt) -> AgentToolReceipt: ...

    def get_tool_receipt(self, receipt_id: str) -> AgentToolReceipt | None: ...

    def list_tool_receipts(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
    ) -> tuple[AgentToolReceipt, ...]: ...

    def update_summary(
        self,
        conversation_id: str,
        rolling_summary: str,
        summary_through_sequence: int,
        expected_summary_through_sequence: int = 0,
        *,
        expected_version: int | None = None,
        now: datetime | None = None,
    ) -> AgentConversation: ...

    def get_cursor(
        self,
        channel: AgentChannel,
        cursor_key: str = "default",
    ) -> AgentChannelCursor | None: ...

    def advance_cursor(
        self,
        channel: AgentChannel,
        cursor_key: str = "default",
        update_id: int | None = None,
        expected_update_id: int | None = None,
        *,
        next_update_id: int | None = None,
        now: datetime | None = None,
    ) -> AgentChannelCursor: ...
