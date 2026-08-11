"""Conversation creation, bounded context assembly, and summary maintenance."""

from __future__ import annotations

from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.agent_model_provider import ModelMessage, ModelRequest
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.agent.enums import AgentConversationStatus, AgentMessageRole
from domain.agent.models import AgentConversation
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
    ) -> None:
        if recent_message_limit < 1 or summary_threshold < recent_message_limit:
            raise ValueError("invalid Agent context bounds")
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator
        self._recent_message_limit = recent_message_limit
        self._summary_threshold = summary_threshold

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
        for item in recent:
            if item.role is AgentMessageRole.USER:
                messages.append(ModelMessage(role="user", content=item.content))
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
        transcript = "\n".join(f"{item.role.value}: {item.content}" for item in messages)
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
