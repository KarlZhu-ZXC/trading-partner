"""Compact MCP surface inventory, schema, and explicit-sync boundary tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from interfaces.mcp.server import (
    COMPACT_28_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)


class _Envelope:
    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {"ok": True, "request_id": "req_compact", "data": {}}


def _container() -> MagicMock:
    container = MagicMock()
    container.settings = SimpleNamespace(mcp_server_name="Trading Partner Test")
    container.services = MagicMock()
    return container


def _wire_size(tools: list[Any]) -> int:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
            "outputSchema": tool.outputSchema,
            "annotations": (tool.annotations.model_dump(mode="json") if tool.annotations else None),
        }
        for tool in tools
    ]
    return len(json.dumps(payload, separators=(",", ":")))


def _local_definition_refs(value: Any) -> set[str]:
    if isinstance(value, dict):
        refs: set[str] = set()
        for item in value.values():
            refs.update(_local_definition_refs(item))
        return refs
    if isinstance(value, list):
        refs = set()
        for item in value:
            refs.update(_local_definition_refs(item))
        return refs
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return {value.rsplit("/", 1)[-1]}
    return set()


@pytest.mark.asyncio
async def test_compact_is_the_only_public_surface() -> None:
    compact = await create_mcp_server(_container()).list_tools()
    compact_names = {tool.name for tool in compact}

    assert PUBLIC_TOOL_NAMES == COMPACT_28_TOOL_NAMES
    assert compact_names == COMPACT_28_TOOL_NAMES
    assert len(compact_names) == 28


@pytest.mark.asyncio
async def test_technical_snapshot_description_discloses_cross_market_support() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}

    description = tools["technical_get_snapshot"].description
    assert "A-share" in description
    assert "US" in description
    assert "CME" in description
    assert "OTC" in description


@pytest.mark.asyncio
async def test_compact_grouped_tools_publish_closed_discriminated_request_unions() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}
    expected_variants = {
        "investment_case_read": 2,
        "market_data_get": 6,
        "external_state_sync": 3,
        "research_workflow_run": 8,
        "monitor_read": 4,
        "monitor_manage": 3,
    }

    for name, variant_count in expected_variants.items():
        schema = tools[name].inputSchema
        assert schema["required"] == ["request"]
        request = schema["properties"]["request"]
        assert request["discriminator"]["propertyName"] == "operation"
        assert len(request["oneOf"]) == variant_count
        for variant in request["oneOf"]:
            definition = variant["$ref"].rsplit("/", 1)[-1]
            assert "operation" in schema["$defs"][definition]["required"]


@pytest.mark.asyncio
async def test_judgment_confirmation_schema_exposes_chat_authorization_provenance() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}
    properties = tools["research_judgment_confirm"].inputSchema["properties"]

    assert properties["reviewed_by"]["enum"] == ["user", "external_agent", "codex"]
    assert properties["submitted_via"]["enum"] == ["direct", "codex_chat"]
    assert "authorization_note" in properties


@pytest.mark.asyncio
async def test_compact_wire_schema_and_each_tool_stay_bounded() -> None:
    compact = await create_mcp_server(_container()).list_tools()

    assert _wire_size(compact) <= 64 * 1024
    assert sum(
        len(json.dumps(tool.inputSchema, separators=(",", ":"))) for tool in compact
    ) <= 36 * 1024
    for tool in compact:
        assert len(json.dumps(tool.inputSchema, separators=(",", ":"))) <= 8 * 1024, tool.name


@pytest.mark.asyncio
async def test_compact_schema_compression_keeps_every_local_ref_resolvable() -> None:
    compact = await create_mcp_server(_container()).list_tools()

    for tool in compact:
        definitions = tool.inputSchema.get("$defs", {})
        assert _local_definition_refs(tool.inputSchema) <= set(definitions), tool.name
    a_share = next(tool for tool in compact if tool.name == "a_share_get_facts")
    assert any(name.startswith("S") for name in a_share.inputSchema["$defs"])


@pytest.mark.asyncio
async def test_compact_annotations_distinguish_reads_sync_appends_and_destructive_manage() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}

    assert tools["account_get"].annotations.model_dump() == {
        "title": None,
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert tools["external_state_sync"].annotations.readOnlyHint is False
    assert tools["external_state_sync"].annotations.openWorldHint is True
    assert tools["instrument_resolve"].annotations.destructiveHint is False
    assert tools["research_memory_append"].annotations.destructiveHint is False
    assert tools["investment_case_manage"].annotations.destructiveHint is True
    assert tools["research_workflow_run"].annotations.readOnlyHint is False
    assert tools["research_workflow_run"].annotations.idempotentHint is True


@pytest.mark.asyncio
async def test_system_health_discloses_the_active_surface_profile() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope()
    result = await create_mcp_server(container)._tool_manager.call_tool("system_health", {})

    assert result["data"] == {
        "mcp_surface_profile": "compact_28",
        "public_tool_count": 28,
        "surface_schema_version": "compact-v4",
    }


@pytest.mark.asyncio
async def test_durable_account_and_watchlist_reads_cannot_refresh_upstreams() -> None:
    container = _container()
    container.services.portfolio.get_account_positions.return_value = _Envelope()
    container.services.portfolio.get_account_snapshot = AsyncMock(return_value=_Envelope())
    container.services.watchlist.get_items = AsyncMock(return_value=_Envelope())
    manager = create_mcp_server(container)._tool_manager

    account_result = await manager.call_tool("account_get", {})
    watchlist_result = await manager.call_tool(
        "watchlist_get",
        {"request": {"operation": "items"}},
    )

    assert account_result["ok"] is True
    assert watchlist_result["ok"] is True
    container.services.portfolio.get_account_snapshot.assert_not_awaited()
    request = container.services.watchlist.get_items.await_args.args[0]
    assert request.refresh is False


@pytest.mark.asyncio
async def test_external_state_sync_refreshes_accounts_and_watchlist_only_when_selected() -> None:
    container = _container()
    container.services.portfolio.get_account_snapshot = AsyncMock(return_value=_Envelope())
    container.services.watchlist.get_items = AsyncMock(return_value=_Envelope())
    manager = create_mcp_server(container)._tool_manager

    accounts_result = await manager.call_tool(
        "external_state_sync",
        {"request": {"operation": "accounts"}},
    )
    watchlist_result = await manager.call_tool(
        "external_state_sync",
        {"request": {"operation": "watchlist", "view": "items"}},
    )

    assert accounts_result["ok"] is True
    assert watchlist_result["ok"] is True
    container.services.portfolio.get_account_snapshot.assert_awaited_once()
    request = container.services.watchlist.get_items.await_args.args[0]
    assert request.refresh is True
