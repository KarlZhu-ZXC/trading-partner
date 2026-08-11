"""Application port for exact Agent-D operation validation and invocation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AgentActionInvocationResult:
    """Bounded result projection plus a secret-safe durable receipt JSON."""

    result: object
    receipt_json: str


class AgentActionOperationGateway(Protocol):
    def validate_operation(
        self,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    async def invoke_operation(
        self,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AgentActionInvocationResult: ...


class AgentPendingActionGateway(Protocol):
    """Channel-neutral pending lifecycle consumed by the model runtime."""

    def prepare(
        self,
        *,
        conversation_id: str,
        channel: Any,
        principal: str,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
        presented_summary: str,
    ) -> Any: ...


__all__ = [
    "AgentActionInvocationResult",
    "AgentActionOperationGateway",
    "AgentPendingActionGateway",
]
