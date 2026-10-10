from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import interfaces.console.api as console_api


class _Status:
    def __init__(self, status: str = "IDLE") -> None:
        self.value = status
        self.calls = 0

    def status(self) -> _Status:
        return self

    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        self.calls += 1
        return {"status": self.value}


class _Registry:
    def __init__(self, *, fail_dashboard: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_dashboard = fail_dashboard

    async def invoke_uncompacted(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((name, arguments))
        operation = arguments.get("request", {}).get("operation")
        if self.fail_dashboard and operation == "dashboard":
            raise RuntimeError("provider secret must not escape")
        if name == "system_health":
            data: dict[str, Any] = {"status": "healthy"}
        elif operation == "dashboard":
            data = {"items": []}
        else:
            data = {"runs": []}
        return {"ok": True, "data": data, "warnings": [], "errors": []}


class _Workspace:
    def __init__(self) -> None:
        self.read_calls = 0
        self.collect_calls = 0
        self.refresh_calls = 0
        self.batch = object()
        self.coverage = {
            "agenda": "COMPLETE",
            "retro": "COMPLETE",
            "scorecards": "COMPLETE",
        }

    def read(self) -> dict[str, Any]:
        self.read_calls += 1
        return {
            "workflow_attention": [],
            "review_items": [],
            "review_item_metrics": None,
            "review_item_history": [],
            "review_items_error": None,
            "review_coverage": {
                "sources": dict(self.coverage),
                "complete": True,
                "materialization_required": False,
            },
        }

    def collect(self) -> Any:
        self.collect_calls += 1
        return SimpleNamespace(batch=self.batch, coverage=dict(self.coverage))

    def refresh(self) -> dict[str, Any]:
        self.refresh_calls += 1
        return {
            "updated_count": 2,
            "coverage": dict(self.coverage),
            "complete": True,
            "scan_receipt": {
                "status": "COMPLETE",
                "complete": True,
                "sources": {},
                "persistence": {"status": "SAVED", "receipt_id": "audit_1"},
            },
        }


class _Refresh:
    def __init__(self) -> None:
        self.apply_calls: list[object] = []

    def apply(self, batch: object) -> tuple[str, ...]:
        self.apply_calls.append(batch)
        return ("review-item-1", "review-item-2")


class _ReviewItems:
    def __init__(self) -> None:
        self.list_open_calls = 0
        self.list_recent_calls = 0
        self.metrics_calls = 0
        self.latest_observed_at_calls = 0

    @staticmethod
    def _row() -> SimpleNamespace:
        return SimpleNamespace(
            model_dump=lambda *, mode: {
                "review_item_id": "review-item-1",
                "status": "OPEN",
                "mode": mode,
            }
        )

    def list_open(self, *, subject_id: str | None = None, limit: int = 100) -> tuple[Any, ...]:
        assert subject_id is None
        assert limit == 100
        self.list_open_calls += 1
        return (self._row(),)

    def list_recent(self, *, subject_id: str | None = None, limit: int = 100) -> tuple[Any, ...]:
        assert subject_id is None
        assert limit == 100
        self.list_recent_calls += 1
        return (self._row(),)

    def metrics(self, *, subject_id: str | None = None) -> Any:
        assert subject_id is None
        self.metrics_calls += 1
        return SimpleNamespace(
            total_items=1,
            open_count=1,
            acknowledged_count=0,
            model_dump=lambda *, mode: {
                "total_items": 1,
                "open_count": 1,
                "acknowledged_count": 0,
                "mode": mode,
            },
        )

    def latest_observed_at(self) -> datetime:
        self.latest_observed_at_calls += 1
        return datetime(2026, 9, 22, tzinfo=UTC)


class _Forbidden:
    def __init__(self) -> None:
        self.provider_calls = 0
        self.model_calls = 0
        self.confirm_calls = 0

    def provider(self, *_args: Any, **_kwargs: Any) -> None:
        self.provider_calls += 1
        raise AssertionError("provider must not be invoked by a read route")

    def model(self, *_args: Any, **_kwargs: Any) -> None:
        self.model_calls += 1
        raise AssertionError("model must not be invoked by a read route")

    def confirm(self, *_args: Any, **_kwargs: Any) -> None:
        self.confirm_calls += 1
        raise AssertionError("confirmation must not be invoked by a read route")


def _install_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_dashboard: bool = False,
) -> tuple[_Registry, _Workspace, _Refresh, _ReviewItems, _Forbidden]:
    registry = _Registry(fail_dashboard=fail_dashboard)
    workspace = _Workspace()
    refresh = _Refresh()
    review_items = _ReviewItems()
    forbidden = _Forbidden()
    services = SimpleNamespace(
        review_workspace_query=workspace,
        review_item_refresh=refresh,
        review_items=review_items,
    )
    statuses = SimpleNamespace(
        post_market_sync=_Status(),
        notifications=_Status(),
        maintenance=_Status(),
    )
    container = SimpleNamespace(
        services=services,
        operations=statuses,
        context=SimpleNamespace(
            clock=SimpleNamespace(now=lambda: datetime(2026, 9, 22, tzinfo=UTC))
        ),
    )
    monkeypatch.setattr(console_api.app.state, "container", container, raising=False)
    monkeypatch.setattr(console_api.app.state, "capability_registry", registry, raising=False)
    monkeypatch.setattr(
        console_api.app.state,
        "console_session_token",
        "synthetic-session-token",
        raising=False,
    )
    return registry, workspace, refresh, review_items, forbidden


@pytest.mark.asyncio
async def test_overview_reads_only_durable_sources_and_keeps_full_monitor_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, workspace, refresh, review_items, forbidden = _install_state(monkeypatch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_api.app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.get("/api/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["capability_count"] == 9
    assert body["business_capability_count"] == 24
    assert body["health"]["ok"] is True
    assert body["maintenance"] == {"status": "IDLE"}
    assert body["monitor_dashboard"]["data"] == {"items": []}
    assert body["recent_runs"]["data"] == {"runs": []}
    assert [name for name, _ in registry.calls] == [
        "system_health",
        "monitor_read",
        "monitor_read",
    ]
    assert registry.calls[1][1] == {
        "request": {"operation": "dashboard", "status": None}
    }
    assert registry.calls[2][1] == {"request": {"operation": "runs", "limit": 20}}
    assert workspace.read_calls == 1
    assert workspace.collect_calls == 0
    assert workspace.refresh_calls == 0
    assert refresh.apply_calls == []
    assert review_items.list_open_calls == 0
    assert review_items.list_recent_calls == 0
    assert forbidden.provider_calls == forbidden.model_calls == forbidden.confirm_calls == 0


@pytest.mark.asyncio
async def test_overview_preserves_independent_capability_failure_without_secret_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, workspace, refresh, review_items, forbidden = _install_state(
        monkeypatch,
        fail_dashboard=True,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_api.app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.get("/api/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["health"]["ok"] is True
    assert body["maintenance"] == {"status": "IDLE"}
    assert body["recent_runs"]["ok"] is True
    assert body["monitor_dashboard"] == {
        "ok": False,
        "data": None,
        "warnings": [],
        "errors": [
            {"code": "CONSOLE_DURABLE_CALL_FAILED", "message": "Durable source unavailable"}
        ],
        "degraded": True,
    }
    assert "provider secret must not escape" not in response.text
    assert workspace.collect_calls == 0
    assert refresh.apply_calls == []
    assert review_items.list_open_calls == 0
    assert review_items.list_recent_calls == 0
    assert forbidden.provider_calls == forbidden.model_calls == forbidden.confirm_calls == 0


@pytest.mark.asyncio
async def test_review_items_get_is_read_only_for_open_and_resolved_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, refresh, review_items, forbidden = _install_state(monkeypatch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_api.app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        open_response = await client.get("/api/review-items")
        resolved_response = await client.get("/api/review-items?include_resolved=true")

    assert open_response.status_code == resolved_response.status_code == 200
    assert open_response.json()["items"][0]["review_item_id"] == "review-item-1"
    assert resolved_response.json()["items"][0]["review_item_id"] == "review-item-1"
    assert review_items.list_open_calls == 1
    assert review_items.list_recent_calls == 1
    assert review_items.metrics_calls == 2
    assert review_items.latest_observed_at_calls == 2
    assert workspace.read_calls == workspace.collect_calls == 0
    assert refresh.apply_calls == []
    assert forbidden.provider_calls == forbidden.model_calls == forbidden.confirm_calls == 0


@pytest.mark.asyncio
async def test_review_items_refresh_requires_exact_session_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, refresh, _, _ = _install_state(monkeypatch)
    headers = {"X-Trading-Partner-Console-Token": "synthetic-session-token"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_api.app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        missing_token = await client.post(
            "/api/review-items/refresh",
            json={"confirmation": "refresh_review_items"},
        )
        wrong_confirmation = await client.post(
            "/api/review-items/refresh",
            json={"confirmation": "refresh"},
            headers=headers,
        )
        unknown_field = await client.post(
            "/api/review-items/refresh",
            json={"confirmation": "refresh_review_items", "secret": "should-reject"},
            headers=headers,
        )

    assert missing_token.status_code == 403
    assert wrong_confirmation.status_code == 422
    assert unknown_field.status_code == 422
    assert "should-reject" not in unknown_field.text
    assert workspace.collect_calls == 0
    assert refresh.apply_calls == []


@pytest.mark.asyncio
async def test_review_items_refresh_collects_and_applies_once_after_explicit_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, refresh, _, _ = _install_state(monkeypatch)
    headers = {"X-Trading-Partner-Console-Token": "synthetic-session-token"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_api.app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = await client.post(
            "/api/review-items/refresh",
            json={"confirmation": "refresh_review_items"},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json() == {
        "updated_count": 2,
        "coverage": {
            "agenda": "COMPLETE",
            "retro": "COMPLETE",
            "scorecards": "COMPLETE",
        },
        "complete": True,
        "scan_receipt": {
            "status": "COMPLETE",
            "complete": True,
            "sources": {},
            "persistence": {"status": "SAVED", "receipt_id": "audit_1"},
        },
    }
    assert workspace.refresh_calls == 1
    assert workspace.collect_calls == 0
    assert refresh.apply_calls == []
