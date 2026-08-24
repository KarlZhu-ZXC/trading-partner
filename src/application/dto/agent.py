"""Transport-neutral DTOs for one Shared Agent Runtime turn."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from application.ports.agent_model_provider import ModelUsage
from application.ports.agent_tool_gateway import AgentToolReceipt
from domain.agent.attachments import (
    AGENT_IMAGE_MAX_BYTES,
    AGENT_IMAGE_MAX_COUNT,
    AGENT_IMAGE_MAX_TOTAL_BYTES,
    AGENT_IMAGE_MEDIA_TYPES,
)
from domain.agent.enums import AgentChannel

# Ephemeral host context is intentionally kept small.  These limits belong to
# the transport-neutral application contract as well as the Console adapter so
# another channel cannot accidentally bypass the runtime safety boundary.
EPHEMERAL_CONTEXT_PATH_MAX_CHARS = 1_024
EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS = 8_192
EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS = 16_384
EPHEMERAL_CONTEXT_MAX_BYTES = 16_384
EPHEMERAL_CONTEXT_ROUTE_HASH_MAX_CHARS = 256
EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS = 160
EPHEMERAL_CONTEXT_SURFACE_MAX_CHARS = 96
_EPHEMERAL_CONTEXT_ROUTE_HASH_PATTERN = re.compile(
    r"^[A-Za-z0-9._~:/?#=&%+-]+$"
)
_EPHEMERAL_CONTEXT_SURFACE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_EPHEMERAL_CONTEXT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


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
    route_hash: str | None = None
    surface: str | None = None
    selected_subject_id: str | None = None
    selected_monitor_id: str | None = None
    selected_run_id: str | None = None
    active_tab: str | None = None
    workbench_subject_id: str | None = None

    def __post_init__(self) -> None:
        values = (
            ("location", self.location, EPHEMERAL_CONTEXT_PATH_MAX_CHARS),
            ("selection", self.selection, EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS),
            ("content_excerpt", self.content_excerpt, EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS),
            ("route_hash", self.route_hash, EPHEMERAL_CONTEXT_ROUTE_HASH_MAX_CHARS),
            ("surface", self.surface, EPHEMERAL_CONTEXT_SURFACE_MAX_CHARS),
            (
                "selected_subject_id",
                self.selected_subject_id,
                EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
            ),
            (
                "selected_monitor_id",
                self.selected_monitor_id,
                EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
            ),
            ("selected_run_id", self.selected_run_id, EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS),
            ("active_tab", self.active_tab, EPHEMERAL_CONTEXT_SURFACE_MAX_CHARS),
            (
                "workbench_subject_id",
                self.workbench_subject_id,
                EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
            ),
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
                pattern = (
                    _EPHEMERAL_CONTEXT_ROUTE_HASH_PATTERN
                    if field_name == "route_hash"
                    else _EPHEMERAL_CONTEXT_SURFACE_PATTERN
                    if field_name in {"surface", "active_tab"}
                    else _EPHEMERAL_CONTEXT_ID_PATTERN
                    if field_name
                    in {
                        "selected_subject_id",
                        "selected_monitor_id",
                        "selected_run_id",
                        "workbench_subject_id",
                    }
                    else None
                )
                if pattern is not None and pattern.fullmatch(value) is None:
                    raise ValueError(f"{field_name} has an invalid context pattern")
                total_bytes += len(value.encode("utf-8"))
        if total_bytes > EPHEMERAL_CONTEXT_MAX_BYTES:
            raise ValueError("ephemeral context exceeds the bounded total size")

    def as_dict(self) -> dict[str, str | None]:
        """Return a compact mapping for the current model-context message."""

        return {
            "location": self.location,
            "selection": self.selection,
            "content_excerpt": self.content_excerpt,
            "route_hash": self.route_hash,
            "surface": self.surface,
            "selected_subject_id": self.selected_subject_id,
            "selected_monitor_id": self.selected_monitor_id,
            "selected_run_id": self.selected_run_id,
            "active_tab": self.active_tab,
            "workbench_subject_id": self.workbench_subject_id,
        }


AgentTurnEventName = Literal[
    "message_started",
    "tool_started",
    "tool_finished",
    "pending_action",
    "text_delta",
    "completed",
    "failed",
    "cancelled",
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
class AgentImageInput:
    """Validated in-memory image input before private storage."""

    content: bytes
    media_type: str
    original_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not 1 <= len(self.content) <= (
            AGENT_IMAGE_MAX_BYTES
        ):
            raise ValueError("image content is out of bounds")
        if self.media_type not in AGENT_IMAGE_MEDIA_TYPES:
            raise ValueError("image media type is unsupported")
        if self.original_name is not None and (
            not self.original_name.strip() or len(self.original_name) > 255
        ):
            raise ValueError("image original name is invalid")


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    conversation_id: str
    owner_principal: str
    channel: AgentChannel
    content: str
    model_id: str | None = None
    model: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    external_message_ref: str | None = None
    ephemeral_context: EphemeralContext | None = None
    attachments: tuple[AgentImageInput, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.attachments, tuple):
            raise ValueError("Agent attachments must be a tuple")
        if len(self.attachments) > AGENT_IMAGE_MAX_COUNT:
            raise ValueError("Too many Agent image attachments")
        if any(not isinstance(item, AgentImageInput) for item in self.attachments):
            raise ValueError("Agent image attachments are invalid")
        if sum(len(item.content) for item in self.attachments) > AGENT_IMAGE_MAX_TOTAL_BYTES:
            raise ValueError("Agent image attachments exceed the total size bound")


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    text: str
    tool_rounds: int
    tool_receipts: tuple[AgentToolReceipt, ...]
    # Durable lifecycle identity.  ``None`` is retained for lightweight
    # compatibility fixtures that do not provide the turn repository.
    turn_id: str | None = None
    selected_provider_id: str | None = None
    selected_model: str | None = None
    route_reason: str | None = None
    fallback_from: str | None = None
    fallback_code: str | None = None
    artifact_urls: tuple[str, ...] = ()
    evidence_manifest: str | None = None
    capability_search_audits: tuple[Mapping[str, object], ...] = ()
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
