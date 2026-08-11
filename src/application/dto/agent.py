"""Transport-neutral DTOs for one Shared Agent Runtime turn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from application.ports.agent_model_provider import ModelUsage
from application.ports.agent_tool_gateway import AgentToolReceipt
from domain.agent.enums import AgentChannel

# Ephemeral host context is intentionally kept small.  These limits belong to
# the transport-neutral application contract as well as the Console adapter so
# another channel cannot accidentally bypass the runtime safety boundary.
EPHEMERAL_CONTEXT_PATH_MAX_CHARS = 1_024
EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS = 8_192
EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS = 16_384
EPHEMERAL_CONTEXT_MAX_BYTES = 16_384


@dataclass(frozen=True, slots=True)
class EphemeralContext:
    """Untrusted, one-turn context supplied by the current Console surface.

    This DTO deliberately has no persistence representation.  The runtime may
    use it while assembling one model request, but it is never part of an
    ``AgentMessage`` or a rolling summary.
    """

    location: str | None = None
    selection: str | None = None
    content_excerpt: str | None = None

    def __post_init__(self) -> None:
        values = (
            ("location", self.location, EPHEMERAL_CONTEXT_PATH_MAX_CHARS),
            ("selection", self.selection, EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS),
            ("content_excerpt", self.content_excerpt, EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS),
        )
        total_bytes = 0
        for field_name, value, max_chars in values:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field_name} must be text or null")
            if value is not None:
                if not value:
                    raise ValueError(f"{field_name} must not be blank")
                if len(value) > max_chars:
                    raise ValueError(f"{field_name} exceeds the bounded context limit")
                total_bytes += len(value.encode("utf-8"))
        if total_bytes > EPHEMERAL_CONTEXT_MAX_BYTES:
            raise ValueError("ephemeral context exceeds the bounded total size")

    def as_dict(self) -> dict[str, str | None]:
        """Return a compact mapping for the current model-context message."""

        return {
            "location": self.location,
            "selection": self.selection,
            "content_excerpt": self.content_excerpt,
        }


AgentTurnEventName = Literal[
    "message_started",
    "tool_started",
    "tool_finished",
    "pending_action",
    "text_delta",
    "completed",
    "failed",
]


@dataclass(frozen=True, slots=True)
class AgentTurnEvent:
    """One bounded transport event emitted while an Agent turn runs.

    The runtime emits these events for channel adapters such as Console SSE;
    they are deliberately transport-neutral and contain only identifiers,
    receipt metadata, or the final bounded answer text.
    """

    type: AgentTurnEventName
    data: Mapping[str, object] = field(default_factory=dict)

    @property
    def event(self) -> AgentTurnEventName:
        """Compatibility alias for adapters that call the discriminator event."""

        return self.type

    def as_dict(self) -> dict[str, object]:
        return {"type": self.type, "data": dict(self.data)}


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    conversation_id: str
    owner_principal: str
    channel: AgentChannel
    content: str
    external_message_ref: str | None = None
    ephemeral_context: EphemeralContext | None = None


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    text: str
    tool_rounds: int
    tool_receipts: tuple[AgentToolReceipt, ...]
    usage: ModelUsage | None = None
    web_search_used: bool = False
    web_extractor_used: bool = False
    web_source_urls: tuple[str, ...] = ()
    model_request_id: str | None = None
    model_latency_ms: int | None = None
    tool_trace: tuple[str, ...] = ()


__all__ = [
    "EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS",
    "EPHEMERAL_CONTEXT_MAX_BYTES",
    "EPHEMERAL_CONTEXT_PATH_MAX_CHARS",
    "EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS",
    "AgentTurnEvent",
    "AgentTurnEventName",
    "AgentTurnRequest",
    "AgentTurnResult",
    "EphemeralContext",
]
