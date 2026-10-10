from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from application.ports.capability_usage import CapabilityUsageRecord
from interfaces.capabilities import (
    APPEND,
    READ_DURABLE,
    CapabilityConfirmationRequiredError,
    CapabilityInvocationError,
    CapabilityNotFoundError,
    CompactCapabilityRegistry,
)
from interfaces.capabilities.usage import capability_usage_source
from interfaces.mcp.tools.compact import CompactCapabilityRegistry as MCPRegistry


class Store:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[CapabilityUsageRecord] = []
        self.fail = fail

    def record(self, record: CapabilityUsageRecord) -> None:
        if self.fail:
            raise OSError("synthetic private diagnostic path")
        self.records.append(record)

    def snapshot(self) -> dict[str, Any]:
        return {"rows": [asdict(row) for row in self.records]}


@pytest.mark.asyncio
async def test_canonical_identities_only_and_channel_context_is_restored() -> None:
    store = Store()
    registry = CompactCapabilityRegistry(store, source="CONSOLE")

    async def read(note: str) -> dict[str, str]:
        return {"private": note}

    registry.add_capability(read, name="research_get", policy=READ_DURABLE)
    with capability_usage_source("AGENT"):
        await registry.invoke_validated("research_get", None, {"note": "private-note-body"})
    await registry.invoke_uncompacted("research_get", {"note": "private-account-value"})
    assert [record.source for record in store.records] == ["AGENT", "CONSOLE"]
    assert all(record.operation == "__direct__" for record in store.records)
    assert "private" not in str(store.snapshot())


@pytest.mark.asyncio
async def test_diagnostic_failure_never_retries_or_changes_a_successful_write() -> None:
    store = Store(fail=True)
    registry = CompactCapabilityRegistry(store, source="MCP")
    writes = 0

    async def append() -> dict[str, bool]:
        nonlocal writes
        writes += 1
        return {"ok": True}

    registry.add_capability(append, name="research_memory_append", policy=APPEND)
    assert await registry.invoke_uncompacted(
        "research_memory_append",
        {},
        confirmation="research_memory_append",
    ) == {"ok": True}
    assert writes == 1
    assert registry.usage_recording_failures == 1


@pytest.mark.asyncio
async def test_denial_unknown_name_and_invalid_input_are_closed_outcomes() -> None:
    store = Store()
    registry = CompactCapabilityRegistry(store, source="MCP")

    async def append(value: int) -> dict[str, bool]:
        return {"ok": True}

    registry.add_capability(append, name="research_memory_append", policy=APPEND)
    for name, arguments, confirmation, error_type in [
        ("private-unknown-name", {}, None, CapabilityNotFoundError),
        ("research_memory_append", {"value": 1}, None, CapabilityConfirmationRequiredError),
        (
            "research_memory_append",
            {"value": "private-bad-value"},
            "research_memory_append",
            CapabilityInvocationError,
        ),
    ]:
        with pytest.raises(error_type):
            await registry.invoke_uncompacted(name, arguments, confirmation=confirmation)
    assert [row.outcome for row in store.records] == [
        "INVALID_REQUEST",
        "DENIED",
        "INVALID_REQUEST",
    ]
    assert store.records[0].capability == "__unknown__"
    assert "private" not in str(store.snapshot())


@pytest.mark.asyncio
async def test_mcp_direct_and_gateway_invocations_each_count_once() -> None:
    store = Store()
    neutral = CompactCapabilityRegistry(store, source="MCP")

    async def health() -> dict[str, bool]:
        return {"ok": True}

    neutral.add_capability(health, name="system_health", policy=READ_DURABLE)
    adapter = MCPRegistry.from_registry(neutral)
    server = FastMCP("synthetic")
    adapter.bind_mcp(server)
    await server._tool_manager.call_tool("system_health", {})
    await adapter.invoke("system_health", {})
    assert len(store.records) == 2
    assert all(row.source == "MCP" and row.outcome == "OK" for row in store.records)


@pytest.mark.asyncio
async def test_cancellation_and_error_envelopes_are_not_success() -> None:
    store = Store()
    registry = CompactCapabilityRegistry(store)

    async def cancelled() -> dict[str, bool]:
        raise asyncio.CancelledError()

    async def failed() -> dict[str, Any]:
        return {"ok": False, "errors": [{"code": "PRIVATE_UPSTREAM_VALUE"}]}

    registry.add_capability(cancelled, name="cancelled", policy=READ_DURABLE)
    registry.add_capability(failed, name="failed", policy=READ_DURABLE)
    with pytest.raises(asyncio.CancelledError):
        await registry.invoke_uncompacted("cancelled", {})
    await registry.invoke_uncompacted("failed", {})
    assert [row.outcome for row in store.records] == ["CANCELLED", "TOOL_FAILURE"]
    assert "PRIVATE_UPSTREAM" not in str(store.snapshot())
