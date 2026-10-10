"""Shared deterministic support for Agent behavior evaluations.

The in-memory ports in this module deliberately depend only on the application
ports and domain records.  Keeping them independent from interface adapters
allows the synthetic research evaluator to be imported without loading the
Console application or its optional web dependencies.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from application.ports.agent_model_provider import ModelRequest, ModelResponse
from domain.agent.enums import AgentTurnStatus
from domain.agent.models import (
    AgentConversation,
    AgentMessage,
    AgentTurn,
)
from domain.agent.models import (
    AgentToolReceipt as DurableToolReceipt,
)
from domain.common.ids import EntityIdPrefix

EVALUATION_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_MANIFEST_FILES = (
    "evals/agent-behavior.v1.json",
    "evals/copilot-research.v1.json",
    "src/application/services/copilot_research_evidence.py",
    "src/application/services/copilot_research_budget.py",
    "src/application/services/copilot_research_finalization.py",
    "src/interfaces/agent/prompts.py",
    "src/application/services/agent_runtime_service.py",
    "src/interfaces/agent/capability_gateway.py",
    "src/interfaces/agent/action_gateway.py",
    "src/application/ports/agent_model_provider.py",
)


class EvaluationIds:
    def __init__(self) -> None:
        self._counter = 0

    def new(self, prefix: EntityIdPrefix) -> str:
        self._counter += 1
        return f"{prefix.value}_eval_{self._counter:04d}"


class EvaluationClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now


class MemoryConversationRepository:
    def __init__(self) -> None:
        self.conversations: dict[str, AgentConversation] = {}
        self.messages: dict[str, list[AgentMessage]] = {}
        self.turns: dict[str, AgentTurn] = {}
        self.receipts: list[DurableToolReceipt] = []

    def create_conversation(self, value: AgentConversation) -> AgentConversation:
        self.conversations[value.conversation_id] = value
        self.messages[value.conversation_id] = []
        return value

    def get_conversation(self, conversation_id: str) -> AgentConversation | None:
        return self.conversations.get(conversation_id)

    def list_conversations(
        self,
        owner_principal: str | None = None,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[AgentConversation, ...]:
        _ = include_archived
        values = tuple(
            item
            for item in self.conversations.values()
            if owner_principal is None or item.owner_principal == owner_principal
        )
        return values[:limit]

    def list_messages(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        newest_first: bool = False,
    ) -> tuple[AgentMessage, ...]:
        values = tuple(
            item
            for item in self.messages.get(conversation_id, ())
            if item.sequence > after_sequence
        )
        ordered = tuple(reversed(values)) if newest_first else values
        return ordered[:limit]

    def append_message(self, value: AgentMessage) -> AgentMessage:
        conversation = self.conversations[value.conversation_id]
        stored = replace(value, sequence=conversation.next_message_sequence)
        self.messages[value.conversation_id].append(stored)
        self.conversations[value.conversation_id] = replace(
            conversation,
            next_message_sequence=conversation.next_message_sequence + 1,
            updated_at=value.created_at,
            version=conversation.version + 1,
        )
        return stored

    def append_tool_receipt(self, value: DurableToolReceipt) -> DurableToolReceipt:
        self.receipts.append(value)
        return value

    def create_turn(self, value: AgentTurn) -> AgentTurn:
        self.turns[value.turn_id] = value
        return value

    def get_turn(self, turn_id: str) -> AgentTurn | None:
        return self.turns.get(turn_id)

    def latest_turn(self, conversation_id: str) -> AgentTurn | None:
        values = [item for item in self.turns.values() if item.conversation_id == conversation_id]
        return max(values, key=lambda item: item.started_at) if values else None

    def list_turns(
        self, conversation_id: str, *, limit: int = 100, newest_first: bool = True
    ) -> tuple[AgentTurn, ...]:
        values = tuple(
            item for item in self.turns.values() if item.conversation_id == conversation_id
        )
        return tuple(sorted(values, key=lambda item: item.started_at, reverse=newest_first))[:limit]

    def update_turn(
        self,
        turn_id: str,
        *,
        status: AgentTurnStatus,
        expected_version: int,
        assistant_message_id: str | None = None,
        error_code: str | None = None,
        error_http_status: int | None = None,
        error_retryable: bool | None = None,
        error_attempts: int | None = None,
        completed_at: datetime | None = None,
        now: datetime | None = None,
    ) -> AgentTurn:
        current = self.turns[turn_id]
        if current.version != expected_version:
            raise RuntimeError("version mismatch")
        updated = replace(
            current,
            status=status,
            assistant_message_id=assistant_message_id,
            error_code=error_code,
            error_http_status=error_http_status,
            error_retryable=error_retryable,
            error_attempts=error_attempts,
            completed_at=completed_at,
            updated_at=now or current.updated_at,
            version=current.version + 1,
        )
        self.turns[turn_id] = updated
        return updated

    def update_summary(self, conversation_id: str, *args: Any, **kwargs: Any) -> AgentConversation:
        return self.conversations[conversation_id]

    def list_tool_receipts(
        self, conversation_id: str, *, limit: int = 100
    ) -> tuple[DurableToolReceipt, ...]:
        return tuple(item for item in self.receipts if item.conversation_id == conversation_id)[
            :limit
        ]


class ScriptedModelProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


def evaluation_fingerprint_manifest() -> dict[str, str]:
    """Hash the safety-critical prompt/model/capability inputs for CI gates."""

    manifest: dict[str, str] = {}
    for relative in _MANIFEST_FILES:
        path = EVALUATION_PROJECT_ROOT / relative
        if not path.is_file():
            raise ValueError(f"evaluation manifest file is missing: {relative}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[relative] = digest
    return manifest


__all__ = [
    "EVALUATION_PROJECT_ROOT",
    "EvaluationClock",
    "EvaluationIds",
    "MemoryConversationRepository",
    "ScriptedModelProvider",
    "evaluation_fingerprint_manifest",
]
