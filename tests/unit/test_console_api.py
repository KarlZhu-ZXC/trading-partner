from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine

import interfaces.console.api as console_api
from application.services.review_item_service import ReviewItemService
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository
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


def test_console_bff_canonicalizes_legacy_subject_transport_fields() -> None:
    assert console_api._canonical_subject_transport(
        {
            "data": {
                "case_id": "case_001",
                "case_type": "company",
                "linked_case_ids": ["case_002"],
            }
        }
    ) == {
        "data": {
            "subject_id": "case_001",
            "subject_type": "company",
            "linked_subject_ids": ["case_002"],
        }
    }


def test_workflow_attention_projects_overdue_agenda_and_open_retro_only() -> None:
    items = console_api._workflow_attention_items(
        agenda={
            "data": {
                "items": [
                    {
                        "agenda_item_id": "agenda_overdue",
                        "title": "Earnings",
                        "limitation_codes": ["AGENDA_OUTCOME_UNVERIFIED"],
                    },
                    {
                        "agenda_item_id": "agenda_current",
                        "title": "Investor day",
                        "limitation_codes": [],
                    },
                ]
            }
        },
        retro={
            "data": {
                "runs": [
                    {
                        "run_id": "retro_unreviewed",
                        "findings": [{"code": "MISSING_PLAN"}],
                        "latest_review": None,
                    },
                    {
                        "run_id": "retro_resolved",
                        "findings": [],
                        "latest_review": {"status": "RESOLVED", "action_items": []},
                    },
                ]
            }
        },
    )

    assert [item["key"] for item in items] == [
        "agenda-overdue-agenda_overdue",
        "retro-review-retro_unreviewed-global",
    ]
    assert items[0]["recommended_action"] == "LINK_OUTCOME_OR_REVISE"
    assert items[1]["source_type"] == "TRADE_RETRO"


def test_workflow_attention_projects_each_open_retro_action_as_stable_item() -> None:
    items = console_api._workflow_attention_items(
        agenda={},
        retro={
            "data": {
                "runs": [
                    {
                        "run_id": "retro_1",
                        "findings": [],
                        "latest_review": {
                            "status": "ACCEPTED",
                            "action_items": [
                                "  Record   the next decision before execution.  ",
                                "Record the next decision before execution.",
                            ],
                        },
                    }
                ]
            }
        },
    )

    assert len(items) == 1
    assert items[0]["key"].startswith("retro-action-retro_1-")
    assert items[0]["detail"] == "Record the next decision before execution."
    assert items[0]["recommended_action"] == "COMPLETE_RETRO_ACTION"


def test_workflow_attention_scopes_multi_subject_retro_without_key_collision() -> None:
    items = console_api._workflow_attention_items(
        agenda={},
        retro={
            "data": {
                "runs": [
                    {
                        "run_id": "retro_1",
                        "subject_ids": ["case_1", "case_2", "case_1"],
                        "findings": [],
                        "latest_review": None,
                    }
                ]
            }
        },
    )

    assert {item["key"] for item in items} == {
        "retro-review-retro_1-case_1",
        "retro-review-retro_1-case_2",
    }
    assert {item["subject_id"] for item in items} == {"case_1", "case_2"}
    assert console_api._authoritative_review_refs(
        retro={"data": {"runs": [{"run_id": "retro_1"}]}}
    ) == frozenset({(ReviewItemSourceType.TRADE_RETRO, "retro_1")})


def test_operational_attention_never_presents_unknown_actions_as_retryable() -> None:
    items = console_api._operational_attention_items(
        agent_actions=(
            SimpleNamespace(
                action_id="agent_action_1",
                status=SimpleNamespace(value="UNKNOWN"),
                capability="watchlist_manage",
                operation="add",
            ),
        ),
        broker_intents=(
            SimpleNamespace(
                order_intent_id="order_intent_1",
                status="CANCEL_REQUESTED",
                symbol="AAPL",
                instrument_id="equity:US:AAPL",
                provider_status="CANCEL_REQUEST_ACCEPTED",
            ),
        ),
    )

    assert [item["key"] for item in items] == [
        "agent-unresolved-agent_action_1",
        "broker-unresolved-order_intent_1",
    ]
    assert "will not retry automatically" in items[0]["detail"]
    assert items[1]["recommended_action"] == "RECONCILE_BROKER_ORDER"

    submitting = console_api._operational_attention_items(
        broker_intents=(
            SimpleNamespace(
                order_intent_id="order_intent_2",
                status="SUBMITTING",
                symbol="SGOV",
                instrument_id="etf:US:SGOV",
                provider_status=None,
            ),
        )
    )[0]
    assert submitting["severity"] == "ERROR"
    assert "do not retry" in submitting["detail"]
    assert "Cancellation" not in submitting["detail"]


