"""Console replay is an explicit scoped read, never an alternate write gate."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from domain.common.errors import DataContractError
from interfaces.console.research_links_api import router


@pytest.mark.asyncio
async def test_replay_route_is_passive_scoped_and_not_cached() -> None:
    calls: list[tuple[str, str]] = []

    def read(subject_id: str, decision_id: str) -> dict[str, str]:
        calls.append((subject_id, decision_id))
        return {"subject_id": subject_id, "decision_id": decision_id}

    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(
        services=SimpleNamespace(decision_replay=SimpleNamespace(get=read))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        url = "/api/research/case_synthetic/decisions/decision_synthetic/replay"
        result = await client.get(url)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["data"]["decision_id"] == "decision_synthetic"
        assert (await client.post(url, json={"confirmation": "confirm"})).status_code == 405
    assert calls == [("case_synthetic", "decision_synthetic")]


@pytest.mark.asyncio
async def test_replay_route_hides_domain_error_details() -> None:
    def read(subject_id: str, decision_id: str) -> None:
        raise DataContractError("private text must not escape", details={"secret": "synthetic"})

    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(
        services=SimpleNamespace(decision_replay=SimpleNamespace(get=read))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get("/api/research/case_synthetic/decisions/decision_other/replay")
    assert result.status_code == 422
    assert "private text" not in result.text
    assert "secret" not in result.text
