"""Compact MCP surface inventory, schema, and explicit-sync boundary tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import Draft202012Validator

from interfaces.mcp.server import (
    COMPACT_28_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_capability_registry,
    create_mcp_server,
)
from interfaces.mcp.tools.compact import ConfirmationPolicy


class _Envelope:
    def __init__(
        self,
        data: dict[str, Any] | None = None,
        *,
        degraded: bool = False,
        warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        self._data = data or {}
        self._degraded = degraded
        self._warnings = warnings or []

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {
            "ok": True,
            "request_id": "req_compact",
            "degraded": self._degraded,
            "warnings": self._warnings,
            "data": dict(self._data),
        }


def _container() -> MagicMock:
    container = MagicMock()
    container.settings = SimpleNamespace(mcp_server_name="Trading Partner Test")
    container.services = MagicMock()
    container.services.data_quality.check.return_value = _Envelope()
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
async def test_registry_and_mcp_transport_publish_identical_contracts() -> None:
    container = _container()
    registry_tools = {
        tool.name: tool.model_dump(mode="json")
        for tool in create_capability_registry(container).list_tools()
    }
    mcp_tools = {
        tool.name: tool.model_dump(mode="json")
        for tool in await create_mcp_server(container).list_tools()
    }

    assert registry_tools == mcp_tools


def test_registry_uses_explicit_confirmation_policy_not_read_only_hint() -> None:
    policies = create_capability_registry(_container()).policies

    assert policies["instrument_resolve"].annotations.readOnlyHint is False
    assert policies["instrument_resolve"].confirmation is ConfirmationPolicy.NONE
    assert policies["external_state_sync"].confirmation is ConfirmationPolicy.MATCH_CAPABILITY_NAME


@pytest.mark.asyncio
async def test_registry_and_mcp_transport_invoke_the_same_health_handler() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope()

    registry_result = await create_capability_registry(container).invoke(
        "system_health",
        {},
    )
    mcp_result = await create_mcp_server(container)._tool_manager.call_tool(
        "system_health",
        {},
    )

    assert registry_result == mcp_result


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
        "market_data_get": 7,
        "external_state_sync": 3,
        "portfolio_analyze": 4,
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
    assert (
        sum(len(json.dumps(tool.inputSchema, separators=(",", ":"))) for tool in compact)
        <= 36 * 1024
    )
    for tool in compact:
        assert len(json.dumps(tool.inputSchema, separators=(",", ":"))) <= 8 * 1024, tool.name


@pytest.mark.asyncio
async def test_compact_schema_compression_keeps_every_local_ref_resolvable() -> None:
    compact = await create_mcp_server(_container()).list_tools()

    for tool in compact:
        Draft202012Validator.check_schema(tool.inputSchema)
        definitions = tool.inputSchema.get("$defs", {})
        assert _local_definition_refs(tool.inputSchema) <= set(definitions), tool.name
    a_share = next(tool for tool in compact if tool.name == "a_share_get_facts")
    assert len(a_share.inputSchema["$defs"]) < 36
    assert all(len(name) == 1 for name in a_share.inputSchema["$defs"])


@pytest.mark.asyncio
async def test_compact_public_schema_rejects_fields_from_other_operations() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}
    schema = tools["account_get"].inputSchema
    validator = Draft202012Validator(schema)

    assert not list(validator.iter_errors({"request": {"operation": "positions"}}))
    errors = list(
        validator.iter_errors(
            {"request": {"operation": "positions", "limit": 20}},
        )
    )
    assert errors


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
        "data_quality": {
            "component_checks": {},
            "component_check_limitations": [
                "CONFIGURATION_CHECK_IS_NOT_UPSTREAM_REACHABILITY",
                "ONLY_COMPONENTS_WITH_EXPLICIT_PROBES_ARE_LISTED",
            ],
        },
        "mcp_surface_profile": "compact_28",
        "public_tool_count": 28,
        "surface_schema_version": "compact-v10",
    }


@pytest.mark.asyncio
async def test_system_health_keeps_operational_and_data_quality_states_separate() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope(
        {"status": "ok", "components": {"provider": {"state": "ok"}}}
    )
    container.services.data_quality.check.return_value = _Envelope(
        {"status": "degraded", "issues": [{"code": "MONITOR_NEVER_EVALUATED"}]},
        degraded=True,
        warnings=[
            {
                "code": "DATA_QUALITY_ISSUES",
                "message": "Quality gap",
                "details": {},
            }
        ],
    )

    result = await create_mcp_server(container)._tool_manager.call_tool("system_health", {})

    assert result["degraded"] is False
    assert result["data"]["status"] == "ok"
    assert result["data"]["data_quality"]["status"] == "degraded"
    assert result["data"]["data_quality"]["component_checks"] == {
        "provider": {"state": "ok"}
    }
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_system_health_survives_data_quality_center_failure() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope({"status": "ok"})
    container.services.data_quality.check.side_effect = RuntimeError("token=secret")

    result = await create_mcp_server(container)._tool_manager.call_tool("system_health", {})

    assert result["ok"] is True
    assert result["degraded"] is False
    assert result["data"]["status"] == "ok"
    quality = result["data"]["data_quality"]
    assert quality["status"] == "error"
    assert quality["account_snapshots"] == []
    assert quality["issues"][0]["code"] == "DATA_QUALITY_CENTER_UNAVAILABLE"
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_performance_summary_routes_through_durable_attribution_service() -> None:
    container = _container()
    container.services.account_transactions.get_performance_attribution.return_value = (
        _Envelope()
    )

    result = await create_mcp_server(container)._tool_manager.call_tool(
        "portfolio_analyze",
        {
            "request": {
                "operation": "performance_summary",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-08-01T00:00:00Z",
                "cost_basis_method": "FIFO",
            }
        },
    )

    assert result["ok"] is True
    container.services.account_transactions.get_performance_attribution.assert_called_once()


@pytest.mark.asyncio
async def test_durable_account_and_watchlist_reads_cannot_refresh_upstreams() -> None:
    container = _container()
    container.services.portfolio.get_account_positions.return_value = _Envelope()
    container.services.portfolio.get_account_snapshot = AsyncMock(return_value=_Envelope())
    container.services.watchlist.get_items = AsyncMock(return_value=_Envelope())
    manager = create_mcp_server(container)._tool_manager

    account_result = await manager.call_tool("account_get", {"request": {"operation": "positions"}})
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
async def test_account_transactions_read_is_durable_only() -> None:
    container = _container()
    container.services.account_transactions.list_durable_transactions.return_value = _Envelope()
    container.services.account_transactions.get_transactions = AsyncMock(return_value=_Envelope())

    result = await create_mcp_server(container)._tool_manager.call_tool(
        "account_get",
        {"request": {"operation": "transactions", "limit": 20}},
    )

    assert result["ok"] is True
    container.services.account_transactions.list_durable_transactions.assert_called_once()
    container.services.account_transactions.get_transactions.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_state_sync_refreshes_accounts_and_watchlist_only_when_selected() -> None:
    container = _container()
    container.services.portfolio.get_account_snapshot = AsyncMock(return_value=_Envelope())
    container.services.watchlist.sync_all = AsyncMock(return_value=_Envelope())
    manager = create_mcp_server(container)._tool_manager

    accounts_result = await manager.call_tool(
        "external_state_sync",
        {"request": {"operation": "accounts"}},
    )
    watchlist_result = await manager.call_tool(
        "external_state_sync",
        {"request": {"operation": "watchlist"}},
    )

    assert accounts_result["ok"] is True
    assert watchlist_result["ok"] is True
    container.services.portfolio.get_account_snapshot.assert_awaited_once()
    container.services.watchlist.sync_all.assert_awaited_once_with()
