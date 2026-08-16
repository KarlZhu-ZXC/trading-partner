"""Provider-neutral port for the shared Agent model runtime.

The Agent runtime deliberately speaks in a small, OpenAI-compatible vocabulary.
It does not expose a vendor name, an SDK type, or a provider-specific payload to
the application layer.  Infrastructure adapters translate these immutable
messages to the configured wire protocol and back again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

type ModelRole = Literal["system", "user", "assistant", "tool"]
type ModelReasoningEffort = Literal["low", "medium", "high", "max"]


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    """A function/tool call emitted by a model.

    ``arguments`` is kept as a string because OpenAI-compatible endpoints are
    not consistent about whether they return a JSON object or a JSON-encoded
    string.  Code which wants structured arguments can parse it after applying
    the gateway's schema validation.
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """One provider-neutral conversation message."""

    role: ModelRole
    content: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return the compact mapping used by both protocol codecs."""

        result: dict[str, object] = {"role": self.role}
        if self.content is not None:
            result["content"] = self.content
        if self.tool_calls:
            result["tool_calls"] = tuple(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            )
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            result["name"] = self.name
        return result


@dataclass(frozen=True, slots=True)
class ModelTool:
    """A function definition advertised to the model."""

    name: str
    description: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        function: dict[str, object] = {"name": self.name, "parameters": dict(self.parameters)}
        if self.description is not None:
            function["description"] = self.description
        return {"type": "function", "function": function}


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Token usage returned by an upstream model, when available."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    web_search_calls: int | None = None
    web_extractor_calls: int | None = None

    @property
    def prompt_tokens(self) -> int | None:
        return self.input_tokens

    @property
    def completion_tokens(self) -> int | None:
        return self.output_tokens


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Provider-neutral model request.

    Messages/tools may be supplied either as the typed dataclasses above or as
    ordinary mappings.  Supporting mappings keeps the application gateway
    decoupled from a particular serialization library while still offering a
    typed convenience API for callers.
    """

    messages: Sequence[ModelMessage | Mapping[str, Any]] = ()
    tools: Sequence[ModelTool | Mapping[str, Any]] = ()
    model: str | None = None
    reasoning_mode: Literal["none", "effort", "thinking"] = "none"
    reasoning_effort: str | None = None
    max_output_tokens: int | None = None
    native_web_search: bool = False


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized model result shared by Console and Telegram runtimes."""

    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    usage: ModelUsage | None = None
    model: str | None = None
    finish_reason: str | None = None
    web_search_used: bool = False
    web_extractor_used: bool = False
    web_source_urls: tuple[str, ...] = ()
    request_id: str | None = None
    latency_ms: int | None = None

    @property
    def content(self) -> str:
        """Compatibility spelling for callers accustomed to chat responses."""

        return self.text


@dataclass(frozen=True, slots=True)
class ModelStreamChunk:
    """One normalized increment from an optional provider streaming port."""

    text_delta: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    usage: ModelUsage | None = None
    model: str | None = None
    finish_reason: str | None = None
    web_search_used: bool = False
    web_extractor_used: bool = False
    web_source_urls: tuple[str, ...] = ()
    request_id: str | None = None
    latency_ms: int | None = None
    done: bool = False
    final_response: ModelResponse | None = None


@dataclass(frozen=True, slots=True)
class ModelCatalogItem:
    """One secret-safe model advertised by a configured Provider."""

    id: str
    reasoning_efforts: tuple[ModelReasoningEffort, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """Bounded model directory fetched server-side with Provider credentials."""

    models: tuple[ModelCatalogItem, ...]
    fetched_at: datetime
    cached: bool = False


# Explicit Agent-prefixed aliases make the boundary discoverable without
# forcing callers to repeat the longer names in every type annotation.
AgentModelRequest = ModelRequest
AgentModelResponse = ModelResponse
AgentModelMessage = ModelMessage
AgentModelToolCall = ModelToolCall
AgentModelTool = ModelTool
AgentModelUsage = ModelUsage
AgentModelStreamChunk = ModelStreamChunk
AgentModelCatalog = ModelCatalog
AgentModelCatalogItem = ModelCatalogItem


class AgentModelProvider(Protocol):
    """Port implemented by an OpenAI-compatible Agent model adapter."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one model turn without mutating Trading Partner state."""
        ...

    async def aclose(self) -> None:
        """Release owned HTTP resources; injected clients may be no-op."""
        ...


class AgentModelCatalogProvider(Protocol):
    """Optional extension for Providers which expose a model directory."""

    async def list_models(self, *, force_refresh: bool = False) -> ModelCatalog:
        """Return only normalized model identifiers and supported effort values."""
        ...


class AgentModelStreamProvider(Protocol):
    """Optional token streaming extension for an Agent model adapter."""

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        """Yield normalized deltas; adapters may omit this port entirely."""
        ...
