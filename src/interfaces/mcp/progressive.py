"""Progressive MCP discovery and capability gateways.

The compact registry remains the source of truth for validation, confirmation,
compaction, and handler invocation.  This module only builds a registration-time
index and exposes the small public discovery/read/write transport surface.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import Tool as MCPTool
from mcp.types import ToolAnnotations

from interfaces.mcp.tool_inventory import DIRECT_PUBLIC_TOOL_NAMES
from interfaces.mcp.tools.compact import (
    CapabilityEffect,
    CapabilityPolicy,
    CompactCapabilityRegistry,
    CompactOperationDescriptor,
)

MAX_DISCOVERY_TOKEN_LENGTH: Final = 100
DISCOVERY_TOOL_NAME: Final = "capability_discover"
READ_TOOL_NAME: Final = "capability_read"
WRITE_TOOL_NAME: Final = "capability_write"
DISCOVERY_DESCRIPTION: Final = (
    "List public capabilities when tool is omitted, or return one exact call schema."
)
READ_DESCRIPTION: Final = "Read one public capability with its original arguments."
WRITE_DESCRIPTION: Final = "Invoke one confirmed public capability with its original arguments."
_INVALID_REQUEST = "Invalid capability request."
_UNKNOWN_TOOL = "Unknown public capability."
_WRONG_ROUTE = "Capability is not available on this route."
_INVALID_CONFIRMATION = "Confirmation does not match the capability."
_UNKNOWN_OPERATION = "Unknown public operation."

# Short descriptions are the only prose emitted by the no-argument catalog.
SHORT_DESCRIPTIONS: Final[dict[str, str]] = {
    "system_health": "Read durable system health, data quality, and attention summary.",
    "instrument_resolve": "Resolve a market symbol to one canonical instrument.",
    "research_get": "Read durable Research Subjects, context, history, search, and attention.",
    "investment_case_manage": "Create, update, or archive a Research Subject with confirmation.",
    "research_judgment_propose": "Propose Research state, Thesis, Plan, or Instrument changes.",
    "research_judgment_confirm": "Confirm or resolve an explicit Research candidate decision.",
    "research_memory_append": "Append confirmed Journal, Decision, Agenda, or review records.",
    "a_share_get_facts": "Read sourced A-share facts and research datasets.",
    "market_data_get": "Read sourced quotes, bars, market context, and futures data.",
    "technical_get_snapshot": "Read deterministic indicators and Smart Money structure.",
    "technical_render_chart": "Render indicators and Smart Money structure from sourced bars.",
    "us_get_facts": "Read sourced US company, macro, sentiment, and market facts.",
    "external_state_sync": "Refresh explicitly requested broker or watchlist state.",
    "portfolio_get": "Read durable accounts, positions, activity, performance, and behavior.",
    "broker_order_manage": "Preview or manage confirmation-gated supported broker orders.",
    "research_workflow_run": "Run a confirmation-gated research or scorecard workflow.",
    "watchlist_get": "Read durable Watchlist Hub groups, items, and history.",
    "watchlist_manage": "Add or remove active-source Watchlist membership with confirmation.",
    "portfolio_risk_get": "Read durable portfolio risk exposure and policy context.",
    "risk_policy_update": "Append a confirmed portfolio risk-policy version.",
    "monitor_read": "Read durable Monitor definitions, runs, events, and dashboard.",
    "monitor_manage": "Create or update a versioned Monitor, or resolve an event.",
    "monitor_evaluate": "Evaluate a Monitor using deterministic sourced facts.",
    "view_get": "Read bounded Observation intake, review context, or current formal view.",
}


@dataclass(frozen=True, slots=True)
class _DiscoveryIndex:
    policies: dict[str, CapabilityPolicy]
    descriptions: dict[str, str]
    schemas: dict[str, dict[str, Any]]
    entries: dict[tuple[str, str | None], CompactOperationDescriptor]
    grouped: dict[str, tuple[CompactOperationDescriptor, ...]]
    read_tools: frozenset[str]
    write_tools: frozenset[str]


def _public_operation_names(schema: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def visit(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            if key == "operation":
                const = value.get("const")
                if isinstance(const, str):
                    found.add(const)
                enum = value.get("enum")
                if isinstance(enum, list):
                    found.update(item for item in enum if isinstance(item, str))
            for child_key, child in value.items():
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)

    visit(schema)
    return found


def _build_index(registry: CompactCapabilityRegistry) -> _DiscoveryIndex:
    policies = {
        name: policy for name, policy in registry.policies.items() if name in SHORT_DESCRIPTIONS
    }
    public_tools = {tool.name: tool for tool in registry.list_tools()}
    descriptions = {
        name: (public_tools[name].description or SHORT_DESCRIPTIONS[name])
        for name in policies
        if name in public_tools
    }
    schemas = {
        name: deepcopy(tool.inputSchema)
        for name, tool in public_tools.items()
        if name in policies
    }
    entries: dict[tuple[str, str | None], CompactOperationDescriptor] = {}
    grouped: dict[str, list[CompactOperationDescriptor]] = {}
    for descriptor in registry.operation_descriptors():
        capability = descriptor.capability
        if capability not in policies:
            continue
        public_schema = schemas.get(capability)
        if public_schema is not None and descriptor.operation is not None:
            allowed = _public_operation_names(public_schema)
            if allowed and descriptor.operation not in allowed:
                continue
            if not allowed and "request" in public_schema.get("properties", {}):
                continue
        entries.setdefault((capability, descriptor.operation), descriptor)
        if descriptor.operation is not None:
            grouped.setdefault(capability, []).append(descriptor)

    read_tools = frozenset(
        name
        for name, policy in policies.items()
        if name not in DIRECT_PUBLIC_TOOL_NAMES
        and policy.effect in {CapabilityEffect.READ_DURABLE, CapabilityEffect.READ_PROVIDER}
    )
    write_tools = frozenset(
        name
        for name, policy in policies.items()
        if name not in DIRECT_PUBLIC_TOOL_NAMES
        and policy.confirmation_required
    )
    return _DiscoveryIndex(
        policies=policies,
        descriptions=descriptions,
        schemas=schemas,
        entries=entries,
        grouped={name: tuple(items) for name, items in grouped.items()},
        read_tools=read_tools,
        write_tools=write_tools,
    )


def _validate_name(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_DISCOVERY_TOKEN_LENGTH:
        raise ToolError(_INVALID_REQUEST)
    return value


def _route(index: _DiscoveryIndex, tool: str) -> str:
    if tool in DIRECT_PUBLIC_TOOL_NAMES:
        return tool
    if tool in index.read_tools:
        return READ_TOOL_NAME
    if tool in index.write_tools:
        return WRITE_TOOL_NAME
    raise ToolError(_WRONG_ROUTE)


def _grouped_call_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap one exact grouped schema while keeping local refs valid."""

    request_schema = deepcopy(schema)
    definitions = request_schema.pop("$defs", None)
    result: dict[str, Any] = {
        "type": "object",
        "properties": {"request": request_schema},
        "required": ["request"],
        "additionalProperties": False,
    }
    if isinstance(definitions, dict):
        result["$defs"] = definitions
    return result


