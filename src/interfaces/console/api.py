"""FastAPI surface for the loopback-only Trading Partner console."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from application import __version__
from application.dto.catalyst_agenda_sync import (
    CatalystAgendaSyncInput,
)
from application.dto.monitoring import MonitorArchiveInput
from application.services.trade_retro_schedule import trade_retro_weekly_windows
from bootstrap import ApplicationContainer, build_default_application
from interfaces.console.agent_api import build_agent_runtime_state
from interfaces.console.agent_api import router as agent_router
from interfaces.console.catalog import capability_catalog
from interfaces.mcp.server import create_capability_registry
from interfaces.mcp.tool_inventory import MCP_VNEXT_TOOL_NAMES
from interfaces.mcp.tools.compact import (
    CapabilityConfirmationRequiredError,
    CapabilityNotFoundError,
    CompactCapabilityRegistry,
)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.console_session_token = secrets.token_urlsafe(32)
    container = build_default_application()
    registry = create_capability_registry(container)
    app.state.container = container
    app.state.capability_registry = registry
    app.state.capabilities = capability_catalog(registry.list_tools(), registry.policies)
    agent_state = build_agent_runtime_state(container, registry)
    app.state.agent_runtime_state = agent_state
    # Keep the three collaborators discoverable for channel adapters and
    # diagnostics without exposing any model credentials or endpoint details.
    app.state.agent_runtime = agent_state.runtime
    app.state.agent_gateway = agent_state.capability_gateway
    app.state.agent_context = agent_state.context_service
    app.state.agent_action_gateway = agent_state.action_gateway
    app.state.agent_handoff_service = agent_state.handoff_service
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

_CONSOLE_HOSTS = frozenset({"127.0.0.1", "localhost"})
_CONSOLE_ORIGINS = frozenset(
    {
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    }
)
_CONSOLE_TOKEN_HEADER = "X-Trading-Partner-Console-Token"


@app.middleware("http")
async def _enforce_console_boundary(request: Request, call_next: Any) -> Any:
    """Keep browser writes inside the current loopback Console session."""
    hostname = request.url.hostname
    if hostname not in _CONSOLE_HOSTS:
        return JSONResponse(status_code=400, content={"detail": "Invalid console host"})

    origin = request.headers.get("origin")
    if origin is not None and origin not in _CONSOLE_ORIGINS:
        return JSONResponse(status_code=403, content={"detail": "Console origin is not allowed"})

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        expected = getattr(request.app.state, "console_session_token", None)
        supplied = request.headers.get(_CONSOLE_TOKEN_HEADER)
        if (
            not isinstance(expected, str)
            or not isinstance(supplied, str)
            or not secrets.compare_digest(supplied, expected)
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Console session token is missing or expired"},
            )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_CONSOLE_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type", _CONSOLE_TOKEN_HEADER],
)

app.include_router(agent_router)


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


class MonitorArchiveRequest(_RequestModel):
    expected_version: int = Field(ge=1)
    confirmation: Literal["monitor_archive"]


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


class AgendaSyncRequest(_RequestModel):
    window_days: int = Field(default=30, ge=1, le=180)
    instrument_ids: tuple[str, ...] = ()
    fred_release_ids: tuple[int, ...] = ()
    as_of: datetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class AgendaSummaryRequest(_RequestModel):
    window_days: int = Field(default=7, ge=1, le=30)


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
        raise HTTPException(
            status_code=404,
            detail="MCP tool is not in the vNext surface",
        ) from None
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


@app.get("/api/session")
def console_session(request: Request) -> dict[str, str]:
    """Issue the opaque token used by this process's loopback Console UI."""
    return {"token": cast(str, request.app.state.console_session_token)}


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
            result = await container.operations.notifications.send_test()
        elif action == "notification_flush":
            lock = container.resources.monitor_run_lock
            acquired = lock.acquire()
            if not acquired:
                raise HTTPException(status_code=409, detail="Monitor operation is already active")
            result = await container.operations.notifications.flush_pending()
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
        "dashboard": _canonical_subject_transport(
            await _invoke_capability(
                request,
                "monitor_read",
                {"request": {"operation": "dashboard", "status": None}},
            )
        ),
        "runs": _canonical_subject_transport(
            await _invoke_capability(
                request,
                "monitor_read",
                {"request": {"operation": "runs", "limit": run_limit}},
            )
        ),
        "events": _canonical_subject_transport(
            await _invoke_capability(
                request,
                "monitor_read",
                {"request": {"operation": "events", "limit": event_limit}},
            )
        ),
    }


