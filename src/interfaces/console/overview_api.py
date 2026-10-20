"""Overview transport and explicit local ReviewItem materialization command."""

from __future__ import annotations

import asyncio
from typing import Any, Literal, cast

from fastapi import APIRouter, Request

from bootstrap import ApplicationContainer
from interfaces.console._shared import (
    ConsoleRequestModel,
    canonical_subject_transport,
    failure_payload,
)
from interfaces.mcp.tool_inventory import MCP_VNEXT_TOOL_NAMES, PUBLIC_TOOL_NAMES

router = APIRouter()


class RefreshReviewItemsRequest(ConsoleRequestModel):
    confirmation: Literal["refresh_review_items"]


async def _read(request: Request, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await request.app.state.capability_registry.invoke_uncompacted(name, arguments)
        if isinstance(result, dict):
            return result
    except Exception:  # noqa: BLE001 - one unavailable tile must not hide other sources
        pass
    return failure_payload("CONSOLE_DURABLE_CALL_FAILED", "Durable source unavailable")


@router.get("/api/overview")
async def overview(
    request: Request, scope: Literal["full", "notes"] = "full",
) -> dict[str, Any]:
    container = cast(ApplicationContainer, request.app.state.container)
    read_workspace = (
        container.services.review_workspace_query.read_operations
        if scope == "notes" else container.services.review_workspace_query.read
    )
    workspace, health, monitors, runs = await asyncio.gather(
        asyncio.to_thread(read_workspace),
        _read(request, "system_health", {}),
        _read(request, "monitor_read", {"request": {"operation": "dashboard", "status": None}}),
        _read(request, "monitor_read", {"request": {"operation": "runs", "limit": 20}}),
    )
    result = {
        **workspace,
        "health": health,
        "monitor_dashboard": monitors,
        "recent_runs": runs,
        "capability_count": len(PUBLIC_TOOL_NAMES),
        "business_capability_count": len(MCP_VNEXT_TOOL_NAMES),
    }
    readers = {
        "post_market_sync": container.operations.post_market_sync.status,
        "notifications": container.operations.notifications.status,
        "maintenance": container.operations.maintenance.status,
    }
    if scope == "notes":
        readers = {"notifications": container.operations.notifications.status}
    for name, read_status in readers.items():
        try:
            status = await asyncio.to_thread(read_status)
            result[name] = status.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - preserve independent operational failures
            result[name] = failure_payload("CONSOLE_STATUS_UNAVAILABLE", "Status unavailable")
    return cast(dict[str, Any], canonical_subject_transport(result))


@router.post("/api/review-items/refresh")
async def refresh_review_items(
    request: Request,
    payload: RefreshReviewItemsRequest,
) -> dict[str, Any]:
    """Explicit local projection update; no provider refresh or judgment confirmation."""
    container = cast(ApplicationContainer, request.app.state.container)
    return await asyncio.to_thread(container.services.review_workspace_query.refresh)