def _metadata(index: _DiscoveryIndex, tool: str, operation: str | None) -> dict[str, Any]:
    policy = index.policies[tool]
    return {
        "tool": tool,
        "operation": operation,
        "description": index.descriptions.get(tool, SHORT_DESCRIPTIONS[tool]),
        "effect": policy.effect.value,
        "confirmation": policy.confirmation.value,
        "call_tool": _route(index, tool),
    }


def _discover(index: _DiscoveryIndex, tool: Any, operation: Any) -> Any:
    tool_name = _validate_name(tool, optional=True)
    operation_name = _validate_name(operation, optional=True)
    if tool_name is None:
        if operation_name is not None:
            raise ToolError(_INVALID_REQUEST)
        return [
            {"tool": name, "description": description}
            for name, description in SHORT_DESCRIPTIONS.items()
            if name in index.policies
        ]
    if tool_name not in index.policies:
        raise ToolError(_UNKNOWN_TOOL)

    direct = index.entries.get((tool_name, None))
    grouped = index.grouped.get(tool_name, ())
    if operation_name is None:
        if grouped:
            return {"tool": tool_name, "operations": [item.operation for item in grouped]}
        if direct is None:
            raise ToolError(_UNKNOWN_OPERATION)
        result = _metadata(index, tool_name, None)
        result["inputSchema"] = direct.input_schema
        return result

    descriptor = index.entries.get((tool_name, operation_name))
    if descriptor is None or descriptor.operation is None:
        raise ToolError(_UNKNOWN_OPERATION)
    result = _metadata(index, tool_name, operation_name)
    result["inputSchema"] = _grouped_call_schema(descriptor.input_schema)
    return result


