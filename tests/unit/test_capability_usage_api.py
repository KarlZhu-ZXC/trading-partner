from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import interfaces.console.api as console_api
from application.ports.capability_usage import CapabilityUsageRecord
from interfaces.capabilities import READ_DURABLE, CompactCapabilityRegistry


@pytest.mark.asyncio
async def test_usage_export_is_read_only_and_separates_inventory_from_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        records = 0

        def record(self, record: CapabilityUsageRecord) -> None:
            self.records += 1
            raise AssertionError("Export must never record an invocation")

        def snapshot(self) -> dict[str, Any]:
            return {"initialized": False, "rows": [], "completeness": "best_effort"}

    store = Store()
    registry = CompactCapabilityRegistry(store)

    async def health() -> dict[str, bool]:
        raise AssertionError("Export must never call a business capability")

    registry.add_capability(health, name="system_health", policy=READ_DURABLE)
    monkeypatch.setattr(console_api.app.state, "capability_registry", registry, raising=False)
    monkeypatch.setattr(
        console_api.app.state,
        "container",
        SimpleNamespace(
            context=SimpleNamespace(capability_usage=store),
        ),
        raising=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_api.app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.get("/api/capability-usage")
    assert response.status_code == 200
    result = response.json()
    assert result["inventory"] == {"public_tools": 9, "capabilities": 1, "operations": 1}
    assert result["rows"] == []
    assert result["initialized"] is False
    assert result["recording_failures_this_process"] == 0
    assert store.records == 0
    assert any("not evidence" in text for text in result["limitations"])


@pytest.mark.asyncio
async def test_usage_storage_failure_is_unavailable_not_unused_or_private_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> dict[str, Any]:
        raise OSError("/private/synthetic-owner-path")

    monkeypatch.setattr(
        console_api.app.state, "capability_registry", CompactCapabilityRegistry(), raising=False
    )
    monkeypatch.setattr(
        console_api.app.state,
        "container",
        SimpleNamespace(
            context=SimpleNamespace(capability_usage=SimpleNamespace(snapshot=unavailable)),
        ),
        raising=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_api.app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.get("/api/capability-usage")
    assert response.json()["completeness"] == "unavailable"
    assert "private" not in response.text
