"""Contracts for the transport-neutral capability registry."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from interfaces.capabilities.policy import APPEND, READ_DURABLE
from interfaces.capabilities.registry import (
    CapabilityConfirmationRequiredError,
    CompactCapabilityRegistry,
)
from interfaces.mcp.tools.compact import CompactCapabilityRegistry as MCPCapabilityRegistry


def test_neutral_capability_modules_have_no_transport_sdk_imports() -> None:
    root = Path(__file__).parents[2] / "src" / "interfaces" / "capabilities"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)
        assert not any(name == "mcp" or name.startswith("mcp.") for name in imported), path


def test_neutral_factory_does_not_import_legacy_compact_adapter() -> None:
    path = Path(__file__).parents[2] / "src" / "interfaces" / "capabilities" / "factory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "interfaces.mcp.tools.compact" not in modules


@pytest.mark.asyncio
async def test_one_pydantic_signature_model_drives_schema_validation_and_invocation() -> None:
    calls: list[dict[str, Any]] = []

    async def read(*, limit: int = 5) -> dict[str, Any]:
        calls.append({"limit": limit})
        return {"items": list(range(limit))}

    registry = CompactCapabilityRegistry()
    registry.add_capability(read, name="read", policy=READ_DURABLE)

    descriptor = registry.find_operation("read")
    assert descriptor.exact_schema["properties"]["limit"]["type"] == "integer"
    registered = registry.registered_capabilities[0]
    assert registered.tool.parameters["properties"]["limit"]["default"] == 5
    assert registry.validate_operation("read", None, {}) == {"limit": 5}
    with pytest.raises(ValidationError):
        registry.validate_operation("read", None, {"limit": "not-an-int"})

    assert await registry.invoke_uncompacted("read", {}) == {"items": [0, 1, 2, 3, 4]}
    assert calls == [{"limit": 5}]


@pytest.mark.asyncio
async def test_neutral_confirmation_policy_is_enforced_before_handler() -> None:
    calls: list[int] = []

    async def append(*, value: int) -> dict[str, int]:
        calls.append(value)
        return {"value": value}

    registry = CompactCapabilityRegistry()
    registry.add_capability(append, name="append", policy=APPEND)

    with pytest.raises(CapabilityConfirmationRequiredError):
        await registry.invoke_uncompacted("append", {"value": 1})
    assert calls == []
    assert await registry.invoke_uncompacted(
        "append", {"value": 2}, confirmation="append"
    ) == {"value": 2}
    assert calls == [2]


@pytest.mark.asyncio
async def test_neutral_uncompacted_path_keeps_full_result() -> None:
    async def large_read() -> dict[str, Any]:
        return {"items": [{"index": index, "detail": "x" * 1000} for index in range(40)]}

    registry = CompactCapabilityRegistry()
    registry.add_capability(large_read, name="large_read", policy=READ_DURABLE)

    result = await registry.invoke_uncompacted("large_read", {})
    assert len(result["items"]) == 40
    assert all("_truncated" not in item for item in result["items"])


def test_mcp_adapter_adopts_the_same_neutral_graph() -> None:
    async def read(*, value: int = 1) -> dict[str, int]:
        return {"value": value}

    neutral = CompactCapabilityRegistry()
    neutral.add_capability(read, name="read", policy=READ_DURABLE)
    adapter = MCPCapabilityRegistry.from_registry(neutral)

    assert adapter._capabilities is neutral._capabilities
    assert adapter._operations is neutral._operations
    assert adapter.find_operation("read") is neutral.find_operation("read")
    assert (
        adapter.find_operation("read").exact_schema
        == neutral.find_operation("read").exact_schema
    )
