from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import interfaces.console.observation_refresh_api as refresh_api
from interfaces.console.api import app


@pytest.mark.asyncio
async def test_refresh_submission_returns_durable_identity_before_work_finishes(monkeypatch):
    release = asyncio.Event()
    saved = {}

    class Service:
        async def run(self, key, *, on_claim):
            saved[key] = {"request_id": key, "run": {"status": "RUNNING"}, "stages": {}}
            on_claim(SimpleNamespace())
            await release.wait()
            saved[key]["run"]["status"] = "SUCCEEDED"

        async def status(self, key):
            return saved.get(key, {"run": None})

    monkeypatch.setattr(refresh_api, "_service", lambda request: Service())
    monkeypatch.setattr(app.state, "console_session_token", "synthetic-token", raising=False)
    monkeypatch.setattr(app.state, "observation_refresh_tasks", {}, raising=False)
    headers = {"X-Trading-Partner-Console-Token": "synthetic-token"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        key = str(uuid4())
        try:
            response = await asyncio.wait_for(
                client.post(
                    "/api/observation-refresh",
                    json={"request_id": key},
                    headers=headers,
                ),
                timeout=1,
            )
            assert response.status_code == 202
            assert response.json()["data"]["request_id"] == key
            assert response.json()["data"]["run"]["status"] == "RUNNING"
            second = await client.post(
                "/api/observation-refresh",
                json={"request_id": str(uuid4())},
                headers=headers,
            )
            assert second.json()["data"]["request_id"] == key
            assert len(saved) == 1
            read = await client.get(f"/api/observation-refresh/{key}")
            assert read.status_code == 200
            assert (
                await client.post("/api/observation-refresh", json={"request_id": key})
            ).status_code == 403
            assert (
                await client.post(
                    "/api/observation-refresh", json={"request_id": "bad"}, headers=headers
                )
            ).status_code == 422
        finally:
            tasks = list(app.state.observation_refresh_tasks.values())
            release.set()
            await asyncio.gather(*tasks)
