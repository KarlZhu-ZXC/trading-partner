"""The Console reads saved discussions through the existing note privacy boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from domain.common.errors import DataContractError
from interfaces.console.research_links_api import router


@pytest.mark.asyncio
@pytest.mark.parametrize("include_content", [False, True])
async def test_discussion_route_passes_explicit_read_and_keeps_no_store(include_content):
    desk = MagicMock()
    desk.note.return_value = {"discussion_content": None}
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(services=SimpleNamespace(note_desk=desk))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/api/note-desk/revision_synthetic", params={
            "journal_id": "journal_synthetic", "text_offset": 6000,
            "include_content": str(include_content).lower(),
        })
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    desk.note.assert_called_once_with(
        "revision_synthetic", include_content=include_content,
        private_content_authorized=include_content, block_index=0, text_offset=6000,
        context_cursor=None, journal_id="journal_synthetic",
        include_discussions=False, include_portfolio=True,
    )
    assert len(desk.method_calls) == 1


@pytest.mark.asyncio
async def test_discussion_route_rejects_invalid_scope_without_leaking_source_text():
    desk = MagicMock()
    desk.note.side_effect = DataContractError("synthetic private error must not escape")
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(services=SimpleNamespace(note_desk=desk))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/api/note-desk/revision_synthetic", params={
            "journal_id": "journal_other_note", "include_content": "true",
        })
        for params in ({"journal_id": ""}, {"journal_id": "x" * 129}, {"text_offset": -1}):
            invalid = await client.get("/api/note-desk/revision_synthetic", params=params)
            assert invalid.status_code == 422
    assert response.status_code == 409
    assert "synthetic private error" not in response.text
    assert desk.note.call_count == 1


@pytest.mark.asyncio
async def test_note_search_is_a_bounded_no_store_read():
    desk = MagicMock()
    desk.browse.return_value = {"items": [], "offset": 30, "has_more": False}
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(services=SimpleNamespace(note_desk=desk))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/api/note-desk/search", json={
            "query": "Synthetic title", "offset": 30,
        })
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        desk.browse.assert_called_once_with(query="Synthetic title", offset=30, limit=30)
        for body in ({"limit": 101}, {"offset": -1}, {"query": "x" * 201}, {"unknown": True}):
            assert (await client.post("/api/note-desk/search", json=body)).status_code == 422
        assert desk.browse.call_count == 1
        response = await client.get("/api/note-desk?offset=60")
        assert response.status_code == 200
        desk.browse.assert_called_with(limit=30, offset=60)
    assert not desk.note.called and not desk.inbox.called


@pytest.mark.asyncio
async def test_history_and_receipt_routes_are_no_store_reads_with_safe_errors():
    desk = MagicMock()
    desk.history.return_value = {"items": [], "note_revision_id": "synthetic"}
    desk.conclusion_receipt.return_value = {"status": "NOT_FOUND"}
    desk.history_trades.return_value = {"items": []}
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(services=SimpleNamespace(note_desk=desk))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        for path in (
            "/api/note-desk/synthetic/history?cursor=synthetic",
            "/api/note-desk/synthetic/conclusion-receipt?request_id=synthetic",
            "/api/note-desk/synthetic/history-trades?start=2026-10-01T00:00:00Z&end=2026-10-02T00:00:00Z",
        ):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
        desk.history.assert_called_once_with("synthetic", cursor="synthetic")
        desk.conclusion_receipt.assert_called_once_with("synthetic", "synthetic")
        assert desk.history_trades.call_args.kwargs["start"].utcoffset() is not None
        desk.history.side_effect = DataContractError("Private source error")
        response = await client.get("/api/note-desk/synthetic/history")
        assert response.status_code == 409 and "Private source error" not in response.text
    assert not desk.note.called and not desk.browse.called
