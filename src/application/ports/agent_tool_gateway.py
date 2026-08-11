"""Transport-neutral read-only Agent tool gateway port.

The Agent runtime only knows this small contract.  It does not receive the
27-tool MCP inventory and never gets a database/provider handle.  Concrete
gateways live at the interface boundary and reuse the compact registry's exact
Pydantic operation validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AgentToolDescriptor:
    """One exact operation schema exposed to the Agent model."""

    capability: str
    operation: str | None
    description: str
    schema: dict[str, Any]
    effect: str
    confirmation_required: bool
    auto_allowed: bool
    direct: bool = False

    @property
    def capability_name(self) -> str:
        return self.capability

    @property
    def input_schema(self) -> dict[str, Any]:
        # Never let a caller mutate the registry-owned schema.
        from copy import deepcopy

        return deepcopy(self.schema)

    @property
    def request_schema(self) -> dict[str, Any]:
        return self.input_schema

    @property
    def exact_schema(self) -> dict[str, Any]:
        return self.input_schema

    @property
    def arguments_schema(self) -> dict[str, Any]:
        schema = self.input_schema
        properties = schema.get("properties")
        if isinstance(properties, dict) and "operation" in properties:
            properties.pop("operation")
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [item for item in required if item != "operation"]
            if not schema["required"]:
                schema.pop("required", None)
        return schema

    def as_dict(self) -> dict[str, Any]:
        from copy import deepcopy

        return {
            "capability": self.capability,
            "operation": self.operation,
            "description": self.description,
            "schema": deepcopy(self.schema),
            "exact_schema": deepcopy(self.schema),
            "arguments_schema": self.arguments_schema,
            "effect": self.effect,
            "confirmation_required": self.confirmation_required,
            "auto_allowed": self.auto_allowed,
            "direct": self.direct,
        }


@dataclass(frozen=True, slots=True)
class AgentToolReceipt:
    """Bounded, secret-safe metadata for one Agent tool read."""

    capability: str
    operation: str | None
    request_id: str | None = None
    effect: str | None = None
    degraded: bool = False
    source_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    error_code: str | None = None
    result_size_bytes: int = 0
    result_truncated: bool = False

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.warning_codes

    @property
    def size_bytes(self) -> int:
        return self.result_size_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "operation": self.operation,
            "request_id": self.request_id,
            "effect": self.effect,
            "degraded": self.degraded,
            "source_codes": list(self.source_codes),
            "warning_codes": list(self.warning_codes),
            "error_code": self.error_code,
            "result_size_bytes": self.result_size_bytes,
            "result_truncated": self.result_truncated,
        }


@dataclass(frozen=True, slots=True)
class AgentToolResult:
    """Structured result returned by :meth:`AgentToolGateway.read`."""

    result: Any
    receipt: AgentToolReceipt

    @property
    def data(self) -> Any:
        """Compatibility alias for runtimes that call the payload ``data``."""

        return self.result

    def as_dict(self) -> dict[str, Any]:
        return {"result": self.result, "receipt": self.receipt.as_dict()}


AgentToolReadResult = AgentToolResult
AgentCapabilityDescriptor = AgentToolDescriptor
CapabilityDescriptor = AgentToolDescriptor
AgentReceipt = AgentToolReceipt
ToolReceipt = AgentToolReceipt
ToolReadResult = AgentToolResult


class AgentToolGateway(Protocol):
    """Minimal capability search/read API consumed by the shared Agent loop."""

    def search(self, query: str, limit: int = 3) -> tuple[AgentToolDescriptor, ...]:
        """Return a deterministic, bounded set of exact operation descriptors."""

    async def read(
        self,
        capability: str,
        operation: str | None,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        """Validate and execute one Agent-A-allowed operation."""


__all__ = [
    "AgentToolDescriptor",
    "AgentCapabilityDescriptor",
    "AgentToolGateway",
    "AgentToolReceipt",
    "AgentToolReadResult",
    "AgentToolResult",
    "AgentReceipt",
    "CapabilityDescriptor",
    "ToolReceipt",
    "ToolReadResult",
]
