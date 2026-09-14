from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

import interfaces.console.api as api


@pytest.mark.asyncio
async def test_quick_review_read_and_confirmation_boundary(monkeypatch):
    service = NS(
        get=Mock(return_value={"subject_id": "s1", "review_token": "test"}),
        submit=Mock(
            return_value={
                "decision_id": "d2",
                "status": "RECORDED",
                "action": "maintain",
                "review_due_at": None,
            }
        ),
    )
    monkeypatch.setattr(api, "_container", lambda r: NS(services=NS(quick_review=service)))
    monkeypatch.setattr(api.app.state, "console_session_token", "test-only", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1:8765"
    ) as client:
        response = await client.get("/api/research/s1/quick-review")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        service.submit.assert_not_called()
        body = dict(
            review_token="test",
            baseline_decision_id="d1",
            action="maintain",
            rationale="No action",
            idempotency_key="one",
            confirmed=True,
        )
        assert (await client.post("/api/research/s1/quick-review", json=body)).status_code == 403
        headers = {"X-Trading-Partner-Console-Token": "test-only"}
        assert (
            await client.post(
                "/api/research/s1/quick-review", json={**body, "confirmed": False}, headers=headers
            )
        ).status_code == 422
        response = await client.post("/api/research/s1/quick-review", json=body, headers=headers)
        assert response.status_code == 200 and response.json()["data"]["status"] == "RECORDED"
        service.submit.assert_called_once()
