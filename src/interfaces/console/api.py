"""FastAPI surface for the loopback-only Trading Partner console."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from application import __version__
from bootstrap import ApplicationContainer, build_default_application
from interfaces.console.catalog import capability_catalog
from interfaces.mcp.server import create_capability_registry
from interfaces.mcp.tool_inventory import COMPACT_28_TOOL_NAMES
from interfaces.mcp.tools.compact import (
    CapabilityConfirmationRequiredError,
    CapabilityNotFoundError,
    CompactCapabilityRegistry,
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = build_default_application()
    registry = create_capability_registry(container)
    app.state.container = container
    app.state.capability_registry = registry
    app.state.capabilities = capability_catalog(registry.list_tools(), registry.policies)
    app.state.schwab_oauth_task = None
    try:
        yield
    finally:
        await container.aclose()


app = FastAPI(
    title="Trading Partner Local Console API",
    description="Loopback-only operational UI and compact MCP capability execution.",
    version=__version__,
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"^http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}$",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)


def _container(request: Request) -> ApplicationContainer:
    return cast(ApplicationContainer, request.app.state.container)


def _registry(request: Request) -> CompactCapabilityRegistry:
    return cast(CompactCapabilityRegistry, request.app.state.capability_registry)


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolInvokeRequest(_RequestModel):
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmation: str | None = Field(default=None, max_length=100)


ConsoleAction = Literal[
    "monitor_run_due",
    "post_market_sync_due",
    "post_market_sync_catch_up",
    "notification_test",
    "notification_flush",
    "database_backup",
    "cache_prune_preview",
    "cache_prune_apply",
    "schwab_oauth_renew",
    "schwab_oauth_renew_confirmed",
]


class ConsoleActionRequest(_RequestModel):
    action: ConsoleAction
    confirmation: str = Field(min_length=1, max_length=100)
    retention_days: int = Field(default=30, ge=1, le=3650)


def _sanitized_error(request: Request, error: Exception) -> str:
    return _container(request).context.secret_redactor.redact_text(str(error))


def _schwab_oauth_status(request: Request) -> dict[str, Any]:
    manager = _container(request).operations.schwab_oauth
    if manager is None:
        return {
            "configured": False,
            "flow": {
                "state": "IDLE",
                "message_code": "SCHWAB_OAUTH_NOT_CONFIGURED",
                "retry_requires_confirmation": False,
            },
            "token_health": None,
        }
    return {
        "configured": True,
        "flow": asdict(manager.status()),
        "token_health": manager.token_health().model_dump(mode="json"),
    }


async def _start_schwab_oauth(
    request: Request,
    *,
    confirm_retry_after_failure: bool,
) -> dict[str, Any]:
    manager = _container(request).operations.schwab_oauth
    if manager is None:
        raise HTTPException(status_code=422, detail="Schwab OAuth is not configured")

    running_task = request.app.state.schwab_oauth_task
    if running_task is not None and not running_task.done():
        return _schwab_oauth_status(request)

    current = manager.status()
    if current.state.value == "ACTIVE":
        return _schwab_oauth_status(request)
    if current.retry_requires_confirmation and not confirm_retry_after_failure:
        raise HTTPException(
            status_code=409,
            detail="Close the previous Schwab authorization tab before starting a new flow",
        )

    task = asyncio.create_task(
        asyncio.to_thread(
            manager.renew,
            confirm_retry_after_failure=confirm_retry_after_failure,
        )
    )
    request.app.state.schwab_oauth_task = task
    for _ in range(20):
        await asyncio.sleep(0.025)
        if task.done() or manager.status().state.value == "ACTIVE":
            break
    return _schwab_oauth_status(request)


async def _invoke_capability(
    request: Request,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    confirmation: str | None = None,
) -> Any:
    try:
        return await _registry(request).invoke(
            tool_name,
            arguments,
            confirmation=confirmation,
        )
    except CapabilityNotFoundError:
        raise HTTPException(status_code=404, detail="MCP tool is not in compact_28") from None
    except CapabilityConfirmationRequiredError:
        raise HTTPException(
            status_code=409,
            detail="Capability requires an explicit matching confirmation",
        ) from None
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail=_sanitized_error(request, error),
        ) from None


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    return cast(dict[str, Any], await _invoke_capability(request, "system_health", {}))


@app.get("/api/capabilities")
def capabilities(request: Request) -> dict[str, Any]:
    items: list[dict[str, Any]] = request.app.state.capabilities
    return {"count": len(items), "items": items}


@app.post("/api/tools/invoke")
async def invoke_tool(request: Request, payload: ToolInvokeRequest) -> dict[str, Any]:
    result = await _invoke_capability(
        request,
        payload.tool_name,
        payload.arguments,
        confirmation=payload.confirmation,
    )
    return {
        "tool_name": payload.tool_name,
        "result": jsonable_encoder(result),
    }


@app.post("/api/actions/run")
async def run_action(request: Request, payload: ConsoleActionRequest) -> dict[str, Any]:
    if payload.confirmation != payload.action:
        raise HTTPException(status_code=409, detail="Action confirmation does not match")
    container = _container(request)
    action = payload.action
    lock = None
    acquired = False
    result: object
    try:
        if action == "monitor_run_due":
            lock = container.resources.monitor_run_lock
            acquired = lock.acquire()
            if not acquired:
                raise HTTPException(status_code=409, detail="Monitor run is already active")
            result = await container.operations.monitor_dispatch.run_due()
        elif action in {"post_market_sync_due", "post_market_sync_catch_up"}:
            lock = container.resources.post_market_sync_lock
            acquired = lock.acquire()
            if not acquired:
                raise HTTPException(status_code=409, detail="Post-market sync is already active")
            if action == "post_market_sync_due":
                result = await container.operations.post_market_sync.run_if_due()
            else:
                result = await container.operations.post_market_sync.catch_up_latest_due()
        elif action == "notification_test":
            result = await container.operations.monitor_notifications.send_test()
        elif action == "notification_flush":
            lock = container.resources.monitor_run_lock
            acquired = lock.acquire()
            if not acquired:
                raise HTTPException(status_code=409, detail="Monitor operation is already active")
            result = await container.operations.monitor_notifications.flush_pending()
        elif action == "database_backup":
            result = container.operations.maintenance.backup()
        elif action in {"schwab_oauth_renew", "schwab_oauth_renew_confirmed"}:
            result = await _start_schwab_oauth(
                request,
                confirm_retry_after_failure=action == "schwab_oauth_renew_confirmed",
            )
        else:
            result = container.operations.maintenance.prune_expired_cache(
                retention_days=payload.retention_days,
                dry_run=action == "cache_prune_preview",
            )
        return {"action": action, "result": jsonable_encoder(result)}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail=_sanitized_error(request, error),
        ) from None
    finally:
        if lock is not None and acquired:
            lock.release()


@app.get("/api/monitors")
async def monitors(
    request: Request,
    run_limit: int = Query(default=20, ge=1, le=100),
    event_limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    return {
        "dashboard": await _invoke_capability(
            request,
            "monitor_read",
            {"request": {"operation": "dashboard", "status": None}},
        ),
        "runs": await _invoke_capability(
            request,
            "monitor_read",
            {"request": {"operation": "runs", "limit": run_limit}},
        ),
        "events": await _invoke_capability(
            request,
            "monitor_read",
            {"request": {"operation": "events", "limit": event_limit}},
        ),
    }


@app.get("/api/accounts")
async def accounts(request: Request) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        await _invoke_capability(
            request,
            "account_get",
            {"request": {"operation": "positions"}},
        ),
    )


@app.get("/api/watchlist")
async def watchlist(request: Request) -> dict[str, Any]:
    groups = await _invoke_capability(
        request,
        "watchlist_get",
        {"request": {"operation": "groups"}},
    )
    groups_data = groups.get("data") if isinstance(groups, dict) else None
    group_items = groups_data.get("groups") if isinstance(groups_data, dict) else None
    source = groups_data.get("source") if isinstance(groups_data, dict) else None
    aggregate_group_name = next(
        (
            str(group["name"])
            for group in group_items or ()
            if isinstance(group, dict)
            and source == "MOOMOO"
            and group.get("group_type") == "SYSTEM"
            and group.get("source_group_key") == "All"
            and group.get("active") is True
            and group.get("name")
        ),
        None,
    )
    item_request: dict[str, Any] = {"operation": "items", "limit": 500}
    if aggregate_group_name is not None:
        item_request["group_name"] = aggregate_group_name
    return {
        "items": await _invoke_capability(
            request,
            "watchlist_get",
            {"request": item_request},
        ),
        "scope": {
            "group_name": aggregate_group_name,
            "all_active_moomoo_items": aggregate_group_name is not None,
        },
    }


@app.get("/api/operations")
def operations(request: Request) -> dict[str, Any]:
    services = _container(request).operations
    return {
        "post_market_sync": services.post_market_sync.status().model_dump(mode="json"),
        "notifications": services.monitor_notifications.status().model_dump(mode="json"),
        "maintenance": services.maintenance.status().model_dump(mode="json"),
    }


@app.get("/api/schwab/oauth")
def schwab_oauth(request: Request) -> dict[str, Any]:
    return _schwab_oauth_status(request)


@app.get("/api/overview")
async def overview(request: Request) -> dict[str, Any]:
    container = _container(request)
    return {
        "health": await _invoke_capability(request, "system_health", {}),
        "monitor_dashboard": await _invoke_capability(
            request,
            "monitor_read",
            {"request": {"operation": "dashboard", "status": None}},
        ),
        "recent_runs": await _invoke_capability(
            request,
            "monitor_read",
            {"request": {"operation": "runs", "limit": 5}},
        ),
        "post_market_sync": container.operations.post_market_sync.status().model_dump(mode="json"),
        "notifications": container.operations.monitor_notifications.status().model_dump(
            mode="json"
        ),
        "maintenance": container.operations.maintenance.status().model_dump(mode="json"),
        "capability_count": len(COMPACT_28_TOOL_NAMES),
    }