@app.post("/api/monitors/{monitor_id}/archive")
def archive_monitor(
    monitor_id: str,
    payload: MonitorArchiveRequest,
    request: Request,
) -> dict[str, Any]:
    """Soft-delete one Monitor through the shared application service."""
    result = _container(request).services.monitoring.archive(
        MonitorArchiveInput(
            monitor_id=monitor_id,
            expected_version=payload.expected_version,
            confirmed_by="user",
            idempotency_key=f"console-monitor-archive-{monitor_id}-{payload.expected_version}",
        )
    )
    return result.model_dump(mode="json")


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


@app.get("/api/portfolio")
async def portfolio(
    request: Request,
    transaction_limit: int = Query(default=500, ge=1, le=1000),
    coverage_limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Build the durable Portfolio Hub from compact read capabilities only."""

    accounts_result = await _durable_console_call(
        request,
        "account_get",
        {"request": {"operation": "positions"}},
    )
    transactions_result = await _durable_console_call(
        request,
        "account_get",
        {"request": {"operation": "transactions", "limit": transaction_limit}},
    )
    exposure_result = await _durable_console_call(
        request,
        "portfolio_analyze",
        {"request": {"operation": "exposure"}},
    )
    coverage_result = await _durable_console_call(
        request,
        "portfolio_analyze",
        {"request": {"operation": "coverage", "limit": coverage_limit}},
    )
    risk_policy_result = await _durable_console_call(
        request,
        "portfolio_risk_get",
        {"request": {"operation": "policy"}},
    )
    risk_check_result = await _durable_console_call(
        request,
        "portfolio_risk_get",
        {"request": {"operation": "check"}},
    )
    return {
        "accounts": accounts_result,
        "transactions": transactions_result,
        "exposure": exposure_result,
        "coverage": coverage_result,
        "risk_policy": risk_policy_result,
        "risk_check": risk_check_result,
    }


@app.get("/api/retro")
async def trade_retro(request: Request) -> dict[str, Any]:
    """Return immutable Trade Retro runs and review revisions without Provider I/O."""

    result = cast(
        dict[str, Any],
        await _invoke_capability(
            request,
            "portfolio_analyze",
            {"request": {"operation": "retro_history", "limit": 50}},
        ),
    )
    review_start, review_end, prepare_start, prepare_end = trade_retro_weekly_windows(
        _container(request).context.clock.now()
    )
    return {
        **result,
        "console_windows": {
            "previous": {"start": review_start, "end": review_end},
            "next": {"start": prepare_start, "end": prepare_end},
        },
    }


def _console_failure(request: Request, error: Exception, code: str) -> dict[str, Any]:
    """Represent one failed console capability without hiding other aggregates."""

    return {
        "ok": False,
        "data": None,
        "warnings": [],
        "errors": [{"code": code, "message": _sanitized_error(request, error)}],
        "degraded": True,
    }


async def _durable_console_call(
    request: Request,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Invoke one compact read and preserve a per-capability failure envelope."""

    try:
        result = await _invoke_capability(request, tool_name, arguments)
    except Exception as error:  # noqa: BLE001 - one failed tile must not hide the hub
        return _console_failure(request, error, "CONSOLE_DURABLE_CALL_FAILED")
    if isinstance(result, dict):
        return result
    return _console_failure(
        request,
        TypeError(f"{tool_name} returned a non-object result"),
        "CONSOLE_DURABLE_CALL_INVALID",
    )


async def _watchlist_envelopes(request: Request) -> dict[str, Any]:
    """Read groups plus the active Moomoo ``All`` aggregate through compact MCP."""

    groups = await _durable_console_call(
        request,
        "watchlist_get",
        {"request": {"operation": "groups"}},
    )
    groups_data = groups.get("data")
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
    items = await _durable_console_call(
        request,
        "watchlist_get",
        {"request": item_request},
    )
    return {
        "groups": groups,
        "items": items,
        "scope": {
            "group_name": aggregate_group_name,
            "all_active_moomoo_items": aggregate_group_name is not None,
        },
    }


@app.get("/api/watchlist")
async def watchlist(request: Request) -> dict[str, Any]:
    """Return the durable Watchlist items while retaining its groups envelope."""

    return await _watchlist_envelopes(request)


def _research_state_failure(request: Request, error: Exception) -> dict[str, Any]:
    """Keep one Research Subject visible when its state read fails.

    The research console is an audit/read surface.  A broken state read for one
    Research Subject must therefore remain a normal failed envelope rather than
    aborting the whole aggregate.  The exception text is passed through the
    same secret redactor used by the other console endpoints.
    """

    return {
        "ok": False,
        "data": None,
        "warnings": [],
        "errors": [
            {
                "code": "CONSOLE_RESEARCH_STATE_READ_FAILED",
                "message": _sanitized_error(request, error),
            }
        ],
        "degraded": True,
    }


def _canonical_subject_transport(value: Any) -> Any:
    """Translate compact MCP's frozen ``case_*`` fields for the internal Console BFF."""

    if isinstance(value, dict):
        aliases = {
            "case_id": "subject_id",
            "case_type": "subject_type",
            "linked_case_ids": "linked_subject_ids",
        }
        return {
            aliases.get(key, key): _canonical_subject_transport(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical_subject_transport(item) for item in value]
    return value


@app.get("/api/research")
async def research(request: Request) -> dict[str, Any]:
    """Return every durable Research Subject and its current Thesis state.

    This endpoint deliberately routes through the same compact capability
    registry exposed to MCP.  It does not refresh providers, read repositories,
    or create a second research API. Subject listing is paged at the public
    maximum (200) and each state envelope is retained beside its subject so a
    partial read remains visible and auditable in the UI.
    """

    page_size = 200
    offset = 0
    subject_pages: list[dict[str, Any]] = []
    subjects: list[dict[str, Any]] = []

    while True:
        page = await _invoke_capability(
            request,
            "investment_case_read",
            {
                "request": {
                    "operation": "query",
                    "include_archived": True,
                    "limit": page_size,
                    "offset": offset,
                }
            },
        )
        if not isinstance(page, dict):
            page = {
                "ok": False,
                "data": None,
                "warnings": [],
                "errors": [
                    {
                        "code": "CONSOLE_RESEARCH_SUBJECT_LIST_INVALID",
                        "message": "investment_case_read returned a non-object result",
                    }
                ],
                "degraded": True,
            }
        subject_pages.append(_canonical_subject_transport(page))

        page_data = page.get("data")
        page_items = page_data.get("items") if isinstance(page_data, dict) else None
        if not isinstance(page_items, list):
            break
        for subject_wire in page_items:
            if not isinstance(subject_wire, dict):
                continue
            case_id = subject_wire.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                subjects.append(
                    {
                        "subject": _canonical_subject_transport(subject_wire),
                        "state": _research_state_failure(
                            request,
                            ValueError("Research Subject is missing legacy case_id"),
                        ),
                    }
                )
                continue
            try:
                state = await _invoke_capability(
                    request,
                    "research_judgment_get",
                    {
                        "request": {
                            "operation": "state",
                            "case_id": case_id,
                            "include_archived_theses": True,
                            "include_watchlist": False,
                        }
                    },
                )
            except Exception as error:  # noqa: BLE001 - preserve other subjects
                state = _research_state_failure(request, error)
            subjects.append(
                {
                    "subject": _canonical_subject_transport(subject_wire),
                    "state": _canonical_subject_transport(state),
                }
            )

        if len(page_items) < page_size:
            break
        offset += page_size

    # ``ResearchSubjectListDTO.total`` is the page count, not a global count.
    # The aggregate has already walked every page, so its durable count is the
    # number of valid Research Subject records retained below.
    total = len(subjects)
    return {
        "subjects": subjects,
        "subject_list": {
            "pages": subject_pages,
            "total": total,
            "page_size": page_size,
        },
    }


async def _console_subject_choices(
    request: Request,
    *,
    include_archived: bool,
    selected_subject_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Page lightweight Subject choices and load state only for the selected row."""

    page_size = 200
    offset = 0
    subjects: list[dict[str, Any]] = []
    while True:
        page = await _durable_console_call(
            request,
            "investment_case_read",
            {
                "request": {
                    "operation": "query",
                    "include_archived": include_archived,
                    "limit": page_size,
                    "offset": offset,
                }
            },
        )
        data = page.get("data") if isinstance(page, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            aggregate: dict[str, Any] = {
                "subject": _canonical_subject_transport(item),
                "state": None,
            }
            case_id = item.get("case_id")
            if selected_subject_id is not None and case_id == selected_subject_id:
                aggregate["state"] = _canonical_subject_transport(
                    await _durable_console_call(
                        request,
                        "research_judgment_get",
                        {
                            "request": {
                                "operation": "state",
                                "case_id": selected_subject_id,
                                "include_archived_theses": True,
                                "include_watchlist": False,
                            }
                        },
                    )
                )
            subjects.append(aggregate)
        if len(items) < page_size:
            break
        offset += len(items)
    return subjects, {"total": len(subjects), "page_size": page_size}


@app.get("/api/scorecards")
async def scorecards(
    request: Request,
    subject_id: str | None = Query(default=None, min_length=1, max_length=100),
    thesis_id: str | None = Query(default=None, min_length=1, max_length=100),
    limit: int = Query(default=50, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return Research Subjects plus immutable Judgment Scorecard history.

    Both reads stay behind the compact capability registry and remain durable-only.
    A failed history read is retained beside the independently readable Research
    Subjects so the Console can diagnose the exact boundary instead of hiding the
    whole page.
    """

    subjects, subject_list = await _console_subject_choices(
        request,
        include_archived=True,
        selected_subject_id=subject_id,
    )
    history_request: dict[str, Any] = {
        "operation": "scorecard_history",
        "limit": limit,
        "offset": offset,
    }
    if subject_id is not None:
        history_request["case_id"] = subject_id
    if thesis_id is not None:
        history_request["thesis_id"] = thesis_id
    history = await _durable_console_call(
        request,
        "research_judgment_get",
        {"request": history_request},
    )
    return {
        "subjects": subjects,
        "subject_list": subject_list,
        "scorecards": _canonical_subject_transport(history),
    }


@app.get("/api/agenda")
async def catalyst_agenda(
    request: Request,
    as_of: datetime | None = None,
    window_days: int = Query(default=30, ge=1, le=180),
    agenda_item_id: str | None = Query(default=None, min_length=1, max_length=128),
    include_history: bool = False,
    subject_id: str | None = Query(default=None, min_length=1, max_length=128),
    instrument_id: str | None = Query(default=None, min_length=1, max_length=128),
    scope: str | None = Query(default=None, min_length=1, max_length=32),
    kind: str | None = Query(default=None, min_length=1, max_length=32),
    status: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return durable Catalyst Agenda items and lightweight Subject choices."""

    filters = {
        key: [value]
        for key, value in (
            ("case_ids", subject_id),
            ("instrument_ids", instrument_id),
            ("scopes", scope),
            ("kinds", kind),
            ("statuses", status),
        )
        if value is not None
    }
    agenda_request: dict[str, Any] = {
        "operation": "agenda",
        "window_days": window_days,
        "include_history": include_history,
        "limit": limit,
        "offset": offset,
    }
    if as_of is not None:
        agenda_request["as_of"] = as_of
    if agenda_item_id is not None:
        agenda_request["agenda_item_id"] = agenda_item_id
    if filters:
        agenda_request["filters"] = filters

    agenda = await _durable_console_call(
        request,
        "research_memory_get",
        {"request": agenda_request},
    )
    subject_aggregates, subject_list = await _console_subject_choices(
        request,
        include_archived=False,
    )
    subject_items = [item["subject"] for item in subject_aggregates]
    subjects = {
        "ok": True,
        "data": {
            "items": subject_items,
            "total": subject_list["total"],
            "has_more": False,
        },
        "warnings": [],
        "errors": [],
        "degraded": False,
    }
    return {
        "agenda": _canonical_subject_transport(agenda),
        "subjects": _canonical_subject_transport(subjects),
    }


@app.post("/api/agenda/sync")
async def catalyst_agenda_sync(request: Request, payload: AgendaSyncRequest) -> dict[str, Any]:
    input_value = CatalystAgendaSyncInput(
        instrument_ids=payload.instrument_ids,
        fred_release_ids=payload.fred_release_ids,
        window_days=payload.window_days,
        as_of=payload.as_of,
        idempotency_key=payload.idempotency_key,
    )
    sync_result = await _container(request).operations.catalyst_agenda_sync.sync(input_value)
    return {"data": jsonable_encoder(sync_result)}


@app.get("/api/agenda/outcome-candidates")
async def catalyst_agenda_outcome_candidates(
    request: Request,
    subject_id: str | None = Query(default=None, min_length=1, max_length=128),
    instrument_id: str | None = Query(default=None, min_length=1, max_length=256),
    as_of: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Read bounded durable outcome candidates; never refresh a Provider."""

    if subject_id is not None:
        candidate_request: dict[str, Any] = {
            "operation": "timeline",
            "case_id": subject_id,
            "entity_types": ["event", "report", "evidence"],
            "limit": limit,
        }
    else:
        candidate_request = {
            "operation": "search",
            "entity_types": ["event", "report", "evidence"],
            "include_superseded": False,
            "limit": limit,
            "offset": 0,
        }
        if instrument_id is not None:
            candidate_request["instrument_id"] = instrument_id
    if as_of is not None:
        candidate_request["as_of"] = as_of
    candidates = await _durable_console_call(
        request,
        "research_memory_get",
        {"request": candidate_request},
    )
    return {"candidates": _canonical_subject_transport(candidates)}


@app.get("/api/agenda/summary-preview")
async def catalyst_agenda_summary_preview(
    request: Request,
    window_days: int = Query(default=7, ge=1, le=30),
) -> dict[str, Any]:
    preview = _container(request).operations.catalyst_agenda_notifications.preview_daily(
        window_days=window_days
    )
    return {"data": jsonable_encoder(preview)}


@app.post("/api/agenda/summary-send")
async def catalyst_agenda_summary_send(
    request: Request,
    payload: AgendaSummaryRequest,
) -> dict[str, Any]:
    receipt = await _container(
        request
    ).operations.catalyst_agenda_notifications.enqueue_daily(
        window_days=payload.window_days
    )
    return {"data": jsonable_encoder(receipt)}


@app.get("/api/operations")
async def operations(request: Request) -> dict[str, Any]:
    services = _container(request).operations
    health = await _invoke_capability(request, "system_health", {})
    monitor_dashboard = await _invoke_capability(
        request,
        "monitor_read",
        {"request": {"operation": "dashboard", "status": None}},
    )
    return {
        "post_market_sync": services.post_market_sync.status().model_dump(mode="json"),
        "notifications": services.notifications.status().model_dump(mode="json"),
        "maintenance": services.maintenance.status().model_dump(mode="json"),
        "health": health,
        "monitor_dashboard": _canonical_subject_transport(monitor_dashboard),
        "sync_receipts": [
            {
                "run_id": item.run_id,
                "market_session_date": item.market_session_date,
                "scheduled_for": item.scheduled_for,
                "started_at": item.started_at,
                "completed_at": item.completed_at,
                "status": item.status,
                "portfolio_status": item.portfolio_status,
                "watchlist_status": item.watchlist_status,
                "account_snapshot_count": len(item.account_snapshot_ids),
                "watchlist_groups_synced": item.watchlist_groups_synced,
                "watchlist_membership_relations_synced": (
                    item.watchlist_membership_relations_synced
                ),
                "warning_codes": item.warning_codes,
                "error_codes": item.error_codes,
                "attempt_count": item.attempt_count,
            }
            for item in services.post_market_sync.recent_runs(20)
        ],
        "outbox_entries": [
            {
                "notification_id": item.notification_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "channel": item.channel,
                "title": item.title,
                "status": item.status,
                "attempt_count": item.attempt_count,
                "next_attempt_at": item.next_attempt_at,
                "created_at": item.created_at,
                "last_attempt_at": item.last_attempt_at,
                "delivered_at": item.delivered_at,
                "last_error_code": item.last_error_code,
                "expires_at": item.expires_at,
            }
            for item in services.notifications.recent_entries(50)
        ],
    }


@app.get("/api/schwab/oauth")
def schwab_oauth(request: Request) -> dict[str, Any]:
    return _schwab_oauth_status(request)


@app.get("/api/overview")
async def overview(request: Request) -> dict[str, Any]:
    container = _container(request)
    research_attention: list[dict[str, Any]] = []
    subject_page = await _invoke_capability(
        request,
        "investment_case_read",
        {
            "request": {
                "operation": "query",
                "include_archived": False,
                "limit": 200,
                "offset": 0,
            }
        },
    )
    subject_data = subject_page.get("data") if isinstance(subject_page, dict) else None
    subject_items = subject_data.get("items") if isinstance(subject_data, dict) else None
    for subject in subject_items if isinstance(subject_items, list) else []:
        if not isinstance(subject, dict) or not isinstance(subject.get("case_id"), str):
            continue
        state = await _durable_console_call(
            request,
            "research_judgment_get",
            {
                "request": {
                    "operation": "state",
                    "case_id": subject["case_id"],
                    "include_archived_theses": False,
                    "include_watchlist": False,
                }
            },
        )
        state_data = state.get("data")
        pending = state_data.get("pending_candidates") if isinstance(state_data, dict) else None
        if isinstance(pending, list) and pending:
            research_attention.append(
                {
                    "subject_id": subject["case_id"],
                    "title": subject.get("title"),
                    "pending_count": len(pending),
                }
            )
    return {
        "health": await _invoke_capability(request, "system_health", {}),
        "monitor_dashboard": _canonical_subject_transport(
            await _invoke_capability(
                request,
                "monitor_read",
                {"request": {"operation": "dashboard", "status": None}},
            )
        ),
        "recent_runs": _canonical_subject_transport(
            await _invoke_capability(
                request,
                "monitor_read",
                {"request": {"operation": "runs", "limit": 20}},
            )
        ),
        "post_market_sync": container.operations.post_market_sync.status().model_dump(mode="json"),
        "notifications": container.operations.notifications.status().model_dump(
            mode="json"
        ),
        "maintenance": container.operations.maintenance.status().model_dump(mode="json"),
        "capability_count": len(MCP_VNEXT_TOOL_NAMES),
        "research_attention": _canonical_subject_transport(research_attention),
        "agenda_summary": _canonical_subject_transport(
            await _durable_console_call(
                request,
                "research_memory_get",
                {
                    "request": {
                        "operation": "agenda",
                        "window_days": 7,
                        "include_history": False,
                        "limit": 100,
                        "offset": 0,
                    }
                },
            )
        ),
    }