def test_scorecard_attention_marks_consecutive_gap_as_persistent() -> None:
    items = console_api._scorecard_attention_items(
        {
            "ok": True,
            "data": {
                "runs": [
                    {
                        "scorecard_id": "scorecard_2",
                        "subject_id": "case_1",
                        "thesis_id": "thesis_1",
                        "thesis_revision_no": 2,
                        "generated_at": "2026-08-13T09:00:00+00:00",
                        "dimensions": [
                            {
                                "code": "EVIDENCE_RECENCY",
                                "status": "NOT_EVALUATED",
                                "result_code": "NO_EXACT_REVISION_EVIDENCE",
                                "title": "Evidence recency",
                                "summary": "No exact-revision evidence is available.",
                            }
                        ],
                    },
                    {
                        "scorecard_id": "scorecard_1",
                        "subject_id": "case_1",
                        "thesis_id": "thesis_1",
                        "thesis_revision_no": 1,
                        "generated_at": "2026-08-12T09:00:00+00:00",
                        "dimensions": [
                            {
                                "code": "EVIDENCE_RECENCY",
                                "status": "NOT_EVALUATED",
                                "result_code": "NO_EXACT_REVISION_EVIDENCE",
                                "title": "Evidence recency",
                                "summary": "No exact-revision evidence is available.",
                            }
                        ],
                    },
                ]
            },
        }
    )

    assert len(items) == 1
    assert items[0]["key"] == "scorecard-gap-thesis_1-EVIDENCE_RECENCY"
    assert items[0]["title"].startswith("Persistent Scorecard gap")
    assert items[0]["subject_id"] == "case_1"
    assert items[0]["source_ref"] == "thesis_1"
    assert items[0]["href"].endswith("#scorecard-scorecard_2")


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


class _AgendaClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _AgendaSyncService:
    def __init__(self, sync_result: Any) -> None:
        self.sync_result = sync_result
        self.synced_inputs: list[Any] = []

    async def sync(self, request: Any) -> Any:
        self.synced_inputs.append(request)
        return self.sync_result


class _AgendaSummaryService:
    def __init__(self, *, preview: Any, receipt: Any) -> None:
        self.preview = preview
        self.receipt = receipt
        self.preview_windows: list[int] = []
        self.enqueue_windows: list[int] = []

    def preview_daily(self, *, window_days: int) -> Any:
        self.preview_windows.append(window_days)
        return self.preview

    async def enqueue_daily(self, *, window_days: int) -> Any:
        self.enqueue_windows.append(window_days)
        return self.receipt


class _AgendaContainer:
    def __init__(
        self,
        *,
        sync_service: _AgendaSyncService | None = None,
        summary_service: _AgendaSummaryService | None = None,
        now: datetime | None = None,
    ) -> None:
        self.services = SimpleNamespace(
            review_items=SimpleNamespace(
                reconcile=lambda *_args, **_kwargs: (),
                list_open=lambda **_kwargs: (),
                list_recent=lambda **_kwargs: (),
                metrics=lambda **_kwargs: SimpleNamespace(
                    model_dump=lambda **_dump_kwargs: {
                        "total_items": 0,
                        "open_count": 0,
                        "acknowledged_count": 0,
                    }
                ),
            )
        )
        self.operations = SimpleNamespace(
            catalyst_agenda_sync=sync_service,
            catalyst_agenda_notifications=summary_service,
        )
        self.context = SimpleNamespace(
            clock=_AgendaClock(now or datetime.now(UTC)),
            secret_redactor=SimpleNamespace(redact_text=lambda value: value),
        )

    async def aclose(self) -> None:
        return None


