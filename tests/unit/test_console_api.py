from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import interfaces.console.api as console_api
from infrastructure.providers.account.schwab_oauth import (
    SchwabOAuthFlowState,
    SchwabOAuthFlowStatus,
)
from interfaces.mcp.tools.compact import (
    CACHE_DISCOVERY,
    READ_DURABLE,
    SYNC,
    CompactCapabilityRegistry,
)


class _Container:
    async def aclose(self) -> None:
        return None


class _OAuthHealth:
    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {
            "state": "EXPIRING",
            "reauthorization_due_at": "2026-08-01T05:23:00Z",
            "warning_codes": ["SCHWAB_OAUTH_REAUTH_DUE_SOON"],
        }


class _OAuthManager:
    def __init__(self, flow: SchwabOAuthFlowStatus | None = None) -> None:
        self.flow = flow or SchwabOAuthFlowStatus(state=SchwabOAuthFlowState.IDLE)
        self.renew_calls: list[bool] = []

    def status(self) -> SchwabOAuthFlowStatus:
        return self.flow

    def token_health(self) -> _OAuthHealth:
        return _OAuthHealth()

    def renew(self, *, confirm_retry_after_failure: bool = False) -> SchwabOAuthFlowStatus:
        self.renew_calls.append(confirm_retry_after_failure)
        self.flow = SchwabOAuthFlowStatus(
            state=SchwabOAuthFlowState.SUCCEEDED,
            flow_id="oauth_test",
            message_code="SCHWAB_OAUTH_REAUTHORIZED",
        )
        return self.flow


