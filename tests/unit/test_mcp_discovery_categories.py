"""Category-filtered MCP discovery keeps the full exact surface reachable."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from interfaces.capabilities.catalog import CATEGORY_CAPABILITIES, CapabilityCategory
from interfaces.mcp.progressive import (
    DISCOVERY_TOOL_NAME,
    SHORT_DESCRIPTIONS,
    register_discovery,
)
from interfaces.mcp.tool_inventory import MCP_VNEXT_TOOL_NAMES
from interfaces.mcp.tools.compact import READ_DURABLE, CompactCapabilityRegistry


def _catalog_registry(calls: dict[str, int]) -> CompactCapabilityRegistry:
    registry = CompactCapabilityRegistry()

    async def handler() -> dict[str, str]:
        calls["handler"] += 1
        return {"status": "unexpected"}

    for name, description in SHORT_DESCRIPTIONS.items():
        registry.add_capability(
            handler,
            name=name,
            description=description,
            policy=READ_DURABLE,
        )
    return registry


async def _discover(
    registry: CompactCapabilityRegistry,
    arguments: dict[str, Any],
) -> Any:
    server = FastMCP("discovery-category-test")
    register_discovery(server, registry)
    tool = server._tool_manager.get_tool(DISCOVERY_TOOL_NAME)
    assert tool is not None
    return await tool.run(arguments)


def test_categories_partition_the_complete_capability_catalogue() -> None:
    groups = tuple(CATEGORY_CAPABILITIES.values())
    union = set().union(*groups)

    assert union == set(MCP_VNEXT_TOOL_NAMES)
    assert sum(len(group) for group in groups) == len(MCP_VNEXT_TOOL_NAMES) == 24
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(groups)
        for right in groups[index + 1 :]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("category", tuple(CATEGORY_CAPABILITIES))
async def test_category_filter_preserves_catalogue_order_and_contents(
    category: CapabilityCategory,
) -> None:
    calls = {"handler": 0}
    result = await _discover(_catalog_registry(calls), {"category": category})

    expected = [
        name
        for name in SHORT_DESCRIPTIONS
        if name in CATEGORY_CAPABILITIES[category]
    ]
    assert [item["tool"] for item in result] == expected
    assert calls == {"handler": 0}


@pytest.mark.asyncio
async def test_no_category_keeps_the_full_catalogue_and_direct_discovery() -> None:
    calls = {"handler": 0}
    registry = _catalog_registry(calls)

    catalog = await _discover(registry, {})
    assert [item["tool"] for item in catalog] == list(SHORT_DESCRIPTIONS)

    direct = await _discover(registry, {"tool": "system_health"})
    assert direct["tool"] == "system_health"
    assert direct["operation"] is None
    assert "inputSchema" in direct
    assert calls == {"handler": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    (
        {"category": "unknown-category"},
        {"category": "research", "tool": "research_get"},
        {"category": "research", "operation": "query"},
        {"category": "research", "tool": "research_get", "operation": "query"},
    ),
)
async def test_invalid_or_mixed_category_filters_fail_without_echo_or_invocation(
    arguments: dict[str, Any],
) -> None:
    calls = {"handler": 0}

    with pytest.raises(ToolError) as error:
        await _discover(_catalog_registry(calls), arguments)

    assert "unknown-category" not in str(error.value)
    assert calls == {"handler": 0}