def public_input_schema(tool: MCPTool) -> dict[str, Any]:
    """Return the thin public input schema for one compact business tool."""

    schema = tool.inputSchema
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(properties, dict) and isinstance(properties.get("request"), dict):
        return {
            "type": "object",
            "properties": {
                "request": {
                    "type": "object",
                    "properties": {"operation": {"type": "string"}},
                    "required": ["operation"],
                    "additionalProperties": True,
                }
            },
            "required": ["request"],
            "additionalProperties": False,
        }
    if isinstance(properties, dict) and properties:
        return {"type": "object", "additionalProperties": True}
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _register_discovery_tool(server: FastMCP, index: _DiscoveryIndex) -> None:
    async def capability_discover(
        tool: str | None = None, operation: str | None = None
    ) -> Any:
        return _discover(index, tool, operation)

    server.add_tool(
        capability_discover,
        name=DISCOVERY_TOOL_NAME,
        description=DISCOVERY_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=False,
    )


def register_discovery(server: FastMCP, registry: CompactCapabilityRegistry) -> None:
    """Register only discovery for hosts that do not use the gateways."""

    _register_discovery_tool(server, _build_index(registry))


def register_gateways(server: FastMCP, registry: CompactCapabilityRegistry) -> None:
    """Register the read/write capability gateways."""

    index = _build_index(registry)

    async def capability_read(tool: str, arguments: dict[str, Any]) -> Any:
        tool_name = _validate_name(tool)
        if tool_name not in index.policies:
            raise ToolError(_UNKNOWN_TOOL)
        if tool_name not in index.read_tools:
            raise ToolError(_WRONG_ROUTE)
        return await registry.invoke(tool_name, dict(arguments))

    async def capability_write(
        tool: str, arguments: dict[str, Any], confirmation: str
    ) -> Any:
        tool_name = _validate_name(tool)
        if tool_name not in index.policies:
            raise ToolError(_UNKNOWN_TOOL)
        if tool_name not in index.write_tools:
            raise ToolError(_WRONG_ROUTE)
        if confirmation != tool_name:
            raise ToolError(_INVALID_CONFIRMATION)
        return await registry.invoke(tool_name, dict(arguments), confirmation=confirmation)

    server.add_tool(
        capability_read,
        name=READ_TOOL_NAME,
        description=READ_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    server.add_tool(
        capability_write,
        name=WRITE_TOOL_NAME,
        description=WRITE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )


__all__ = [
    "DISCOVERY_DESCRIPTION",
    "DISCOVERY_TOOL_NAME",
    "MAX_DISCOVERY_TOKEN_LENGTH",
    "READ_TOOL_NAME",
    "SHORT_DESCRIPTIONS",
    "WRITE_TOOL_NAME",
    "public_input_schema",
    "register_discovery",
    "register_gateways",
]