class _OAuthContainer:
    def __init__(self, manager: _OAuthManager) -> None:
        self.operations = SimpleNamespace(schwab_oauth=manager)
        self.context = SimpleNamespace(
            secret_redactor=SimpleNamespace(redact_text=lambda value: value)
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_tool_workbench_invokes_reads_and_gates_writes(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def system_health() -> dict[str, Any]:
        calls.append(("system_health", {}))
        return {"ok": True, "tool": "system_health"}

    async def instrument_resolve(market: str, query: str) -> dict[str, Any]:
        calls.append(("instrument_resolve", {"market": market, "query": query}))
        return {"ok": True, "tool": "instrument_resolve"}

    async def external_state_sync(request: dict[str, Any]) -> dict[str, Any]:
        calls.append(("external_state_sync", {"request": request}))
        return {"ok": True, "tool": "external_state_sync"}

    registry = CompactCapabilityRegistry()
    registry.add_capability(
        system_health,
        name="system_health",
        description="health",
        policy=READ_DURABLE,
    )
    registry.add_capability(
        instrument_resolve,
        name="instrument_resolve",
        description="resolve",
        policy=CACHE_DISCOVERY,
    )
    registry.add_capability(
        external_state_sync,
        name="external_state_sync",
        description="sync",
        policy=SYNC,
    )
    monkeypatch.setattr(console_api, "build_default_application", _Container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: registry,
    )

    transport = httpx.ASGITransport(app=console_api.app)
    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        catalog = await client.get("/api/capabilities")
        health = await client.get("/api/health")
        read = await client.post(
            "/api/tools/invoke",
            json={"tool_name": "system_health", "arguments": {}},
        )
        resolved = await client.post(
            "/api/tools/invoke",
            json={
                "tool_name": "instrument_resolve",
                "arguments": {"market": "US", "query": "TTWO"},
            },
        )
        rejected = await client.post(
            "/api/tools/invoke",
            json={
                "tool_name": "external_state_sync",
                "arguments": {"request": {"operation": "accounts"}},
            },
        )
        accepted = await client.post(
            "/api/tools/invoke",
            json={
                "tool_name": "external_state_sync",
                "arguments": {"request": {"operation": "accounts"}},
                "confirmation": "external_state_sync",
            },
        )

    assert catalog.status_code == 200
    sync_item = next(
        item for item in catalog.json()["items"] if item["name"] == "external_state_sync"
    )
    assert sync_item["effect"] == "SYNC"
    assert sync_item["confirmation_required"] is True
    assert health.status_code == 200
    assert read.status_code == 200
    assert resolved.status_code == 200
    assert rejected.status_code == 409
    assert accepted.status_code == 200
    assert calls == [
        ("system_health", {}),
        ("system_health", {}),
        ("instrument_resolve", {"market": "US", "query": "TTWO"}),
        ("external_state_sync", {"request": {"operation": "accounts"}}),
    ]


@pytest.mark.asyncio
async def test_watchlist_console_uses_moomoo_all_group_for_instrument_count(
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def watchlist_get(request: dict[str, Any]) -> dict[str, Any]:
        calls.append(request)
        if request["operation"] == "groups":
            return {
                "ok": True,
                "data": {
                    "source": "MOOMOO",
                    "groups": [
                        {
                            "name": "Favorites",
                            "source_group_key": "Favorites",
                            "group_type": "SYSTEM",
                            "active": True,
                        },
                        {
                            "name": "All",
                            "source_group_key": "All",
                            "group_type": "SYSTEM",
                            "active": True,
                        },
                    ],
                },
            }
        return {"ok": True, "data": {"group": {"name": request.get("group_name")}, "items": []}}

    registry = CompactCapabilityRegistry()
    registry.add_capability(
        watchlist_get,
        name="watchlist_get",
        description="watchlist",
        policy=READ_DURABLE,
    )
    monkeypatch.setattr(console_api, "build_default_application", _Container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: registry,
    )

    transport = httpx.ASGITransport(app=console_api.app)
    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/api/watchlist")

    assert response.status_code == 200
    assert calls == [
        {"operation": "groups"},
        {"operation": "items", "limit": 500, "group_name": "All"},
    ]
    assert response.json()["scope"] == {
        "group_name": "All",
        "all_active_moomoo_items": True,
    }


@pytest.mark.asyncio
async def test_accounts_console_uses_grouped_durable_positions_contract(
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def account_get(request: dict[str, Any]) -> dict[str, Any]:
        calls.append(request)
        return {"ok": True, "data": {"accounts": []}}

    registry = CompactCapabilityRegistry()
    registry.add_capability(
        account_get,
        name="account_get",
        description="accounts",
        policy=READ_DURABLE,
    )
    monkeypatch.setattr(console_api, "build_default_application", _Container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: registry,
    )

    transport = httpx.ASGITransport(app=console_api.app)
    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json()["data"] == {"accounts": []}
    assert calls == [{"operation": "positions"}]


@pytest.mark.asyncio
async def test_console_starts_one_safe_schwab_oauth_flow(monkeypatch: Any) -> None:
    manager = _OAuthManager()
    monkeypatch.setattr(
        console_api,
        "build_default_application",
        lambda: _OAuthContainer(manager),
    )
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        before = await client.get("/api/schwab/oauth")
        started = await client.post(
            "/api/actions/run",
            json={
                "action": "schwab_oauth_renew",
                "confirmation": "schwab_oauth_renew",
            },
        )
        await asyncio.sleep(0.05)
        after = await client.get("/api/schwab/oauth")

    assert before.status_code == 200
    assert before.json()["flow"]["state"] == "IDLE"
    assert before.json()["token_health"]["state"] == "EXPIRING"
    assert started.status_code == 200
    assert manager.renew_calls == [False]
    assert after.json()["flow"]["state"] == "SUCCEEDED"
    serialized = str(after.json()).lower()
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    assert "client_secret" not in serialized
    assert "authorization_url" not in serialized


@pytest.mark.asyncio
async def test_console_requires_confirmation_after_failed_schwab_flow(
    monkeypatch: Any,
) -> None:
    manager = _OAuthManager(
        SchwabOAuthFlowStatus(
            state=SchwabOAuthFlowState.FAILED,
            retry_requires_confirmation=True,
        )
    )
    monkeypatch.setattr(
        console_api,
        "build_default_application",
        lambda: _OAuthContainer(manager),
    )
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/actions/run",
            json={
                "action": "schwab_oauth_renew",
                "confirmation": "schwab_oauth_renew",
            },
        )
        confirmed = await client.post(
            "/api/actions/run",
            json={
                "action": "schwab_oauth_renew_confirmed",
                "confirmation": "schwab_oauth_renew_confirmed",
            },
        )
        await asyncio.sleep(0.05)

    assert rejected.status_code == 409
    assert confirmed.status_code == 200
    assert manager.renew_calls == [True]
