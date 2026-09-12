"""Progressive MCP discovery keeps tools/list thin without weakening exact calls."""

from __future__ import annotations

from typing import Any, Literal, cast
from unittest.mock import MagicMock

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, ConfigDict

from interfaces.mcp.progressive import (
    DISCOVERY_TOOL_NAME,
    MAX_DISCOVERY_TOKEN_LENGTH,
    READ_TOOL_NAME,
    SHORT_DESCRIPTIONS,
    WRITE_TOOL_NAME,
    public_input_schema,
    register_discovery,
    register_gateways,
)
from interfaces.mcp.server import create_capability_registry
from interfaces.mcp.tool_inventory import MCP_VNEXT_TOOL_NAMES
from interfaces.mcp.tools.compact import (
    READ_DURABLE,
    CompactCapabilityRegistry,
    CompactOperationDescriptor,
)


class _GroupedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["one", "two"]


def _registry() -> tuple[CompactCapabilityRegistry, dict[str, int]]:
    registry = CompactCapabilityRegistry()
    calls = {"direct": 0, "grouped": 0}

    async def direct(limit: int | None = None) -> Any:
        calls["direct"] += 1
        return {"limit": limit}

    async def grouped(request: _GroupedRequest) -> Any:
        calls["grouped"] += 1
        return {"operation": request.operation}

    registry.add_capability(
        direct,
        name="system_health",
        description="The original direct capability description.",
        policy=READ_DURABLE,
    )
    registry.add_capability(
        grouped,
        name="portfolio_get",
        description="The original grouped capability description.",
        policy=READ_DURABLE,
        register_direct=False,
    )

    exact_schema = {
        "$defs": {
            "Shared": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            }
        },
        "type": "object",
        "properties": {
            "operation": {"const": "one"},
            "limit": {"type": "integer", "default": 5},
            "nullable": {
                "anyOf": [{"$ref": "#/$defs/Shared"}, {"type": "null"}]
            },
        },
        "required": ["operation"],
        "additionalProperties": False,
    }
    for operation in ("one", "two"):
        schema = {
            **exact_schema,
            "properties": {
                **cast(dict[str, Any], exact_schema["properties"]),
                "operation": {"const": operation},
            },
        }
        registry.register_operation(
            CompactOperationDescriptor(
                capability="portfolio_get",
                operation=operation,
                description=f"Exact description for {operation}.",
                schema=schema,
                policy=READ_DURABLE,
            ),
            invoke=lambda arguments: _never_called(arguments),
        )

    # This descriptor has a public capability name but is absent from that
    # capability's public operation discriminator. It must not be discoverable.
    registry.register_operation(
        CompactOperationDescriptor(
                capability="portfolio_get",
            operation="internal_only",
            description="Private operation.",
            schema={"type": "object"},
            policy=READ_DURABLE,
        ),
        invoke=lambda arguments: _never_called(arguments),
    )
    return registry, calls


async def _never_called(arguments: dict[str, Any]) -> Any:
    raise AssertionError(f"discovery invoked a handler: {arguments}")


async def _run_discovery(
    registry: CompactCapabilityRegistry,
    arguments: dict[str, Any],
) -> Any:
    server = FastMCP("progressive-test")
    register_discovery(server, registry)
    tool = server._tool_manager.get_tool(DISCOVERY_TOOL_NAME)
    assert tool is not None
    return await tool.run(arguments)


@pytest.mark.asyncio
async def test_group_catalog_is_small_and_does_not_publish_schemas() -> None:
    registry, calls = _registry()

    result = await _run_discovery(registry, {"tool": "portfolio_get"})

    assert result["tool"] == "portfolio_get"
    assert set(result) == {"tool", "operations"}
    assert "inputSchema" not in result
    assert result["operations"] == ["one", "two"]
    assert calls == {"direct": 0, "grouped": 0}


@pytest.mark.asyncio
async def test_capability_catalog_accepts_no_tool_and_lists_names_only() -> None:
    registry, calls = _registry()

    result = await _run_discovery(registry, {})

    assert [item["tool"] for item in result] == ["system_health", "portfolio_get"]
    assert len(result) == 2
    assert all(set(item) == {"tool", "description"} for item in result)
    assert calls == {"direct": 0, "grouped": 0}


