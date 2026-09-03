"""FastAPI surface for the loopback-only Trading Partner console."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Annotated, Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError, model_validator

from application import __version__
from application.dto.account_transactions import TradeCycleQueryInput
from application.dto.activity_annotations import ActivityAnnotationAppendInput
from application.dto.behavior_review import BehaviorReviewRunInput
from application.dto.catalyst_agenda_sync import (
    CatalystAgendaSyncInput,
)
from application.dto.external_note_review import ExternalNoteReviewTransitionInput
from application.dto.monitoring import MonitorArchiveInput
from application.dto.review_item import ReviewItemTransitionInput
from application.dto.trade_cycle_overrides import TradeCycleOverrideAppendInput
from application.services.attention_projection import (
    console_attention_payload,
    project_agenda_overdue_fields,
    project_trade_retro_fields,
    project_unresolved_agent_fields,
    project_unresolved_broker,
)
from application.services.external_note_sync_service import (
    ExternalObservationCaptureRequest,
)
from application.services.trade_retro_schedule import trade_retro_weekly_windows
from bootstrap import ApplicationContainer, build_default_application
from domain.common.errors import TradingPartnerError
from domain.external_note.attribution import attributed_blocks
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection
from interfaces.console._shared import ConsoleRequestModel, failure_payload
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
    app.state.observation_analysis_task = None
    app.state.observation_analysis_errors = {}
    try:
        yield
    finally:
        note_task = getattr(app.state, "observation_analysis_task", None)
        if note_task is not None and not note_task.done():
            note_task.cancel()
            with suppress(asyncio.CancelledError):
                await note_task
        await container.aclose()


app = FastAPI(
    title="Trading Partner Local Console API",
    description="Loopback-only operational UI and validated MCP capability execution.",
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


class _RequestModel(ConsoleRequestModel):
    pass


class ToolInvokeRequest(_RequestModel):
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmation: str | None = Field(default=None, max_length=100)
    preserve_full_result: bool = False


class MonitorArchiveRequest(_RequestModel):
    expected_version: int = Field(ge=1)
    confirmation: Literal["monitor_archive"]


class MoomooNotesSyncRequest(_RequestModel):
    analyze: bool = False


class ObservationSyncRequest(_RequestModel):
    source_code: str | None = Field(default=None, min_length=1, max_length=64)
    analyze: bool = False


class ObservationCaptureRequest(_RequestModel):
    source_code: str = Field(min_length=3, max_length=64, pattern=r"^[A-Z][A-Z0-9_]+$")
    external_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    full_body: str = Field(min_length=1, max_length=100_000)
    observed_at: datetime
    summary: str | None = Field(default=None, min_length=1, max_length=50_000)
    source_timestamp: datetime | None = None
    primary_instrument_id: str | None = Field(default=None, min_length=1, max_length=200)
    related_provider_stock_ids: tuple[str, ...] = Field(default=(), max_length=100)
    related_provider_codes: tuple[str, ...] = Field(default=(), max_length=100)
    visibility: str = Field(default="SELF", min_length=1, max_length=40)
    analyze: bool = False

    @model_validator(mode="after")
    def _aware_timestamps(self) -> ObservationCaptureRequest:
        for field_name in ("observed_at", "source_timestamp"):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")
        return self


class ObservationAnalysisRequest(_RequestModel):
    retry_failed: bool = True


class ObservationReviewEnsureRequest(_RequestModel):
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)


class ObservationReviewTransitionRequest(_RequestModel):
    status: Literal["DEFERRED", "ADOPTED", "NO_ACTION"]
    expected_version: int = Field(ge=1)
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    due_at: datetime | None = None
    authorization_note: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    confirmation: Literal["observation_review_update"]


class ActivityAnnotationRequest(_RequestModel):
    provider: str = Field(min_length=1, max_length=64)
    account_ref: str = Field(min_length=1, max_length=128)
    provider_transaction_id: str = Field(min_length=1, max_length=256)
    status: str = Field(min_length=1, max_length=64)
    classification: str | None = Field(default=None, min_length=1, max_length=64)
    order_intent_id: str | None = Field(default=None, min_length=1, max_length=128)
    decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    trade_plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    trade_plan_version: int | None = Field(default=None, ge=1)
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, min_length=1, max_length=2_000)
    expected_version: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    authorization_note: str = Field(min_length=1, max_length=4_000)


class TradeCycleOverrideRequest(_RequestModel):
    root_cycle_id: str = Field(min_length=1, max_length=160)
    operation: Literal["SPLIT", "MERGE", "RELINK"]
    cycle_ids: tuple[str, ...]
    activity_ids: tuple[str, ...] = ()
    split_groups: tuple[tuple[str, ...], ...] = ()
    target_cycle_id: str | None = Field(default=None, min_length=1, max_length=160)
    note: str | None = Field(default=None, min_length=1, max_length=2_000)
    expected_version: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    authorization_note: str = Field(min_length=1, max_length=4_000)


class BehaviorReviewRequest(_RequestModel):
    period_kind: Literal["WEEKLY", "MONTHLY", "QUARTERLY"]
    period_start: datetime
    period_end: datetime
    strategy_code: str | None = Field(default="strategy_v1", min_length=1, max_length=128)
    instrument_ids: tuple[str, ...] = ()
    cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    retro_run_ids: tuple[str, ...] = ()
    retro_review_ids: tuple[str, ...] = ()
    review_item_source_keys: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()
    action_items: tuple[dict[str, Any], ...] = ()
    source_read_complete: bool = True
    source_error_code: str | None = Field(default=None, min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=200)


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


class ConsoleReviewItemTransitionRequest(_RequestModel):
    status: Literal["ACKNOWLEDGED", "RESOLVED"]
    expected_version: int = Field(ge=1)
    resolution_note: str | None = Field(default=None, min_length=1, max_length=2_000)
    resolution_ref: str | None = Field(default=None, min_length=1, max_length=256)
    due_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    authorization_note: str = Field(min_length=1, max_length=4_000)
    confirmation: Literal["review_item_update"]

    @model_validator(mode="after")
    def require_resolution_note(self) -> ConsoleReviewItemTransitionRequest:
        if self.status == "RESOLVED" and self.resolution_note is None:
            raise ValueError("RESOLVED ReviewItem requires resolution_note")
        return self


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
    preserve_full_result: bool = False,
) -> Any:
    try:
        if preserve_full_result:
            return await _registry(request).invoke_uncompacted(
                tool_name,
                arguments,
                confirmation=confirmation,
            )
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
    return cast(
        dict[str, Any],
        await _invoke_capability(request, "system_health", {}, preserve_full_result=True),
    )


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
        preserve_full_result=payload.preserve_full_result,
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
                preserve_full_result=True,
            )
        ),
        "runs": _canonical_subject_transport(
            await _invoke_capability(
                request,
                "monitor_read",
                {"request": {"operation": "runs", "limit": run_limit}},
                preserve_full_result=True,
            )
        ),
        "events": _canonical_subject_transport(
            await _invoke_capability(
                request,
                "monitor_read",
                {"request": {"operation": "events", "limit": event_limit}},
                preserve_full_result=True,
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
            preserve_full_result=True,
        ),
    )


@app.get("/api/portfolio")
async def portfolio(
    request: Request,
    transaction_limit: int = Query(default=500, ge=1, le=1000),
    coverage_limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Build the durable Portfolio Hub from complete validated read capabilities."""

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
    trade_cycles_result = await _durable_console_call(
        request,
        "portfolio_analyze",
        {"request": {"operation": "trade_cycles", "limit": 200}},
    )
    performance_now = _container(request).context.clock.now()
    performance_series_result = await _durable_console_call(
        request,
        "portfolio_analyze",
        {
            "request": {
                "operation": "performance_series",
                "start": performance_now.replace(
                    month=1,
                    day=1,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                ),
                "end": performance_now,
            }
        },
    )
    daily_equity_result = await _durable_console_call(
        request,
        "portfolio_analyze",
        {"request": {"operation": "daily_equity", "limit": 500}},
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
        "trade_cycles": trade_cycles_result,
        "performance_series": performance_series_result,
        "daily_equity": daily_equity_result,
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
            preserve_full_result=True,
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

    return failure_payload(code, _sanitized_error(request, error))


def _console_row(item: Any, *, href: str) -> dict[str, Any]:
    payload = console_attention_payload(item)
    payload["href"] = href
    return payload


def _workflow_attention_items(
    *,
    agenda: dict[str, Any],
    retro: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project every item from already-bounded durable workflow responses."""

    items: list[dict[str, Any]] = []
    agenda_data = agenda.get("data") if isinstance(agenda, dict) else None
    agenda_items = agenda_data.get("items") if isinstance(agenda_data, dict) else None
    for value in agenda_items if isinstance(agenda_items, list) else ():
        if not isinstance(value, dict):
            continue
        item_id = value.get("agenda_item_id")
        if not isinstance(item_id, str) or not item_id:
            continue
        limitation_codes = value.get("limitation_codes")
        projected = project_agenda_overdue_fields(
            agenda_item_id=item_id,
            title=str(value.get("title") or item_id),
            limitation_codes=(
                tuple(str(code) for code in limitation_codes)
                if isinstance(limitation_codes, list)
                else ()
            ),
            subject_id=(
                str(value["subject_id"])
                if isinstance(value.get("subject_id"), str) and value["subject_id"]
                else None
            ),
        )
        if projected is not None:
            items.append(_console_row(projected, href=f"/agenda#agenda-{item_id}"))

    retro_data = retro.get("data") if isinstance(retro, dict) else None
    retro_runs = retro_data.get("runs") if isinstance(retro_data, dict) else None
    for value in retro_runs if isinstance(retro_runs, list) else ():
        if not isinstance(value, dict):
            continue
        run_id = value.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        latest_review = value.get("latest_review")
        review_status = (
            str(latest_review.get("status", "")).upper()
            if isinstance(latest_review, dict)
            else "UNREVIEWED"
        )
        findings = value.get("findings")
        subject_ids_value = value.get("subject_ids")
        subject_ids = tuple(
            dict.fromkeys(
                subject_id
                for subject_id in (
                    subject_ids_value if isinstance(subject_ids_value, (list, tuple)) else ()
                )
                if isinstance(subject_id, str) and subject_id
            )
        )
        action_items = (
            latest_review.get("action_items") if isinstance(latest_review, dict) else None
        )
        for projected in project_trade_retro_fields(
            run_id=run_id,
            finding_count=len(findings) if isinstance(findings, list) else 0,
            review_status=review_status,
            action_items=tuple(action_items) if isinstance(action_items, list) else (),
            subject_ids=subject_ids,
        ):
            items.append(_console_row(projected, href=f"/retro#retro-{run_id}"))

    return items


def _decision_review_attention_items(decisions: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Project due Decisions into stable ReviewItem source rows.

    The decision id is both the durable source reference and the stable key
    suffix.  A later exact superseder is intentionally handled by the
    repository query, so this projector only receives still-live Decisions.
    """

    items: list[dict[str, Any]] = []
    for decision in decisions:
        decision_id = str(getattr(decision, "decision_id", ""))
        subject_id = str(getattr(decision, "subject_id", ""))
        due_at = getattr(decision, "review_due_at", None)
        if not decision_id or not subject_id or not isinstance(due_at, datetime):
            continue
        title = str(getattr(decision, "title", "Decision review"))
        items.append(
            {
                "key": f"decision-review-due-{decision_id}",
                "source_type": ReviewItemSourceType.DECISION_REVIEW_DUE.value,
                "source_ref": decision_id,
                "subject_id": subject_id,
                "title": f"Decision review due · {title}",
                "detail": (
                    "The Decision review deadline has passed and no later exact "
                    "superseding Decision was recorded."
                ),
                "severity": "ATTENTION",
                "recommended_action": "REVIEW_DECISION",
                "href": (
                    f"/decision-workbench?subject_id={subject_id}"
                    f"&capture=decision&supersedes_decision_id={decision_id}"
                ),
                "due_at": due_at.isoformat(),
            }
        )
    return items


def _operational_attention_items(
    *,
    agent_actions: tuple[Any, ...] = (),
    broker_intents: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Render unresolved execution-adjacent state without attempting recovery."""

    items: list[dict[str, Any]] = []
    for action in agent_actions:
        action_id = str(getattr(action, "action_id", ""))
        if not action_id:
            continue
        status = str(getattr(getattr(action, "status", None), "value", "UNKNOWN"))
        items.append(
            _console_row(
                project_unresolved_agent_fields(
                    action_id=action_id,
                    status=status,
                    capability=str(getattr(action, "capability", "Agent action")),
                    operation=str(getattr(action, "operation", "")),
                ),
                href="/chat",
            )
        )
    for intent in broker_intents:
        intent_id = str(getattr(intent, "order_intent_id", ""))
        if not intent_id:
            continue
        items.append(
            _console_row(
                project_unresolved_broker(
                    order_intent_id=intent_id,
                    status=str(getattr(intent, "status", "UNKNOWN")),
                    symbol=str(
                        getattr(intent, "symbol", None) or getattr(intent, "instrument_id", "Order")
                    ),
                    provider_status=getattr(intent, "provider_status", None),
                ),
                href="/capabilities",
            )
        )
    return items


def _scorecard_attention_items(scorecards: dict[str, Any]) -> list[dict[str, Any]]:
    """Project latest Scorecard gaps and label consecutive recurrence."""

    data = scorecards.get("data") if scorecards.get("ok") is True else None
    runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for value in runs:
        if not isinstance(value, dict):
            continue
        thesis_id = value.get("thesis_id")
        if isinstance(thesis_id, str) and thesis_id:
            grouped.setdefault(thesis_id, []).append(value)

    items: list[dict[str, Any]] = []

    def gap_codes(run: dict[str, Any] | None) -> set[str]:
        dimensions = run.get("dimensions") if isinstance(run, dict) else None
        return {
            str(item.get("code"))
            for item in (dimensions if isinstance(dimensions, list) else ())
            if isinstance(item, dict)
            and (
                str(item.get("status", "")).upper() != "EVALUATED"
                or str(item.get("result_code", "")).upper() in {"FAIL", "PARTIAL", "NOT_EVALUATED"}
            )
            and item.get("code")
        }

    for thesis_id, thesis_runs in grouped.items():
        ordered = sorted(
            thesis_runs,
            key=lambda item: (str(item.get("generated_at", "")), str(item.get("scorecard_id", ""))),
            reverse=True,
        )
        latest = ordered[0]
        previous_gaps = gap_codes(ordered[1] if len(ordered) > 1 else None)
        dimensions = latest.get("dimensions")
        for dimension in dimensions if isinstance(dimensions, list) else ():
            if not isinstance(dimension, dict):
                continue
            code = str(dimension.get("code", ""))
            if code not in gap_codes(latest):
                continue
            persistent = code in previous_gaps
            subject_id = latest.get("subject_id")
            items.append(
                {
                    "key": f"scorecard-gap-{thesis_id}-{code}",
                    "severity": "ATTENTION",
                    "title": f"{'Persistent' if persistent else 'New'} Scorecard gap · "
                    f"{dimension.get('title') or code}",
                    "detail": (
                        f"{dimension.get('summary') or dimension.get('result_code') or code} · "
                        f"Thesis revision v{latest.get('thesis_revision_no') or '—'}"
                    ),
                    "href": (
                        f"/scorecards?subject_id={subject_id}#scorecard-"
                        f"{latest.get('scorecard_id')}"
                        if isinstance(subject_id, str) and subject_id
                        else f"/scorecards#scorecard-{latest.get('scorecard_id')}"
                    ),
                    "source_type": "SCORECARD_GAP",
                    # A newer scorecard changes the display anchor, not the
                    # durable issue scope.  Thesis is the authoritative source.
                    "source_ref": thesis_id,
                    "subject_id": subject_id if isinstance(subject_id, str) else None,
                    "recommended_action": "REVIEW_SCORECARD_GAP",
                }
            )
    return items


def _authoritative_review_refs(
    *,
    agenda: dict[str, Any] | None = None,
    retro: dict[str, Any] | None = None,
    scorecards: dict[str, Any] | None = None,
) -> frozenset[tuple[ReviewItemSourceType, str]]:
    """Return exact source objects whose current gaps were fully inspected.

    A paginated response is never authoritative for objects omitted from that
    page.  Each object that is present, however, can safely close its own stale
    projections when its current representation contains no matching gap.
    """

    values: set[tuple[ReviewItemSourceType, str]] = set()
    agenda_data = agenda.get("data") if isinstance(agenda, dict) else None
    agenda_items = agenda_data.get("items") if isinstance(agenda_data, dict) else None
    for item in agenda_items if isinstance(agenda_items, list) else ():
        source_ref = item.get("agenda_item_id") if isinstance(item, dict) else None
        if isinstance(source_ref, str) and source_ref:
            values.add((ReviewItemSourceType.CATALYST_AGENDA, source_ref))

    retro_data = retro.get("data") if isinstance(retro, dict) else None
    retro_runs = retro_data.get("runs") if isinstance(retro_data, dict) else None
    for item in retro_runs if isinstance(retro_runs, list) else ():
        source_ref = item.get("run_id") if isinstance(item, dict) else None
        if isinstance(source_ref, str) and source_ref:
            values.add((ReviewItemSourceType.TRADE_RETRO, source_ref))

    scorecard_data = scorecards.get("data") if isinstance(scorecards, dict) else None
    scorecard_runs = scorecard_data.get("runs") if isinstance(scorecard_data, dict) else None
    for item in scorecard_runs if isinstance(scorecard_runs, list) else ():
        if not isinstance(item, dict):
            continue
        for field in ("thesis_id", "scorecard_id"):
            source_ref = item.get(field)
            if isinstance(source_ref, str) and source_ref:
                values.add((ReviewItemSourceType.SCORECARD_GAP, source_ref))
    return frozenset(values)


def _review_item_projection(item: dict[str, Any]) -> ReviewItemProjection | None:
    try:
        source_type = ReviewItemSourceType(str(item["source_type"]))
        severity_wire = str(item.get("severity", "ATTENTION")).upper()
        severity = (
            ReviewItemSeverity.ERROR
            if severity_wire == "ERROR"
            else ReviewItemSeverity.INFO
            if severity_wire in {"INFO", "OBSERVE"}
            else ReviewItemSeverity.ATTENTION
        )
        return ReviewItemProjection(
            source_key=str(item["key"]),
            source_type=source_type,
            source_ref=str(item["source_ref"]),
            subject_id=(
                str(item["subject_id"])
                if isinstance(item.get("subject_id"), str) and item["subject_id"]
                else None
            ),
            title=str(item["title"]),
            detail=str(item["detail"]),
            severity=severity,
            recommended_action=str(item["recommended_action"]),
            href=str(item["href"]),
            due_at=(
                datetime.fromisoformat(str(item["due_at"]))
                if item.get("due_at") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _reconcile_review_items(
    request: Request,
    *,
    agenda: dict[str, Any] | None = None,
    retro: dict[str, Any] | None = None,
    scorecards: dict[str, Any] | None = None,
) -> tuple[Any, ...]:
    """Materialize only successfully observed durable sources.

    A failed envelope is excluded from ``observed_source_types`` so an outage
    can never auto-resolve an existing item.
    """

    if agenda is None or retro is None or scorecards is None:
        fetched_agenda, fetched_retro, fetched_scorecards = await asyncio.gather(
            _durable_console_call(
                request,
                "research_memory_get",
                {
                    "request": {
                        "operation": "agenda",
                        "window_days": 90,
                        "include_history": False,
                        "limit": 200,
                        "offset": 0,
                    }
                },
            ),
            _durable_console_call(
                request,
                "portfolio_analyze",
                {"request": {"operation": "retro_history", "limit": 50}},
            ),
            _durable_console_call(
                request,
                "research_judgment_get",
                {"request": {"operation": "scorecard_history", "limit": 50, "offset": 0}},
            ),
        )
        agenda = agenda or fetched_agenda
        retro = retro or fetched_retro
        scorecards = scorecards or fetched_scorecards

    observed: set[ReviewItemSourceType] = set()
    authoritative_refs: set[tuple[ReviewItemSourceType, str]] = set()
    fully_observed: set[ReviewItemSourceType] = set()
    raw_items: list[dict[str, Any]] = []
    if agenda.get("ok") is True:
        observed.add(ReviewItemSourceType.CATALYST_AGENDA)
        raw_items.extend(_workflow_attention_items(agenda=agenda, retro={}))
        authoritative_refs.update(_authoritative_review_refs(agenda=agenda))
    if retro.get("ok") is True:
        observed.add(ReviewItemSourceType.TRADE_RETRO)
        raw_items.extend(_workflow_attention_items(agenda={}, retro=retro))
        authoritative_refs.update(_authoritative_review_refs(retro=retro))
    if scorecards.get("ok") is True:
        observed.add(ReviewItemSourceType.SCORECARD_GAP)
        raw_items.extend(_scorecard_attention_items(scorecards))
        authoritative_refs.update(_authoritative_review_refs(scorecards=scorecards))

    container = _container(request)
    decision_limit = 100
    try:
        due_decisions = container.services.decisions.list_review_due(
            now=container.context.clock.now(),
            limit=decision_limit,
        )
    except Exception:  # noqa: BLE001 - failed reads must not auto-resolve
        due_decisions = ()
    else:
        observed.add(ReviewItemSourceType.DECISION_REVIEW_DUE)
        raw_items.extend(_decision_review_attention_items(due_decisions))
        # A result at the bound may omit a still-live Decision.  Keep the
        # source observed for upserts, but never mark it fully observed.
        if len(due_decisions) < decision_limit:
            fully_observed.add(ReviewItemSourceType.DECISION_REVIEW_DUE)
    observation_limit = 100
    try:
        observation_reviews = container.services.external_note_reviews.pending_projections(
            limit=observation_limit
        )
    except Exception:  # noqa: BLE001 - absence must not resolve prior items
        observation_reviews = ()
    else:
        observed.add(ReviewItemSourceType.OBSERVATION_REVIEW_DUE)
        raw_items.extend(
            {
                "key": item.source_key,
                "source_type": item.source_type.value,
                "source_ref": item.source_ref,
                "subject_id": item.subject_id,
                "title": item.title,
                "detail": item.detail,
                "severity": item.severity.value,
                "recommended_action": item.recommended_action,
                "href": item.href,
                "due_at": item.due_at.isoformat() if item.due_at else None,
            }
            for item in observation_reviews
        )
        if len(observation_reviews) < observation_limit:
            fully_observed.add(ReviewItemSourceType.OBSERVATION_REVIEW_DUE)
    try:
        agent_actions = container.operations.agent_pending_actions.list_unresolved(
            now=container.context.clock.now(), limit=100
        )
    except Exception:  # noqa: BLE001 - absence must not resolve prior items
        agent_actions = ()
    else:
        observed.add(ReviewItemSourceType.AGENT_PENDING_ACTION)
        if len(agent_actions) < 100:
            fully_observed.add(ReviewItemSourceType.AGENT_PENDING_ACTION)
    try:
        broker_intents = container.services.broker_orders.list_unresolved(limit=100)
    except Exception:  # noqa: BLE001 - absence must not resolve prior items
        broker_intents = ()
    else:
        observed.add(ReviewItemSourceType.BROKER_ORDER_INTENT)
        if len(broker_intents) < 100:
            fully_observed.add(ReviewItemSourceType.BROKER_ORDER_INTENT)
    raw_items.extend(
        _operational_attention_items(
            agent_actions=agent_actions,
            broker_intents=broker_intents,
        )
    )
    projections = tuple(
        value for item in raw_items if (value := _review_item_projection(item)) is not None
    )
    return container.services.review_items.reconcile(
        projections,
        observed_source_types=frozenset(observed),
        authoritative_source_refs=frozenset(authoritative_refs),
        fully_observed_source_types=frozenset(fully_observed),
    )


async def _durable_console_call(
    request: Request,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Invoke one complete local read and preserve a per-capability failure envelope."""

    try:
        result = await _invoke_capability(
            request,
            tool_name,
            arguments,
            preserve_full_result=True,
        )
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
    """Read groups plus the active Moomoo ``All`` aggregate through validated tools."""

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

    return failure_payload("CONSOLE_RESEARCH_STATE_READ_FAILED", _sanitized_error(request, error))


def _canonical_subject_transport(value: Any) -> Any:
    """Translate public compatibility ``case_*`` fields for the internal Console BFF."""

    if isinstance(value, dict):
        aliases = {
            "case_id": "subject_id",
            "case_type": "subject_type",
            "linked_case_ids": "linked_subject_ids",
        }
        return {
            aliases.get(key, key): _canonical_subject_transport(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical_subject_transport(item) for item in value]
    return value


@app.get("/api/research")
async def research(request: Request) -> dict[str, Any]:
    """Return every durable Research Subject and its current Thesis state.

    This endpoint deliberately routes through the same validated capability
    registry exposed to MCP. It does not refresh providers, read repositories,
    or create a second research API. Subject listing remains compact and paged at
    the public maximum (200). Each actionable state read preserves its full local
    result so Candidate identities, Conditions, and review details cannot be
    replaced by transport truncation markers.
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
            preserve_full_result=True,
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
                            "include_watchlist": True,
                        }
                    },
                    preserve_full_result=True,
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page lightweight Subject choices and load state only for the selected row."""

    page_size = 200
    offset = 0
    subjects: list[dict[str, Any]] = []
    read_ok = True
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
            read_ok = False
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
    return subjects, {"total": len(subjects), "page_size": page_size, "ok": read_ok}


def _trade_cycle_override_input(
    payload: TradeCycleOverrideRequest,
) -> TradeCycleOverrideAppendInput:
    return TradeCycleOverrideAppendInput.model_validate(
        {**payload.model_dump(), "actor": "user", "algorithm_version": "trade_cycle_v1"}
    )


def _trade_cycle_override_projection(request: Request) -> Any:
    return _container(request).services.account_transactions.project_trade_cycles_for_override(
        TradeCycleQueryInput(limit=500)
    )


@app.post("/api/behavior-reviews")
async def run_behavior_review(request: Request, payload: BehaviorReviewRequest) -> dict[str, Any]:
    try:
        value = _container(request).services.behavior_reviews.run(
            BehaviorReviewRunInput.model_validate(payload.model_dump())
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409 if "IDEMPOTENCY" in error.code else 422,
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return value.model_dump(mode="json")


@app.post("/api/trade-cycle-overrides/preview")
async def preview_trade_cycle_override(
    request: Request, payload: TradeCycleOverrideRequest
) -> dict[str, Any]:
    try:
        value = _container(request).services.trade_cycle_overrides.preview_revision(
            _trade_cycle_override_input(payload),
            projection=_trade_cycle_override_projection(request),
        )
    except (ValidationError, ValueError, TradingPartnerError) as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "INPUT_VALIDATION_ERROR", "message": str(error)},
        ) from None
    return value.model_dump(mode="json")


@app.post("/api/trade-cycle-overrides")
async def append_trade_cycle_override(
    request: Request, payload: TradeCycleOverrideRequest
) -> dict[str, Any]:
    try:
        value = _container(request).services.trade_cycle_overrides.append_revision(
            _trade_cycle_override_input(payload),
            projection=_trade_cycle_override_projection(request),
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=(409 if "VERSION" in error.code or "IDEMPOTENCY" in error.code else 422),
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return value.model_dump(mode="json")


@app.post("/api/activity-annotations")
async def append_activity_annotation(
    request: Request, payload: ActivityAnnotationRequest
) -> dict[str, Any]:
    """Append one explicit activity classification or exact Decision/Plan link."""

    try:
        result = _container(request).services.activity_annotations.append_revision(
            ActivityAnnotationAppendInput.model_validate(
                {
                    **payload.model_dump(),
                    "actor": "user",
                }
            )
        )
    except ValidationError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INPUT_VALIDATION_ERROR",
                "message": "Activity annotation input is invalid",
            },
        ) from None
    except TradingPartnerError as error:
        status_code = (
            409
            if error.code
            in {
                "ACTIVITY_ANNOTATION_VERSION_CONFLICT",
                "IDEMPOTENCY_CONFLICT",
                "DUPLICATE_IDEMPOTENCY_KEY",
            }
            else 422
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return result.model_dump(mode="json")


@app.get("/api/decision-workbench")
async def decision_workbench(
    request: Request,
    subject_id: str | None = Query(default=None, min_length=1, max_length=100),
    classification: str | None = Query(default=None, min_length=1, max_length=64),
    classifications: Annotated[list[str] | None, Query()] = None,
    account_refs: Annotated[list[str] | None, Query()] = None,
    instrument_ids: Annotated[list[str] | None, Query()] = None,
    behavior_start: datetime | None = None,
    behavior_end: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate one durable Research Subject decision loop for the Console.

    The endpoint deliberately performs no Provider refresh and no user/domain
    write. It may refresh the internal ReviewItem materialized projection. It
    loads state for only the selected Subject, then reads the independent
    Monitor, Agenda, account, transaction, Retro, and Scorecard sections
    concurrently. Each section retains its own envelope so one failed read
    cannot blank the workflow.
    """

    subjects, subject_list = await _console_subject_choices(
        request,
        include_archived=False,
        selected_subject_id=subject_id,
    )
    selected_subject_id = subject_id

    selected = next(
        (
            item
            for item in subjects
            if isinstance(item.get("subject"), dict)
            and item["subject"].get("subject_id") == selected_subject_id
        ),
        None,
    )
    if selected is not None and selected.get("state") is None:
        selected["state"] = _canonical_subject_transport(
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

    agenda_request: dict[str, Any] = {
        "operation": "agenda",
        "window_days": 90,
        "include_history": False,
        "limit": 100,
        "offset": 0,
    }
    scorecard_request: dict[str, Any] = {
        "operation": "scorecard_history",
        "limit": 20,
        "offset": 0,
    }
    if selected_subject_id is not None:
        agenda_request["filters"] = {"case_ids": [selected_subject_id]}
        scorecard_request["case_id"] = selected_subject_id
    timeline_read = (
        _durable_console_call(
            request,
            "research_memory_get",
            {
                "request": {
                    "operation": "timeline",
                    "case_id": selected_subject_id,
                    "entity_types": ["decision", "journal"],
                    "limit": 20,
                }
            },
        )
        if selected_subject_id is not None
        else asyncio.sleep(0, result={"ok": True, "data": {"items": [], "total": 0}})
    )
    cycle_instrument_ids: list[str] = list(instrument_ids or ())
    performance_now = _container(request).context.clock.now()
    if selected is not None:
        state_envelope = selected.get("state")
        state_data = state_envelope.get("data") if isinstance(state_envelope, dict) else None
        current_plan = (
            state_data.get("current_trade_plan") if isinstance(state_data, dict) else None
        )
        instrument_id = (
            current_plan.get("instrument_id") if isinstance(current_plan, dict) else None
        )
        if not isinstance(instrument_id, str) or not instrument_id:
            subject_value = selected.get("subject")
            instrument_id = (
                subject_value.get("primary_instrument_id")
                if isinstance(subject_value, dict)
                else None
            )
        if isinstance(instrument_id, str) and instrument_id and not cycle_instrument_ids:
            cycle_instrument_ids.append(instrument_id)

    (
        monitors_result,
        agenda_result,
        timeline_result,
        accounts_result,
        transactions_result,
        trade_cycles_result,
        daily_equity_result,
        performance_series_result,
        behavior_result,
        retro_result,
        scorecards_result,
    ) = await asyncio.gather(
        _durable_console_call(
            request,
            "monitor_read",
            {"request": {"operation": "dashboard", "status": None}},
        ),
        _durable_console_call(
            request,
            "research_memory_get",
            {"request": agenda_request},
        ),
        timeline_read,
        _durable_console_call(
            request,
            "account_get",
            {"request": {"operation": "positions"}},
        ),
        _durable_console_call(
            request,
            "account_get",
            {"request": {"operation": "transactions", "limit": 500}},
        ),
        _durable_console_call(
            request,
            "portfolio_analyze",
            {
                "request": {
                    "operation": "trade_cycles",
                    "instrument_ids": [],
                    "limit": 500,
                }
            },
        ),
        _durable_console_call(
            request,
            "portfolio_analyze",
            {"request": {"operation": "daily_equity", "limit": 500}},
        ),
        _durable_console_call(
            request,
            "portfolio_analyze",
            {
                "request": {
                    "operation": "performance_series",
                    "start": performance_now.replace(
                        month=1,
                        day=1,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    ),
                    "end": performance_now,
                }
            },
        ),
        _durable_console_call(
            request,
            "portfolio_analyze",
            {
                "request": {
                    "operation": "behavior_summary",
                    "case_id": selected_subject_id,
                    "account_refs": account_refs or [],
                    "instrument_ids": cycle_instrument_ids,
                    "strategy_code": (
                        "strategy_v1" if selected_subject_id is not None else None
                    ),
                    "classifications": classifications or (
                        [classification] if classification else []
                    ),
                    "minimum_sample_size": 3,
                    "start": behavior_start,
                    "end": behavior_end,
                }
            },
        ),
        _durable_console_call(
            request,
            "portfolio_analyze",
            {"request": {"operation": "retro_history", "limit": 50}},
        ),
        _durable_console_call(
            request,
            "research_judgment_get",
            {"request": scorecard_request},
        ),
    )
    sections = {
        "monitors": _canonical_subject_transport(monitors_result),
        "agenda": _canonical_subject_transport(agenda_result),
        "timeline": _canonical_subject_transport(timeline_result),
        "accounts": _canonical_subject_transport(accounts_result),
        "transactions": _canonical_subject_transport(transactions_result),
        "trade_cycles": _canonical_subject_transport(trade_cycles_result),
        "behavior": _canonical_subject_transport(behavior_result),
        "performance_series": _canonical_subject_transport(performance_series_result),
        "daily_equity": _canonical_subject_transport(daily_equity_result),
        "retro": _canonical_subject_transport(retro_result),
        "scorecards": _canonical_subject_transport(scorecards_result),
    }
    if selected is not None and isinstance(selected.get("state"), dict):
        sections["research_state"] = selected["state"]
    partial_failures = [name for name, envelope in sections.items() if envelope.get("ok") is False]
    try:
        annotation_service = _container(request).services.activity_annotations
        activity_annotations = [
            item.model_dump(mode="json") for item in annotation_service.list_annotations(limit=500)
        ]
    except Exception:  # noqa: BLE001 - retain other durable Journal sections
        activity_annotations = []
        partial_failures.append("activity_annotations")
    try:
        order_intents = [
            item.model_dump(mode="json")
            for item in _container(request).services.broker_orders.list_recent(limit=200)
        ]
    except Exception:  # noqa: BLE001 - retain source facts when order history fails
        order_intents = []
        partial_failures.append("order_intents")
    try:
        behavior_review_runs = [
            item.model_dump(mode="json")
            for item in _container(request).services.behavior_reviews.history(limit=50)
        ]
    except Exception:  # noqa: BLE001 - do not hide other review evidence
        behavior_review_runs = []
        partial_failures.append("behavior_reviews")
    if subject_list["ok"] is False:
        partial_failures.insert(0, "research_subjects")
    durable_review_items: tuple[Any, ...] = ()
    if selected_subject_id is not None:
        review_observed: set[ReviewItemSourceType] = set()
        authoritative_refs: set[tuple[ReviewItemSourceType, str]] = set()
        review_fully_observed: set[ReviewItemSourceType] = set()
        review_raw: list[dict[str, Any]] = []
        if agenda_result.get("ok") is True:
            review_observed.add(ReviewItemSourceType.CATALYST_AGENDA)
            review_raw.extend(_workflow_attention_items(agenda=agenda_result, retro={}))
            authoritative_refs.update(_authoritative_review_refs(agenda=agenda_result))
        if retro_result.get("ok") is True:
            review_observed.add(ReviewItemSourceType.TRADE_RETRO)
            review_raw.extend(_workflow_attention_items(agenda={}, retro=retro_result))
            authoritative_refs.update(_authoritative_review_refs(retro=retro_result))
        if scorecards_result.get("ok") is True:
            review_observed.add(ReviewItemSourceType.SCORECARD_GAP)
            review_raw.extend(_scorecard_attention_items(scorecards_result))
            authoritative_refs.update(_authoritative_review_refs(scorecards=scorecards_result))
        decision_limit = 100
        try:
            due_decisions = _container(request).services.decisions.list_review_due(
                now=_container(request).context.clock.now(),
                subject_id=selected_subject_id,
                limit=decision_limit,
            )
        except Exception:  # noqa: BLE001 - failed reads must not auto-resolve
            due_decisions = ()
            partial_failures.append("decision_review_due")
        else:
            review_observed.add(ReviewItemSourceType.DECISION_REVIEW_DUE)
            review_raw.extend(_decision_review_attention_items(due_decisions))
            if len(due_decisions) < decision_limit:
                review_fully_observed.add(ReviewItemSourceType.DECISION_REVIEW_DUE)
        scoped_projections = tuple(
            projection
            for item in review_raw
            if (projection := _review_item_projection(item)) is not None
            and projection.subject_id == selected_subject_id
        )
        try:
            _container(request).services.review_items.reconcile(
                scoped_projections,
                observed_source_types=frozenset(review_observed),
                authoritative_source_refs=frozenset(authoritative_refs),
                fully_observed_source_types=frozenset(review_fully_observed),
                subject_scope=selected_subject_id,
            )
            durable_review_items = _container(request).services.review_items.list_open(
                subject_id=selected_subject_id,
                limit=500,
            )
        except Exception:  # noqa: BLE001 - other durable stages remain readable
            partial_failures.append("review_items")
    else:
        try:
            durable_review_items = _container(request).services.review_items.list_open(
                subject_id=None,
                limit=500,
            )
        except Exception:  # noqa: BLE001 - retain other durable stages when the queue fails
            partial_failures.append("review_items")
    return {
        "selected_subject_id": selected_subject_id if selected is not None else None,
        "subjects": subjects,
        "subject_list": subject_list,
        **{name: value for name, value in sections.items() if name != "research_state"},
        "partial_failures": partial_failures,
        "review_items": [item.model_dump(mode="json") for item in durable_review_items],
        "activity_annotations": activity_annotations,
        "order_intents": order_intents,
        "behavior_review_runs": behavior_review_runs,
        "review_item_metrics": (
            _container(request)
            .services.review_items.metrics(subject_id=selected_subject_id)
            .model_dump(mode="json")
            if "review_items" not in partial_failures
            else None
        ),
        "review_item_history": [
            item.model_dump(mode="json")
            for item in (
                _container(request).services.review_items.list_recent(
                    subject_id=selected_subject_id,
                    limit=20,
                )
                if "review_items" not in partial_failures
                else ()
            )
        ],
    }


def _start_observation_analysis(
    request: Request,
    operation: Callable[[], Awaitable[object]],
    *,
    note_revision_id: str | None = None,
) -> bool:
    """Start one supervised analysis task and retain only closed error codes."""

    task = getattr(request.app.state, "observation_analysis_task", None)
    if task is not None and not task.done():
        return False
    errors: dict[str, str] = request.app.state.observation_analysis_errors
    error_key = note_revision_id or "__batch__"
    errors.pop(error_key, None)

    async def run() -> None:
        try:
            await operation()
        except TradingPartnerError as error:
            errors[error_key] = error.code
        except Exception:  # noqa: BLE001 - never retain private payload or exception text
            errors[error_key] = "OBSERVATION_ANALYSIS_UNEXPECTED"

    request.app.state.observation_analysis_task = asyncio.create_task(run())
    return True


async def _analyze_pending_observations(request: Request) -> None:
    await _container(request).services.external_notes.analyze_pending(limit=20)


async def _analyze_one_observation(
    request: Request,
    note_revision_id: str,
    *,
    retry_failed: bool,
) -> None:
    services = _container(request).services
    await services.external_notes.analyze_revision(
        note_revision_id,
        retry_failed=retry_failed,
    )


def _observation_inbox_payload(request: Request, *, limit: int) -> dict[str, Any]:
    services = _container(request).services
    service = services.external_notes
    sources = [asdict(item) for item in service.source_capabilities()]
    notes = [
        {
            "identity": asdict(item.identity),
            "revision": {
                **asdict(item.revision),
                "blocks": [
                    asdict(block)
                    for block in (
                        attributed_blocks(item.revision.full_body)
                        if item.revision.full_body
                        else item.revision.blocks
                    )
                ],
            },
            "interpretation": (
                {
                    **asdict(item.interpretation),
                    "payload": json.loads(item.interpretation.payload_json),
                }
                if item.interpretation is not None
                else None
            ),
            "review": (
                review.model_dump(mode="json")
                if (
                    review := services.external_note_reviews.get_for_revision(
                        item.revision.note_revision_id
                    )
                )
                is not None
                else None
            ),
        }
        for item in service.inbox(limit=limit)
    ]
    return {
        "external_notes": jsonable_encoder(notes),
        "observation_sources": jsonable_encoder(sources),
    }


@app.get("/api/observations")
async def observation_inbox(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    """Load private note bodies only when the Journal Notes tab requests them."""

    try:
        return {"data": _observation_inbox_payload(request, limit=limit)}
    except Exception as error:  # noqa: BLE001 - retain a closed, secret-safe failure
        raise HTTPException(
            status_code=422,
            detail=_sanitized_error(request, error),
        ) from None


async def _sync_observations(
    request: Request,
    *,
    source_code: str | None,
    analyze: bool,
) -> dict[str, Any]:
    service = _container(request).services.external_notes
    receipt = await service.sync(analyze=analyze, source_code=source_code)
    analysis_started = False
    if not analyze:
        analysis_started = _start_observation_analysis(
            request,
            lambda: _analyze_pending_observations(request),
        )
    return {
        "data": {
            **jsonable_encoder(asdict(receipt)),
            "analysis_started": analysis_started,
            "source_code": source_code,
        }
    }


@app.get("/api/observations/sources")
async def observation_sources(request: Request) -> dict[str, Any]:
    return {
        "items": [
            asdict(item)
            for item in _container(request).services.external_notes.source_capabilities()
        ]
    }


@app.post("/api/observations/sync")
async def observation_sync(
    request: Request,
    payload: ObservationSyncRequest,
) -> dict[str, Any]:
    return await _sync_observations(
        request,
        source_code=payload.source_code,
        analyze=payload.analyze,
    )


@app.post("/api/observations/import")
async def observation_import(
    request: Request,
    payload: ObservationCaptureRequest,
) -> dict[str, Any]:
    service = _container(request).services.external_notes
    receipt = await service.capture(
        ExternalObservationCaptureRequest(
            source_code=payload.source_code,
            external_id=payload.external_id,
            title=payload.title,
            full_body=payload.full_body,
            observed_at=payload.observed_at,
            summary=payload.summary,
            source_timestamp=payload.source_timestamp,
            primary_instrument_id=payload.primary_instrument_id,
            related_provider_stock_ids=payload.related_provider_stock_ids,
            related_provider_codes=payload.related_provider_codes,
            visibility=payload.visibility,
        ),
        analyze=payload.analyze,
    )
    analysis_started = False
    if not payload.analyze:
        analysis_started = _start_observation_analysis(
            request,
            lambda: _analyze_pending_observations(request),
        )
    return {
        "data": {
            **jsonable_encoder(asdict(receipt)),
            "source_code": payload.source_code,
            "analysis_started": analysis_started,
        }
    }


@app.post("/api/observations/{note_revision_id}/analyze")
async def observation_analyze(
    request: Request,
    note_revision_id: str,
    payload: ObservationAnalysisRequest,
) -> dict[str, Any]:
    task = getattr(request.app.state, "observation_analysis_task", None)
    if task is not None and not task.done():
        return {"data": {"analysis_started": False, "reason": "ANALYSIS_ALREADY_RUNNING"}}
    started = _start_observation_analysis(
        request,
        lambda: _analyze_one_observation(
            request,
            note_revision_id,
            retry_failed=payload.retry_failed,
        ),
        note_revision_id=note_revision_id,
    )
    return {"data": {"analysis_started": started, "note_revision_id": note_revision_id}}


@app.get("/api/observations/{note_revision_id}/analysis")
async def observation_analysis_status(
    request: Request,
    note_revision_id: str,
) -> dict[str, Any]:
    interpretation = _container(request).services.external_notes.interpretation_for_revision(
        note_revision_id
    )
    errors: dict[str, str] = request.app.state.observation_analysis_errors
    return {
        "data": {
            "note_revision_id": note_revision_id,
            "status": (
                interpretation.status
                if interpretation is not None
                else "FAILED" if note_revision_id in errors else "PENDING"
            ),
            "error_code": (
                interpretation.error_code
                if interpretation is not None
                else errors.get(note_revision_id)
            ),
        }
    }


@app.post("/api/observations/{note_revision_id}/review/ensure")
async def observation_review_ensure(
    request: Request,
    note_revision_id: str,
    payload: ObservationReviewEnsureRequest,
) -> dict[str, Any]:
    try:
        value = _container(request).services.external_note_reviews.ensure_pending(
            note_revision_id=note_revision_id,
            subject_id=payload.subject_id,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return {"data": value.model_dump(mode="json")}


@app.get("/api/observations/{note_revision_id}/review")
async def observation_view_review(
    request: Request,
    note_revision_id: str,
) -> dict[str, Any]:
    try:
        value = _container(request).services.view_reviews.get(note_revision_id)
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return {"data": value.model_dump(mode="json")}


@app.get("/api/current-view")
async def current_view(
    request: Request,
    subject_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    try:
        value = _container(request).services.view_reviews.current(subject_id)
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return {"data": value.model_dump(mode="json") if value is not None else None}


@app.post("/api/observation-reviews/{review_id}")
async def observation_review_transition(
    request: Request,
    review_id: str,
    payload: ObservationReviewTransitionRequest,
) -> dict[str, Any]:
    try:
        value = _container(request).services.external_note_reviews.transition(
            ExternalNoteReviewTransitionInput(
                review_id=review_id,
                status=payload.status,
                expected_version=payload.expected_version,
                subject_id=payload.subject_id,
                decision_id=payload.decision_id,
                due_at=payload.due_at,
                actor="user",
                authorization_note=payload.authorization_note,
                idempotency_key=payload.idempotency_key,
            )
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return {"data": value.model_dump(mode="json")}


@app.get("/api/observations/{note_id}/history")
async def observation_history(
    request: Request,
    note_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    items = _container(request).services.external_notes.history(note_id, limit)
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        older = items[index + 1].revision if index + 1 < len(items) else None
        current_text = item.revision.full_body or item.revision.summary
        older_text = (older.full_body or older.summary) if older is not None else ""
        added_lines, removed_lines = _bounded_observation_diff(older_text, current_text)
        interpretation_payload: dict[str, Any] = {}
        if item.interpretation is not None and item.interpretation.status == "SUCCEEDED":
            try:
                raw_payload = json.loads(item.interpretation.payload_json)
                if isinstance(raw_payload, dict):
                    interpretation_payload = {
                        "change_relation": raw_payload.get("change_relation"),
                        "material_change_summary": raw_payload.get(
                            "material_change_summary"
                        ),
                    }
            except (TypeError, ValueError):
                interpretation_payload = {}
        result.append(
            {
                "revision": {
                    "note_revision_id": item.revision.note_revision_id,
                    "version": item.revision.version,
                    "coverage": item.revision.coverage.value,
                    "source_timestamp": item.revision.source_timestamp,
                    "observed_at": item.revision.observed_at,
                    "content_sha256": item.revision.content_sha256,
                    "text_length": len(current_text),
                    "added_lines": added_lines,
                    "removed_lines": removed_lines,
                },
                "interpretation": (
                    {
                        "status": item.interpretation.status,
                        "error_code": item.interpretation.error_code,
                        **interpretation_payload,
                    }
                    if item.interpretation is not None
                    else None
                ),
            }
        )
    return {"data": {"note_id": note_id, "items": jsonable_encoder(result)}}


def _bounded_observation_diff(
    older: str,
    current: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    older_lines = tuple(line.strip() for line in older.splitlines() if line.strip())
    current_lines = tuple(line.strip() for line in current.splitlines() if line.strip())
    matcher = SequenceMatcher(a=older_lines, b=current_lines, autojunk=False)
    added: list[str] = []
    removed: list[str] = []
    for tag, older_start, older_end, current_start, current_end in matcher.get_opcodes():
        if tag in {"insert", "replace"}:
            added.extend(current_lines[current_start:current_end])
        if tag in {"delete", "replace"}:
            removed.extend(older_lines[older_start:older_end])
    def bound(values: list[str]) -> tuple[str, ...]:
        return tuple(value[:500] for value in values[:5])

    return bound(added), bound(removed)


@app.post("/api/moomoo-notes/sync")
async def moomoo_notes_sync(
    request: Request,
    payload: MoomooNotesSyncRequest,
) -> dict[str, Any]:
    """Compatibility route for pre-source-hub Console builds."""

    return await _sync_observations(
        request,
        source_code="MOOMOO_NOTE",
        analyze=payload.analyze,
    )


@app.get("/api/review-items")
async def review_items(
    request: Request,
    subject_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=500),
    include_resolved: bool = False,
) -> dict[str, Any]:
    """Refresh deterministic durable projections and return unresolved items."""

    await _reconcile_review_items(request)
    service = _container(request).services.review_items
    items = (
        service.list_recent(subject_id=subject_id, limit=limit)
        if include_resolved
        else service.list_open(subject_id=subject_id, limit=limit)
    )
    metrics = service.metrics(subject_id=subject_id)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": (
            metrics.total_items
            if include_resolved
            else metrics.open_count + metrics.acknowledged_count
        ),
        "page_count": len(items),
        "metrics": metrics.model_dump(mode="json"),
    }


@app.post("/api/review-items/{review_item_id}/transition")
def transition_review_item(
    review_item_id: str,
    payload: ConsoleReviewItemTransitionRequest,
    request: Request,
) -> dict[str, Any]:
    """Apply one explicit, versioned human acknowledgement or resolution."""

    try:
        item = _container(request).services.review_items.transition(
            ReviewItemTransitionInput(
                review_item_id=review_item_id,
                status=payload.status,
                expected_version=payload.expected_version,
                actor="user",
                authorization_note=payload.authorization_note,
                resolution_note=payload.resolution_note,
                resolution_ref=payload.resolution_ref,
                due_at=payload.due_at,
                idempotency_key=payload.idempotency_key,
            )
        )
    except TradingPartnerError as error:
        status_code = 404 if error.code == "REVIEW_ITEM_NOT_FOUND" else 409
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code, "message": _sanitized_error(request, error)},
        ) from None
    return {"item": item.model_dump(mode="json")}


@app.get("/api/scorecards")
async def scorecards(
    request: Request,
    subject_id: str | None = Query(default=None, min_length=1, max_length=100),
    thesis_id: str | None = Query(default=None, min_length=1, max_length=100),
    limit: int = Query(default=50, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return Research Subjects plus immutable Judgment Scorecard history.

    Both reads stay behind the validated capability registry and remain durable-only.
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
    receipt = await _container(request).operations.catalyst_agenda_notifications.enqueue_daily(
        window_days=payload.window_days
    )
    return {"data": jsonable_encoder(receipt)}


@app.get("/api/operations")
async def operations(request: Request) -> dict[str, Any]:
    services = _container(request).operations
    health = await _invoke_capability(
        request,
        "system_health",
        {},
        preserve_full_result=True,
    )
    monitor_dashboard = await _invoke_capability(
        request,
        "monitor_read",
        {"request": {"operation": "dashboard", "status": None}},
        preserve_full_result=True,
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
        preserve_full_result=True,
    )
    subject_data = subject_page.get("data") if isinstance(subject_page, dict) else None
    subject_items = subject_data.get("items") if isinstance(subject_data, dict) else None
    valid_subjects = tuple(
        subject
        for subject in (subject_items if isinstance(subject_items, list) else ())
        if isinstance(subject, dict) and isinstance(subject.get("case_id"), str)
    )
    subject_read_slots = asyncio.Semaphore(8)

    async def read_subject_state(subject: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        async with subject_read_slots:
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
        return subject, state

    subject_states_task = asyncio.gather(
        *(read_subject_state(subject) for subject in valid_subjects)
    )
    agenda_task = _durable_console_call(
        request,
        "research_memory_get",
        {
            "request": {
                "operation": "agenda",
                "window_days": 90,
                "include_history": False,
                "limit": 200,
                "offset": 0,
            }
        },
    )
    retro_task = _durable_console_call(
        request,
        "portfolio_analyze",
        {"request": {"operation": "retro_history", "limit": 50}},
    )
    scorecard_task = _durable_console_call(
        request,
        "research_judgment_get",
        {"request": {"operation": "scorecard_history", "limit": 50, "offset": 0}},
    )
    subject_states, agenda_summary, retro_history, scorecard_history = await asyncio.gather(
        subject_states_task,
        agenda_task,
        retro_task,
        scorecard_task,
    )
    for subject, state in subject_states:
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
    broker_unresolved = container.services.broker_orders.list_unresolved(limit=50)
    agent_unresolved_repository = container.operations.agent_pending_actions
    agent_unresolved = (
        agent_unresolved_repository.list_unresolved(now=container.context.clock.now(), limit=50)
        if hasattr(agent_unresolved_repository, "list_unresolved")
        else ()
    )
    workflow_attention = _workflow_attention_items(
        agenda=agenda_summary,
        retro=retro_history,
    )
    workflow_attention.extend(
        _operational_attention_items(
            agent_actions=agent_unresolved,
            broker_intents=broker_unresolved,
        )
    )
    workflow_attention.extend(_scorecard_attention_items(scorecard_history))
    review_items_error: dict[str, Any] | None = None
    try:
        await _reconcile_review_items(
            request,
            agenda=agenda_summary,
            retro=retro_history,
            scorecards=scorecard_history,
        )
        durable_review_items = container.services.review_items.list_open(limit=100)
    except Exception as error:  # noqa: BLE001 - inbox remains usable from live projection
        durable_review_items = ()
        review_items_error = _console_failure(
            request,
            error,
            "CONSOLE_REVIEW_ITEM_RECONCILE_FAILED",
        )
    projected_workflow_attention = workflow_attention
    if review_items_error is None:
        # The durable Review Queue owns these same workflow gaps once
        # reconciliation succeeds. Keep the Decision Inbox free of duplicate
        # cards; the raw projection remains available for diagnostics/fallback.
        workflow_attention = []
    health, monitor_dashboard, recent_runs = await asyncio.gather(
        _invoke_capability(request, "system_health", {}, preserve_full_result=True),
        _invoke_capability(
            request,
            "monitor_read",
            {"request": {"operation": "dashboard", "status": None}},
            preserve_full_result=True,
        ),
        _invoke_capability(
            request,
            "monitor_read",
            {"request": {"operation": "runs", "limit": 20}},
            preserve_full_result=True,
        ),
    )
    return {
        "health": health,
        "monitor_dashboard": _canonical_subject_transport(monitor_dashboard),
        "recent_runs": _canonical_subject_transport(recent_runs),
        "post_market_sync": container.operations.post_market_sync.status().model_dump(mode="json"),
        "notifications": container.operations.notifications.status().model_dump(mode="json"),
        "maintenance": container.operations.maintenance.status().model_dump(mode="json"),
        "capability_count": len(MCP_VNEXT_TOOL_NAMES),
        "research_attention": _canonical_subject_transport(research_attention),
        "workflow_attention": _canonical_subject_transport(workflow_attention),
        "workflow_attention_projection": _canonical_subject_transport(projected_workflow_attention),
        "review_items": [item.model_dump(mode="json") for item in durable_review_items],
        "review_item_metrics": (
            container.services.review_items.metrics().model_dump(mode="json")
            if review_items_error is None
            else None
        ),
        "review_item_history": [
            item.model_dump(mode="json")
            for item in (
                container.services.review_items.list_recent(limit=50)
                if review_items_error is None
                else ()
            )
        ],
        "review_items_error": review_items_error,
        "agenda_summary": _canonical_subject_transport(agenda_summary),
    }
