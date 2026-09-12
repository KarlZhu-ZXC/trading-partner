"""Durable note-refresh submission and read-only progress."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from application.services.observation_refresh_service import ObservationRefreshService
from bootstrap import ApplicationContainer
from interfaces.console._shared import ConsoleRequestModel

router = APIRouter()


class RefreshRequest(ConsoleRequestModel):
    request_id: UUID


def _service(request: Request) -> ObservationRefreshService:
    container = cast(ApplicationContainer, request.app.state.container)
    return ObservationRefreshService(
        container.services.external_notes,
        container.services.external_note_review_drafts,
        container.operations.jobs,
    )


@router.get("/api/observation-refresh/{request_id}")
async def refresh_status(request: Request, request_id: UUID) -> dict[str, object]:
    value = await _service(request).status(str(request_id))
    if value["run"] is None:
        raise HTTPException(status_code=404, detail="Refresh run not found")
    return {"data": value}


@router.post("/api/observation-refresh", status_code=202)
async def refresh_submit(request: Request, payload: RefreshRequest) -> dict[str, object]:
    service = _service(request)
    key = str(payload.request_id)
    tasks = getattr(request.app.state, "observation_refresh_tasks", None)
    if tasks is None:
        tasks = {}
        request.app.state.observation_refresh_tasks = tasks
    # Serialize Console refresh submissions; existing capture locks still protect
    # CLI/provider identity writes across processes.
    active = next((task_key for task_key, task in tasks.items() if not task.done()), None)
    if active is not None:
        return {"data": await service.status(active), "coalesced": True}
    claimed = asyncio.Event()
    task = asyncio.create_task(service.run(key, on_claim=lambda _: claimed.set()))
    tasks[key] = task

    def finished(value: asyncio.Task[None]) -> None:
        tasks.pop(key, None)
        if not value.cancelled():
            value.exception()  # retrieve failure; never log private exception text
        claimed.set()

    task.add_done_callback(finished)
    try:
        await asyncio.wait_for(claimed.wait(), timeout=5)
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail={"code": "OBSERVATION_CLAIM_PENDING", "request_id": key},
        ) from None
    result = await service.status(key)
    if result["run"] is None:
        raise HTTPException(status_code=503, detail="Refresh could not be started")
    return {"data": result}