@pytest.mark.asyncio
async def test_selected_group_schema_preserves_refs_defaults_and_policy_metadata() -> None:
    registry, calls = _registry()

    result = await _run_discovery(
        registry,
        {"tool": "portfolio_get", "operation": "one"},
    )

    assert result["tool"] == "portfolio_get"
    assert result["operation"] == "one"
    assert result["description"] == "The original grouped capability description."
    assert "operation_description" not in result
    assert result["effect"] == READ_DURABLE.effect.value
    assert result["confirmation"] == READ_DURABLE.confirmation.value
    assert result["call_tool"] == READ_TOOL_NAME

    schema = result["inputSchema"]
    assert schema["required"] == ["request"]
    assert schema["additionalProperties"] is False
    request_schema = schema["properties"]["request"]
    assert request_schema["properties"]["limit"]["default"] == 5
    assert request_schema["properties"]["nullable"]["anyOf"][0] == {
        "$ref": "#/$defs/Shared"
    }
    assert schema["$defs"]["Shared"]["required"] == ["value"]
    assert not list(
        Draft202012Validator(schema).iter_errors(
            {"request": {"operation": "one", "nullable": {"value": "ok"}}}
        )
    )
    assert calls == {"direct": 0, "grouped": 0}


@pytest.mark.asyncio
async def test_direct_discovery_returns_exact_schema_without_request_wrapper() -> None:
    registry, calls = _registry()

    result = await _run_discovery(registry, {"tool": "system_health"})

    assert result["operation"] is None
    assert result["call_tool"] == READ_TOOL_NAME
    assert result["inputSchema"] == registry.find_operation("system_health").input_schema
    assert "request" not in result["inputSchema"].get("properties", {})
    assert calls == {"direct": 0, "grouped": 0}


def test_public_input_schema_is_thin_and_keeps_grouped_request_boundary() -> None:
    grouped = MCPTool(
        name="grouped",
        inputSchema={
            "type": "object",
            "properties": {"request": {"oneOf": [{"$ref": "#/$defs/V0"}]}},
            "required": ["request"],
        },
    )
    direct = MCPTool(
        name="direct",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}},
    )
    empty = MCPTool(name="empty", inputSchema={"type": "object", "properties": {}})

    assert public_input_schema(grouped) == {
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
    assert public_input_schema(direct) == {
        "type": "object",
        "additionalProperties": True,
    }
    assert public_input_schema(empty) == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


@pytest.mark.asyncio
async def test_unknown_names_and_internal_operations_fail_without_handler_calls() -> None:
    registry, calls = _registry()

    for arguments in (
        {"tool": "missing"},
        {"tool": "portfolio_get", "operation": "internal_only"},
        {"tool": "portfolio_get", "operation": "missing"},
        {"tool": "x" * (MAX_DISCOVERY_TOKEN_LENGTH + 1)},
        {"tool": "portfolio_get", "operation": "x" * (MAX_DISCOVERY_TOKEN_LENGTH + 1)},
    ):
        with pytest.raises(ToolError) as error:
            await _run_discovery(registry, arguments)
        assert len(str(error.value)) <= 128

    assert calls == {"direct": 0, "grouped": 0}


@pytest.mark.asyncio
async def test_capability_gateways_reject_wrong_routes_and_confirmation() -> None:
    container = MagicMock()
    container.settings.mcp_server_name = "gateway-test"
    container.services = MagicMock()
    registry = create_capability_registry(container)
    server = FastMCP("gateway-test")
    register_gateways(server, registry)
    manager = server._tool_manager

    for tool in (
        "external_state_sync",
        "broker_order_manage",
        "research_judgment_confirm",
        "investment_case_manage",
    ):
        with pytest.raises(ToolError):
            await manager.call_tool(
                READ_TOOL_NAME,
                {"tool": tool, "arguments": {}},
            )

    with pytest.raises(ToolError):
        await manager.call_tool(
            WRITE_TOOL_NAME,
            {
                "tool": "research_get",
                "arguments": {},
                "confirmation": "research_get",
            },
        )
    with pytest.raises(ToolError):
        await manager.call_tool(
            WRITE_TOOL_NAME,
            {
                "tool": "investment_case_manage",
                "arguments": {},
                "confirmation": "research_memory_append",
            },
        )
    assert container.services.mock_calls == []


def test_short_descriptions_cover_the_24_business_tools() -> None:
    registry, _ = _registry()
    # The synthetic registry is intentionally small; the mapping itself is the
    # public inventory contract exercised here without constructing providers.
    assert len(SHORT_DESCRIPTIONS) == 24
    assert set(SHORT_DESCRIPTIONS) == MCP_VNEXT_TOOL_NAMES
    assert all(
        description and len(description) <= 120
        for description in SHORT_DESCRIPTIONS.values()
    )
