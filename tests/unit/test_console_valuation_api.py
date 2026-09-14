from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import interfaces.console.api as api


@pytest.mark.asyncio
async def test_valuation_routes_are_explicit_closed_and_token_gated(monkeypatch):
    svc = NS(
        history=Mock(return_value={"items": []}),
        prepare=AsyncMock(return_value={"source_token": "synthetic"}),
        calculate=Mock(return_value={"per_share_value": "50.0000"}),
        save=Mock(return_value={"version_id": "journal_synthetic"}),
    )
    monkeypatch.setattr(api, "_container", lambda r: NS(services=NS(valuation=svc)))
    monkeypatch.setattr(api.app.state, "console_session_token", "test-session-token", raising=False)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.get("/api/research/s1/valuation")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        svc.prepare.assert_not_called()
        assert (await client.post("/api/research/s1/valuation/source", json={})).status_code == 403
        headers = {"X-Trading-Partner-Console-Token": "test-session-token"}
        assert (
            await client.post("/api/research/s1/valuation/source", json={}, headers=headers)
        ).status_code == 200
        svc.prepare.assert_awaited_once_with("s1")
        body = {
            "source_token": "synthetic",
            "assumptions": {
                "normalization_factor": "1",
                "normalization_step": "0.2",
                "pe_multiple": "20",
                "pe_step": "5",
                "business_model": "operating_company",
                "rationale": "Synthetic",
            },
        }
        assert (
            await client.post("/api/research/s1/valuation/calculate", json=body, headers=headers)
        ).status_code == 200
        assert (
            await client.post(
                "/api/research/s1/valuation/calculate",
                json={**body, "eps_diluted": "999"},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.post("/api/research/s1/valuation/versions", json=body, headers=headers)
        ).status_code == 422
        svc.save.assert_not_called()
        saved = await client.post(
            "/api/research/s1/valuation/versions",
            headers=headers,
            json={
                **body,
                "confirmed": True,
                "authorization_note": "Save synthetic assumptions",
                "idempotency_key": "once",
            },
        )
        assert saved.status_code == 200
        svc.save.assert_called_once()