async def _console_headers(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get(
        "/api/session",
        headers={"Origin": "http://127.0.0.1:3000"},
    )
    assert response.status_code == 200
    return {
        "Origin": "http://127.0.0.1:3000",
        "X-Trading-Partner-Console-Token": response.json()["token"],
    }


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
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        console_headers = await _console_headers(client)
        catalog = await client.get("/api/capabilities")
        health = await client.get("/api/health")
        read = await client.post(
            "/api/tools/invoke",
            json={"tool_name": "system_health", "arguments": {}},
            headers=console_headers,
        )
        resolved = await client.post(
            "/api/tools/invoke",
            json={
                "tool_name": "instrument_resolve",
                "arguments": {"market": "US", "query": "TTWO"},
            },
            headers=console_headers,
        )
        rejected = await client.post(
            "/api/tools/invoke",
            json={
                "tool_name": "external_state_sync",
                "arguments": {"request": {"operation": "accounts"}},
            },
            headers=console_headers,
        )
        accepted = await client.post(
            "/api/tools/invoke",
            json={
                "tool_name": "external_state_sync",
                "arguments": {"request": {"operation": "accounts"}},
                "confirmation": "external_state_sync",
            },
            headers=console_headers,
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
async def test_console_rejects_untrusted_host_origin_and_missing_session_token(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []

    async def system_health() -> dict[str, Any]:
        calls.append("health")
        return {"ok": True}

    registry = CompactCapabilityRegistry()
    registry.add_capability(
        system_health,
        name="system_health",
        description="health",
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
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        bad_host = await client.get("http://evil.test/api/health")
        bad_origin = await client.get(
            "/api/session",
            headers={"Origin": "http://127.0.0.1:4444"},
        )
        missing_token = await client.post(
            "/api/tools/invoke",
            json={"tool_name": "system_health", "arguments": {}},
            headers={"Origin": "http://127.0.0.1:3000"},
        )
        headers = await _console_headers(client)
        accepted = await client.post(
            "/api/tools/invoke",
            json={"tool_name": "system_health", "arguments": {}},
            headers=headers,
        )

    assert bad_host.status_code == 400
    assert bad_origin.status_code == 403
    assert missing_token.status_code == 403
    assert accepted.status_code == 200
    assert calls == ["health"]


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
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get("/api/watchlist")

    assert response.status_code == 200
    assert calls == [
        {"operation": "groups"},
        {"operation": "items", "limit": 500, "group_name": "All"},
    ]
    assert response.json()["groups"]["data"]["groups"][1]["source_group_key"] == "All"
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
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json()["data"] == {"accounts": []}
    assert calls == [{"operation": "positions"}]


@pytest.mark.asyncio
async def test_portfolio_console_aggregates_durable_compact_reads_without_sync(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_invoke(
        _request: Any,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        _ = confirmation
        request = arguments["request"]
        calls.append((tool_name, request))
        if tool_name == "watchlist_get" and request["operation"] == "groups":
            return {
                "ok": True,
                "data": {
                    "source": "MOOMOO",
                    "groups": [
                        {
                            "name": "All",
                            "source_group_key": "All",
                            "group_type": "SYSTEM",
                            "active": True,
                        }
                    ],
                },
            }
        return {"ok": True, "data": {"tool": tool_name, **request}}

    monkeypatch.setattr(console_api, "_invoke_capability", fake_invoke)
    monkeypatch.setattr(console_api, "build_default_application", _Container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get("/api/portfolio?transaction_limit=17&coverage_limit=23")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "accounts",
        "transactions",
        "exposure",
        "coverage",
        "risk_policy",
        "risk_check",
    }
    assert calls == [
        ("account_get", {"operation": "positions"}),
        ("account_get", {"operation": "transactions", "limit": 17}),
        ("portfolio_analyze", {"operation": "exposure"}),
        ("portfolio_analyze", {"operation": "coverage", "limit": 23}),
        ("portfolio_risk_get", {"operation": "policy"}),
        ("portfolio_risk_get", {"operation": "check"}),
    ]
    assert all(tool != "watchlist_get" for tool, _request in calls)
    assert all(tool != "external_state_sync" for tool, _request in calls)


@pytest.mark.asyncio
async def test_research_console_pages_all_subjects_and_keeps_partial_state_failure(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    first_page = [{"case_id": "case_001", "title": "First", "status": "active"} for _ in range(200)]
    first_page[0] = {"case_id": "case_001", "title": "First", "status": "active"}
    second_page = [{"case_id": "case_201", "title": "Archived", "status": "archived"}]

    async def fake_invoke(
        _request: Any,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        _ = confirmation
        calls.append((tool_name, arguments))
        if tool_name == "investment_case_read":
            offset = arguments["request"]["offset"]
            items = first_page if offset == 0 else second_page
            return {"ok": True, "data": {"items": items, "total": len(items)}}
        case_id = arguments["request"]["case_id"]
        if case_id == "case_001":
            return {
                "ok": False,
                "data": None,
                "warnings": [],
                "errors": [{"code": "CASE_STATE_FAILED", "message": "fixture failure"}],
                "degraded": True,
            }
        return {
            "ok": True,
            "data": {
                "theses": [],
                "latest_revisions": [],
            },
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(console_api, "_invoke_capability", fake_invoke)
    monkeypatch.setattr(console_api, "build_default_application", _Container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get("/api/research")

    assert response.status_code == 200
    payload = response.json()
    assert payload["subject_list"]["page_size"] == 200
    assert payload["subject_list"]["total"] == 201
    assert len(payload["subjects"]) == 201
    assert payload["subjects"][0]["subject"]["subject_id"] == "case_001"
    assert payload["subjects"][0]["state"]["errors"][0]["code"] == "CASE_STATE_FAILED"
    assert payload["subjects"][-1]["state"]["ok"] is True

    list_calls = [arguments for name, arguments in calls if name == "investment_case_read"]
    assert [call["request"] for call in list_calls] == [
        {"operation": "query", "include_archived": True, "limit": 200, "offset": 0},
        {"operation": "query", "include_archived": True, "limit": 200, "offset": 200},
    ]
    state_calls = [arguments for name, arguments in calls if name == "research_judgment_get"]
    assert state_calls[0]["request"] == {
        "operation": "state",
        "case_id": "case_001",
        "include_archived_theses": True,
        "include_watchlist": True,
    }
    assert state_calls[-1]["request"]["case_id"] == "case_201"


@pytest.mark.asyncio
async def test_scorecard_console_reads_subjects_and_history_through_compact_capabilities(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_invoke(
        _request: Any,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        assert confirmation is None
        compact_request = arguments["request"]
        calls.append((tool_name, compact_request))
        if tool_name == "investment_case_read":
            return {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "case_id": "case_001",
                            "case_type": "company",
                            "title": "TTWO",
                        }
                    ]
                },
            }
        if compact_request["operation"] == "state":
            return {
                "ok": True,
                "data": {"theses": [{"thesis_id": "thesis_001", "title": "Base"}]},
            }
        return {
            "ok": True,
            "data": {
                "items": [
                    {
                        "scorecard_id": "scorecard_001",
                        "case_id": "case_001",
                        "thesis_id": "thesis_001",
                    }
                ],
                "total": 1,
            },
        }

    monkeypatch.setattr(console_api, "_invoke_capability", fake_invoke)
    monkeypatch.setattr(console_api, "build_default_application", _Container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get(
            "/api/scorecards?subject_id=case_001&thesis_id=thesis_001&limit=12&offset=3"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subjects"][0]["subject"]["subject_id"] == "case_001"
    assert payload["scorecards"]["data"]["items"][0]["subject_id"] == "case_001"
    assert calls == [
        (
            "investment_case_read",
            {"operation": "query", "include_archived": True, "limit": 200, "offset": 0},
        ),
        (
            "research_judgment_get",
            {
                "operation": "state",
                "case_id": "case_001",
                "include_archived_theses": True,
                "include_watchlist": False,
            },
        ),
        (
            "research_judgment_get",
            {
                "operation": "scorecard_history",
                "limit": 12,
                "offset": 3,
                "case_id": "case_001",
                "thesis_id": "thesis_001",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_decision_workbench_loads_one_subject_and_preserves_partial_failures(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_invoke(
        _request: Any,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        assert confirmation is None
        compact_request = arguments["request"]
        calls.append((tool_name, compact_request))
        if tool_name == "investment_case_read":
            return {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "case_id": "case_001",
                            "case_type": "company",
                            "title": "TTWO",
                            "status": "ACTIVE",
                        },
                        {
                            "case_id": "case_002",
                            "case_type": "theme",
                            "title": "AI infrastructure",
                            "status": "DRAFT",
                        },
                    ]
                },
            }
        if tool_name == "monitor_read":
            raise RuntimeError("monitor dashboard unavailable")
        if tool_name == "research_judgment_get" and compact_request["operation"] == "state":
            return {
                "ok": True,
                "data": {
                    "theses": [{"thesis_id": "thesis_001"}],
                    "current_trade_plan": {
                        "plan_id": "plan_001",
                        "instrument_id": "equity:US:TTWO",
                    },
                },
            }
        if tool_name == "research_judgment_get":
            return {"ok": True, "data": {"runs": []}}
        if tool_name == "research_memory_get":
            return {"ok": True, "data": {"items": []}}
        assert tool_name == "portfolio_analyze"
        return {
            "ok": True,
            "data": {
                "runs": [
                    {
                        "run_id": "retro_001",
                        "subject_ids": ["case_001"],
                        "findings": [{"plan_id": "plan_001"}],
                        "latest_review": {
                            "status": "OPEN",
                            "action_items": ["Check the next entry against the plan."],
                        },
                    }
                ]
            },
        }

    container = _AgendaContainer()
    reconciled: list[ReviewItemProjection] = []
    container.services.review_items.reconcile = lambda projections, **_kwargs: reconciled.extend(
        projections
    )
    monkeypatch.setattr(console_api, "_invoke_capability", fake_invoke)
    monkeypatch.setattr(console_api, "build_default_application", lambda: container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get("/api/decision-workbench")

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_subject_id"] == "case_001"
    assert payload["subjects"][0]["subject"]["subject_id"] == "case_001"
    assert payload["subjects"][0]["state"]["data"]["theses"][0]["thesis_id"] == "thesis_001"
    assert payload["subjects"][1]["state"] is None
    assert payload["monitors"]["ok"] is False
    assert payload["partial_failures"] == ["monitors"]
    assert (
        calls.count(
            (
                "research_judgment_get",
                {
                    "operation": "state",
                    "case_id": "case_001",
                    "include_archived_theses": True,
                    "include_watchlist": False,
                },
            )
        )
        == 1
    )
    assert (
        "research_memory_get",
        {
            "operation": "agenda",
            "window_days": 90,
            "include_history": False,
            "limit": 100,
            "offset": 0,
            "filters": {"case_ids": ["case_001"]},
        },
    ) in calls
    assert all(name != "external_state_sync" for name, _request in calls)
    assert reconciled
    assert all(item.subject_id == "case_001" for item in reconciled)
    assert all(item.href.startswith("/retro#retro-retro_001") for item in reconciled)


@pytest.mark.asyncio
async def test_review_item_console_transition_requires_session_and_expected_version(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'review-items.db'}")
    Base.metadata.create_all(engine)
    container = _AgendaContainer(now=datetime(2026, 8, 13, 9, tzinfo=UTC))
    container.context.id_generator = SimpleNamespace(new=lambda _prefix: "review_item_1")
    review_service = ReviewItemService(
        SqlAlchemyReviewItemRepository(engine),
        container.context.clock,
        container.context.id_generator,
    )
    container.services.review_items = review_service
    created = review_service.reconcile(
        (
            ReviewItemProjection(
                source_key="scorecard-gap-thesis_1-EVIDENCE_RECENCY",
                source_type=ReviewItemSourceType.SCORECARD_GAP,
                source_ref="scorecard_1",
                subject_id="case_1",
                title="Persistent Scorecard gap · Evidence recency",
                detail="No exact-revision evidence is available.",
                severity=ReviewItemSeverity.ATTENTION,
                recommended_action="REVIEW_SCORECARD_GAP",
                href="/scorecards?subject_id=case_1",
            ),
        ),
        observed_source_types=frozenset({ReviewItemSourceType.SCORECARD_GAP}),
    )[0]
    monkeypatch.setattr(console_api, "build_default_application", lambda: container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)
    body = {
        "status": "RESOLVED",
        "expected_version": created.version,
        "resolution_note": "Added exact-revision evidence and reran the Scorecard.",
        "resolution_ref": "scorecard_2",
        "idempotency_key": "console-review-resolve-1",
        "authorization_note": "User explicitly resolved this ReviewItem in Console.",
        "confirmation": "review_item_update",
    }

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        denied = await client.post(
            f"/api/review-items/{created.review_item_id}/transition", json=body
        )
        headers = await _console_headers(client)
        accepted = await client.post(
            f"/api/review-items/{created.review_item_id}/transition",
            json=body,
            headers=headers,
        )
        stale = await client.post(
            f"/api/review-items/{created.review_item_id}/transition",
            json={**body, "idempotency_key": "console-review-resolve-stale"},
            headers=headers,
        )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["item"]["status"] == "RESOLVED"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "REVIEW_ITEM_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_retro_console_uses_the_canonical_completed_week_window(
    monkeypatch: Any,
) -> None:
    async def fake_invoke(
        _request: Any,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        assert tool_name == "portfolio_analyze"
        assert arguments == {"request": {"operation": "retro_history", "limit": 50}}
        assert confirmation is None
        return {"ok": True, "data": {"runs": []}}

    container = _AgendaContainer(now=datetime(2026, 8, 9, 8, tzinfo=UTC))
    monkeypatch.setattr(console_api, "_invoke_capability", fake_invoke)
    monkeypatch.setattr(console_api, "build_default_application", lambda: container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get("/api/retro")

    assert response.status_code == 200
    assert response.json()["console_windows"] == {
        "previous": {
            "start": "2026-08-03T00:00:00Z",
            "end": "2026-08-08T00:00:00Z",
        },
        "next": {
            "start": "2026-08-10T00:00:00Z",
            "end": "2026-08-15T00:00:00Z",
        },
    }


@pytest.mark.asyncio
async def test_agenda_console_reads_durable_items_and_subject_choices_without_sync(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_invoke(
        _request: Any,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        assert confirmation is None
        calls.append((tool_name, arguments["request"]))
        if tool_name == "research_memory_get":
            if arguments["request"]["operation"] == "timeline":
                return {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "entity_type": "event",
                                "entity_id": "event_001",
                                "subject_id": "case_001",
                            }
                        ]
                    },
                }
            return {
                "ok": True,
                "data": {
                    "items": [{"agenda_item_id": "agenda_001", "subject_id": "case_001"}],
                    "coverage": [],
                },
            }
        return {
            "ok": True,
            "data": {"items": [{"case_id": "case_001", "title": "TTWO"}]},
        }

    monkeypatch.setattr(console_api, "_invoke_capability", fake_invoke)
    monkeypatch.setattr(console_api, "build_default_application", _Container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        response = await client.get(
            "/api/agenda?window_days=14&subject_id=case_001&scope=SUBJECT&kind=EARNINGS"
            "&status=UPCOMING&limit=25"
        )
        candidates_response = await client.get(
            "/api/agenda/outcome-candidates?subject_id=case_001&limit=25"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agenda"]["data"]["items"][0]["subject_id"] == "case_001"
    assert payload["subjects"]["data"]["items"][0]["subject_id"] == "case_001"
    assert candidates_response.status_code == 200
    assert candidates_response.json()["candidates"]["data"]["items"][0]["entity_id"] == "event_001"
    assert calls == [
        (
            "research_memory_get",
            {
                "operation": "agenda",
                "window_days": 14,
                "include_history": False,
                "limit": 25,
                "offset": 0,
                "filters": {
                    "case_ids": ["case_001"],
                    "scopes": ["SUBJECT"],
                    "kinds": ["EARNINGS"],
                    "statuses": ["UPCOMING"],
                },
            },
        ),
        (
            "investment_case_read",
            {"operation": "query", "include_archived": False, "limit": 200, "offset": 0},
        ),
        (
            "research_memory_get",
            {
                "operation": "timeline",
                "case_id": "case_001",
                "entity_types": ["event", "report", "evidence"],
                "limit": 25,
            },
        ),
    ]
    assert all(name != "external_state_sync" for name, _request in calls)


@pytest.mark.asyncio
async def test_agenda_console_sync_dispatches_to_catalyst_agenda_sync_service(
    monkeypatch: Any,
) -> None:
    now = datetime(2026, 8, 9, 10, 0, 0, tzinfo=UTC)
    query_result = {"ok": True, "data": {"sync": "ignored"}}
    sync_service = _AgendaSyncService(query_result)
    container = _AgendaContainer(
        sync_service=sync_service,
        now=now,
    )
    monkeypatch.setattr(console_api, "build_default_application", lambda: container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        headers = await _console_headers(client)
        response = await client.post(
            "/api/agenda/sync",
            json={
                "window_days": 14,
                "instrument_ids": ["equity:US:NVDA", "equity:US:MSFT"],
                "fred_release_ids": [501],
                "as_of": "2026-08-09T00:00:00Z",
                "idempotency_key": "agenda-sync-001",
            },
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json() == {"data": query_result}
    synced_input = sync_service.synced_inputs[0]
    assert synced_input.window_days == 14
    assert synced_input.instrument_ids == ("equity:US:NVDA", "equity:US:MSFT")
    assert synced_input.fred_release_ids == (501,)
    assert synced_input.as_of == datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    assert synced_input.idempotency_key == "agenda-sync-001"


@pytest.mark.asyncio
async def test_agenda_summary_preview_returns_deterministic_grouping_and_counts(
    monkeypatch: Any,
) -> None:
    now = datetime(2026, 8, 9, 10, 0, 0, tzinfo=UTC)
    preview = {
        "source_id": "catalyst-agenda:daily:2026-08-09:w7",
        "title": "催化事项 · 未来 7 天",
        "body": "限制：CATALYST_DATA_PARTIAL",
        "generated_at": now,
        "expires_at": datetime(2026, 8, 10, tzinfo=UTC),
        "window_days": 7,
        "upcoming_count": 1,
        "overdue_count": 1,
        "coverage_gap_count": 1,
        "limitation_codes": ("CATALYST_DATA_PARTIAL",),
    }
    summary_service = _AgendaSummaryService(
        preview=preview,
        receipt={"preview": preview, "notification_id": "notification_001"},
    )
    container = _AgendaContainer(summary_service=summary_service, now=now)
    monkeypatch.setattr(console_api, "build_default_application", lambda: container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        headers = await _console_headers(client)
        response = await client.get(
            "/api/agenda/summary-preview?window_days=7",
            headers=headers,
        )

    payload = response.json()["data"]
    assert response.status_code == 200
    assert payload["title"] == "催化事项 · 未来 7 天"
    assert payload["source_id"] == "catalyst-agenda:daily:2026-08-09:w7"
    assert payload["expires_at"] == "2026-08-10T00:00:00+00:00"
    assert payload["upcoming_count"] == 1
    assert payload["overdue_count"] == 1
    assert payload["coverage_gap_count"] == 1
    assert "限制：CATALYST_DATA_PARTIAL" in payload["body"]
    assert summary_service.preview_windows == [7]


@pytest.mark.asyncio
async def test_agenda_summary_send_enqueues_system_notification(
    monkeypatch: Any,
) -> None:
    now = datetime(2026, 8, 9, 10, 0, 0, tzinfo=UTC)
    receipt = {
        "preview": {
            "source_id": "catalyst-agenda:daily:2026-08-09:w3",
            "title": "催化事项 · 未来 3 天",
        },
        "notification_id": "notification_001",
        "status": "PENDING",
    }
    summary_service = _AgendaSummaryService(
        preview=receipt["preview"],
        receipt=receipt,
    )
    container = _AgendaContainer(summary_service=summary_service, now=now)
    monkeypatch.setattr(console_api, "build_default_application", lambda: container)
    monkeypatch.setattr(
        console_api,
        "create_capability_registry",
        lambda _container: CompactCapabilityRegistry(),
    )
    transport = httpx.ASGITransport(app=console_api.app)

    async with (
        console_api._lifespan(console_api.app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        headers = await _console_headers(client)
        response = await client.post(
            "/api/agenda/summary-send",
            json={"window_days": 3},
            headers=headers,
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["notification_id"] == "notification_001"
    assert payload["status"] == "PENDING"
    assert payload["preview"]["source_id"] == "catalyst-agenda:daily:2026-08-09:w3"
    assert summary_service.enqueue_windows == [3]


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
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        console_headers = await _console_headers(client)
        before = await client.get("/api/schwab/oauth")
        started = await client.post(
            "/api/actions/run",
            json={
                "action": "schwab_oauth_renew",
                "confirmation": "schwab_oauth_renew",
            },
            headers=console_headers,
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
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client,
    ):
        console_headers = await _console_headers(client)
        rejected = await client.post(
            "/api/actions/run",
            json={
                "action": "schwab_oauth_renew",
                "confirmation": "schwab_oauth_renew",
            },
            headers=console_headers,
        )
        confirmed = await client.post(
            "/api/actions/run",
            json={
                "action": "schwab_oauth_renew_confirmed",
                "confirmation": "schwab_oauth_renew_confirmed",
            },
            headers=console_headers,
        )
        await asyncio.sleep(0.05)

    assert rejected.status_code == 409
    assert confirmed.status_code == 200
    assert manager.renew_calls == [True]
