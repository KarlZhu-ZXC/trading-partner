"""Conversation creation, bounded context assembly, and summary maintenance."""

from __future__ import annotations

import base64

from application.ports.agent_attachment_store import AgentAttachmentStore
from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.agent_model_provider import ModelMessage, ModelRequest
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.agent.attachments import AGENT_IMAGE_MAX_TOTAL_BYTES
from domain.agent.enums import AgentConversationStatus, AgentMessageRole
from domain.agent.models import AgentConversation, AgentMessage
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix


class AgentContextService:
    """Assemble context without treating conversation memory as live facts."""

    def __init__(
        self,
        *,
        repository: AgentConversationRepository,
        clock: Clock,
        id_generator: IdGenerator,
        recent_message_limit: int = 12,
        summary_threshold: int = 24,
        attachment_store: AgentAttachmentStore | None = None,
    ) -> None:
        if recent_message_limit < 1 or summary_threshold < recent_message_limit:
            raise ValueError("invalid Agent context bounds")
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator
        self._recent_message_limit = recent_message_limit
        self._summary_threshold = summary_threshold
        self._attachment_store = attachment_store

    def _message_content(
        self,
        item: AgentMessage,
        included_attachment_ids: frozenset[str],
    ) -> str | tuple[dict[str, object], ...]:
        if not item.attachments:
            return item.content
        parts: list[dict[str, object]] = []
        if item.content:
            parts.append({"type": "text", "text": item.content})
        for attachment in item.attachments:
            if attachment.attachment_id not in included_attachment_ids:
                parts.append(
                    {
                        "type": "text",
                        "text": "[image attachment omitted from this context window]",
                    }
                )
                continue
            if self._attachment_store is None:
                raise DataContractError("Agent image attachment storage is unavailable")
            encoded = base64.b64encode(self._attachment_store.read(attachment)).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{attachment.media_type};base64,{encoded}",
                    },
                }
            )
        return tuple(parts)

    def create_conversation(
        self,
        *,
        owner_principal: str,
        title: str,
    ) -> AgentConversation:
        now = self._clock.now()
        return self._repository.create_conversation(
            AgentConversation(
                conversation_id=self._id_generator.new(EntityIdPrefix.AGENT_CONVERSATION),
                owner_principal=owner_principal,
                title=title,
                status=AgentConversationStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
        )

    def require_owned_active(
        self,
        conversation_id: str,
        owner_principal: str,
    ) -> AgentConversation:
        conversation = self._repository.get_conversation(conversation_id)
        if conversation is None:
            raise DataContractError("Agent conversation was not found")
        if conversation.owner_principal != owner_principal:
            raise DataContractError("Agent conversation principal does not match")
        if conversation.status is not AgentConversationStatus.ACTIVE:
            raise DataContractError("Agent conversation is archived")
        return conversation

    def model_messages(
        self,
        *,
        conversation: AgentConversation,
        system_prompt: str,
    ) -> list[ModelMessage]:
        stored = self._repository.list_messages(
            conversation.conversation_id,
            limit=self._recent_message_limit,
            newest_first=True,
        )
        recent = stored
        messages = [ModelMessage(role="system", content=system_prompt)]
        if conversation.rolling_summary:
            messages.append(
                ModelMessage(
                    role="system",
                    content=(
                        "以下是仅用于对话连续性的历史摘要；其中的价格、持仓、成交和研究"
                        "状态都不是当前事实，必要时必须重新调用工具核验：\n"
                        f"{conversation.rolling_summary}"
                    ),
                )
            )
        included_attachment_ids: set[str] = set()
        remaining_image_bytes = AGENT_IMAGE_MAX_TOTAL_BYTES
        for item in reversed(recent):
            for attachment in reversed(item.attachments):
                if attachment.byte_size <= remaining_image_bytes:
                    included_attachment_ids.add(attachment.attachment_id)
                    remaining_image_bytes -= attachment.byte_size
        included = frozenset(included_attachment_ids)
        for item in recent:
            if item.role is AgentMessageRole.USER:
                messages.append(
                    ModelMessage(
                        role="user",
                        content=self._message_content(item, included),
                    )
                )
            else:
                messages.append(ModelMessage(role="assistant", content=item.content))
        return messages

    def summary_request(self, conversation: AgentConversation) -> tuple[ModelRequest, int] | None:
        messages = self._repository.list_messages(
            conversation.conversation_id,
            after_sequence=conversation.summary_through_sequence,
            limit=self._summary_threshold + 1,
        )
        if len(messages) <= self._summary_threshold:
            return None
        through_sequence = messages[-1].sequence
        transcript = "\n".join(
            f"{item.role.value}: {item.content}"
            + (f" [image attachments: {len(item.attachments)}]" if item.attachments else "")
            for item in messages
        )
        prompt = (
            "请把以下对话压缩成简体中文摘要，只保留用户目标、明确偏好、未解决问题和"
            "引用 ID。不得把价格、持仓、成交、研究状态或模型推断写成当前事实。"
        )
        summary_messages = [ModelMessage(role="system", content=prompt)]
        if conversation.rolling_summary:
            summary_messages.append(
                ModelMessage(
                    role="user",
                    content=(
                        "已有滚动摘要如下。请把它与后续新增对话合并，不要丢失仍然有效的"
                        "目标、偏好、未解决问题或引用 ID：\n"
                        f"{conversation.rolling_summary}"
                    ),
                )
            )
        summary_messages.append(ModelMessage(role="user", content=transcript))
        return (
            ModelRequest(
                messages=tuple(summary_messages),
                tools=(),
                session_id=conversation.conversation_id,
            ),
            through_sequence,
        )

    def store_summary(
        self,
        *,
        conversation: AgentConversation,
        summary: str,
        through_sequence: int,
    ) -> AgentConversation:
        return self._repository.update_summary(
            conversation.conversation_id,
            summary[:32_000],
            through_sequence,
            conversation.summary_through_sequence,
            expected_version=conversation.version,
            now=self._clock.now(),
        )


__all__ = ["AgentContextService"]
