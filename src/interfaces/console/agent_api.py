"""Loopback Console adapter for the Shared Agent Runtime.

This module owns only the HTTP/channel boundary.  The model loop, operation
policy, durable conversation store, and provider routing remain behind their
application ports.  ``tp_*`` names are private model tools and never become
FastAPI or MCP routes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from application.dto.agent import (
    EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS,
    EPHEMERAL_CONTEXT_MAX_BYTES,
    EPHEMERAL_CONTEXT_PATH_MAX_CHARS,
    EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS,
    AgentTurnEvent,
    AgentTurnRequest,
    EphemeralContext,
)
from application.ports.agent_conversation_repository import AgentConversationRepository
from application.services.agent_context_service import AgentContextService
from application.services.agent_handoff_service import AgentHandoffService
from application.services.agent_pending_action_service import pending_action_wire
from application.services.agent_runtime_service import AgentRuntimeService
from bootstrap import ApplicationContainer
from domain.agent.enums import AgentChannel, AgentConversationStatus
from domain.agent.models import AgentConversation, AgentMessage, AgentToolReceipt
from domain.common.errors import TradingPartnerError
from interfaces.agent.action_gateway import AgentActionGateway
from interfaces.agent.capability_gateway import AgentCapabilityGateway
from interfaces.agent.prompts import build_agent_system_prompt
from interfaces.mcp.tools.compact import CompactCapabilityRegistry

AGENT_OWNER_PRINCIPAL = "local-console"
AGENT_CHANNEL = AgentChannel.CONSOLE
_SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9._-]+\.png$")


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateConversationRequest(_RequestModel):
    title: str = Field(default="新会话", min_length=1, max_length=240)


class EphemeralContextRequest(_RequestModel):
    """Strictly bounded, one-turn context from the current Console page."""

    location: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_PATH_MAX_CHARS,
    )
    selection: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS,
    )
    content_excerpt: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS,
    )

    @model_validator(mode="after")
    def validate_total_size(self) -> EphemeralContextRequest:
        total_bytes = sum(
            len(value.encode("utf-8"))
            for value in (self.location, self.selection, self.content_excerpt)
            if value is not None
        )
        if total_bytes > EPHEMERAL_CONTEXT_MAX_BYTES:
            raise ValueError("ephemeral_context exceeds the bounded total size")
        return self

    def to_dto(self) -> EphemeralContext:
        """Convert the HTTP payload to the transport-neutral application DTO."""

        return EphemeralContext(
            location=self.location,
            selection=self.selection,
            content_excerpt=self.content_excerpt,
        )


class SendMessageRequest(_RequestModel):
    content: str = Field(min_length=1, max_length=64_000)
    external_message_ref: str | None = Field(default=None, min_length=1, max_length=512)
    ephemeral_context: EphemeralContextRequest | None = None


class ArchiveConversationRequest(_RequestModel):
    expected_version: int = Field(ge=1)


class PendingActionDecisionRequest(_RequestModel):
    confirmation_token: str = Field(
        min_length=1,
        max_length=512,
        validation_alias=AliasChoices("confirmation_token", "token"),
    )
    expected_version: int = Field(ge=1)


class TelegramHandoffRequest(_RequestModel):
    ttl_seconds: int | None = Field(default=None, ge=30, le=3600)


@dataclass(frozen=True, slots=True)
class AgentRuntimeState:
    """Lifespan-owned collaborators and secret-safe readiness diagnostics."""

    repository: AgentConversationRepository | None
    context_service: AgentContextService | None
    capability_gateway: AgentCapabilityGateway | None
    runtime: AgentRuntimeService | None
    status: dict[str, Any]
    action_gateway: AgentActionGateway | None = None
    handoff_service: AgentHandoffService | None = None


def _diagnostic(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def build_agent_runtime_state(
    container: ApplicationContainer,
    registry: CompactCapabilityRegistry,
) -> AgentRuntimeState:
    """Construct the Console Agent graph without exposing endpoint secrets."""

    settings = getattr(container, "settings", None)
    enabled = bool(getattr(settings, "agent_enabled", False))
    operations = getattr(container, "operations", None)
    repository = cast(
        AgentConversationRepository | None,
        getattr(operations, "agent_conversations", None),
    )
    gateway = AgentCapabilityGateway(registry)
    context_service: AgentContextService | None = None
    runtime: AgentRuntimeService | None = None
    action_gateway: AgentActionGateway | None = None
    handoff_service: AgentHandoffService | None = None
    diagnostics: list[dict[str, str]] = []

    if repository is None:
        diagnostics.append(
            _diagnostic(
                "AGENT_RUNTIME_UNAVAILABLE",
                "Agent conversation storage is unavailable.",
            )
        )
    else:
        context = getattr(container, "context", None)
        clock = getattr(context, "clock", None)
        id_generator = getattr(context, "id_generator", None)
        if clock is None or id_generator is None:
            diagnostics.append(
                _diagnostic(
                    "AGENT_RUNTIME_UNAVAILABLE",
                    "Agent runtime context is unavailable.",
                )
            )
        else:
            context_service = AgentContextService(
                repository=repository,
                clock=clock,
                id_generator=id_generator,
            )
            pending_repository = cast(Any, getattr(operations, "agent_pending_actions", None))
            if pending_repository is not None:
                action_gateway = AgentActionGateway.from_dependencies(
                    repository=pending_repository,
                    registry=registry,
                    clock=clock,
                    id_generator=id_generator,
                )
            handoff_repository = cast(Any, getattr(operations, "agent_handoffs", None))
            if handoff_repository is not None:
                handoff_service = AgentHandoffService(
                    repository=handoff_repository,
                    clock=clock,
                    id_generator=id_generator,
                )

    resources = getattr(container, "resources", None)
    model_provider = getattr(resources, "agent_model_provider", None)
    model_metadata: dict[str, object] = {
        "model": None,
        "api_style": None,
        "reasoning_mode": None,
        "native_web_search": None,
        "native_web_extractor": None,
    }
    try:
        endpoint = getattr(settings, "resolved_llm_config", None)
        if endpoint is not None:
            model_metadata = {
                "model": getattr(endpoint, "model", None),
                "api_style": getattr(endpoint, "api_style", None),
                "reasoning_mode": getattr(endpoint, "reasoning_mode", None),
                "native_web_search": getattr(endpoint, "native_web_search", None),
                "native_web_extractor": getattr(endpoint, "native_web_extractor", None),
            }
    except Exception:  # noqa: BLE001 - diagnostics must remain secret-safe
        model_metadata = {
            "model": None,
            "api_style": None,
            "reasoning_mode": None,
            "native_web_search": None,
            "native_web_extractor": None,
        }
    if not enabled:
        diagnostics.append(
            _diagnostic(
                "AGENT_DISABLED",
                "Console Agent is disabled by configuration.",
            )
        )
    elif model_provider is None:
        diagnostics.append(
            _diagnostic(
                "AGENT_CONFIGURATION_UNAVAILABLE",
                "Console Agent is enabled but no configured model endpoint is available.",
            )
        )
    elif context_service is not None:
        assert repository is not None
        runtime = AgentRuntimeService(
            repository=repository,
            context_service=context_service,
            model_provider=model_provider,
            tool_gateway=gateway,
            clock=cast(Any, getattr(getattr(container, "context", None), "clock", None)),
            id_generator=cast(
                Any,
                getattr(getattr(container, "context", None), "id_generator", None),
            ),
            system_prompt=build_agent_system_prompt(),
            pending_action_gateway=action_gateway,
            turn_lock_factory=cast(
                Any,
                getattr(getattr(container, "resources", None), "agent_turn_lock_factory", None),
            ),
        )

    if diagnostics:
        state = "DISABLED" if not enabled else "UNAVAILABLE"
        available = False
    else:
        state = "READY"
        available = True
    status = {
        "channel": AGENT_CHANNEL.value,
        "owner_principal": AGENT_OWNER_PRINCIPAL,
        "enabled": enabled,
        "available": available,
        "state": state,
        "diagnostics": diagnostics,
        "model_configured": model_provider is not None,
        **model_metadata,
        "read_capability_count": sum(
            1 for descriptor in gateway.descriptors() if descriptor.auto_allowed
        ),
    }
    return AgentRuntimeState(
        repository=repository,
        context_service=context_service,
        capability_gateway=gateway,
        runtime=runtime,
        action_gateway=action_gateway,
        handoff_service=handoff_service,
        status=status,
    )


def unavailable_agent_state(*, code: str, message: str) -> AgentRuntimeState:
    """Return a status-only state when composition cannot load configuration."""

    return AgentRuntimeState(
        repository=None,
        context_service=None,
        capability_gateway=None,
        runtime=None,
        action_gateway=None,
        handoff_service=None,
        status={
            "channel": AGENT_CHANNEL.value,
            "owner_principal": AGENT_OWNER_PRINCIPAL,
            "enabled": False,
            "available": False,
            "state": "UNAVAILABLE",
            "diagnostics": [_diagnostic(code, message)],
            "model_configured": False,
            "model": None,
            "api_style": None,
            "reasoning_mode": None,
            "native_web_search": None,
            "native_web_extractor": None,
            "read_capability_count": 0,
        },
    )


router = APIRouter(prefix="/api/agent", tags=["agent"])


def _state(request: Request) -> AgentRuntimeState:
    state = getattr(request.app.state, "agent_runtime_state", None)
    if isinstance(state, AgentRuntimeState):
        return state
    return unavailable_agent_state(
        code="AGENT_RUNTIME_UNAVAILABLE",
        message="Console Agent runtime is not initialized.",
    )


def _raise_unavailable(state: AgentRuntimeState, *, write: bool) -> NoReturn:
    diagnostics = state.status.get("diagnostics")
    first_code = (
        diagnostics[0].get("code")
        if isinstance(diagnostics, list)
        and diagnostics
        and isinstance(diagnostics[0], dict)
        else None
    )
    if state.status.get("state") == "DISABLED" or first_code == "AGENT_DISABLED":
        code = "AGENT_DISABLED"
        message = "Console Agent is disabled by configuration."
    elif first_code == "AGENT_RUNTIME_UNAVAILABLE":
        code = "AGENT_RUNTIME_UNAVAILABLE"
        message = "Console Agent runtime is not initialized."
    else:
        code = "AGENT_CONFIGURATION_UNAVAILABLE"
        message = "Console Agent is not available because its model endpoint is not configured."
    # Reads of durable history remain available while disabled.  Creation and
    # turns are writes to the Agent conversation boundary and fail clearly.
    raise HTTPException(
        status_code=503,
        detail={"code": code, "message": message, "write": write},
    )


def _require_repository(state: AgentRuntimeState) -> AgentConversationRepository:
    repository = state.repository
    if repository is None:
        _raise_unavailable(state, write=False)
    return repository


def _require_runtime(state: AgentRuntimeState) -> AgentRuntimeService:
    if state.runtime is None:
        _raise_unavailable(state, write=True)
    return state.runtime


def _owned_conversation(
    request: Request,
    conversation_id: str,
    *,
    active: bool = False,
) -> AgentConversation:
    state = _state(request)
    repository = _require_repository(state)
    conversation = repository.get_conversation(conversation_id)
    # Return not-found for a principal mismatch so the local Console cannot
    # enumerate another channel/principal's conversations by timing.
    if conversation is None or conversation.owner_principal != AGENT_OWNER_PRINCIPAL:
        raise HTTPException(status_code=404, detail="Agent conversation was not found")
    if active and conversation.status is not AgentConversationStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Agent conversation is archived")
    return conversation


def _time(value: datetime) -> str:
    return value.isoformat()


def _conversation_wire(value: AgentConversation) -> dict[str, Any]:
    return {
        "conversation_id": value.conversation_id,
        "owner_principal": value.owner_principal,
        "title": value.title,
        "status": value.status.value,
        "rolling_summary": value.rolling_summary,
        "summary_through_sequence": value.summary_through_sequence,
        "next_message_sequence": value.next_message_sequence,
        "version": value.version,
        "created_at": _time(value.created_at),
        "updated_at": _time(value.updated_at),
    }


def _safe_model_receipt(value: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if value is None:
        return None, None
    if len(value) > 16_384:
        return None, "AGENT_MODEL_RECEIPT_TOO_LARGE"
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None, "AGENT_MODEL_RECEIPT_INVALID"
    if not isinstance(parsed, dict):
        return None, "AGENT_MODEL_RECEIPT_INVALID"
    usage = parsed.get("usage")
    safe_usage: dict[str, int] | None = None
    if isinstance(usage, dict):
        safe_usage = {
            key: candidate
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "web_search_calls",
                "web_extractor_calls",
            )
            if type(candidate := usage.get(key)) is int and candidate >= 0
        }
    urls = parsed.get("web_source_urls")
    safe_urls = [
        candidate
        for candidate in urls[:20]
        if isinstance(candidate, str)
        and len(candidate) <= 2_048
        and candidate.startswith(("https://", "http://"))
    ] if isinstance(urls, list) else []
    safe = {
        "model": parsed.get("model") if isinstance(parsed.get("model"), str) else None,
        "finish_reason": parsed.get("finish_reason")
        if isinstance(parsed.get("finish_reason"), str)
        else None,
        "tool_rounds": parsed.get("tool_rounds")
        if type(parsed.get("tool_rounds")) is int
        else None,
        "usage": safe_usage,
        "web_search_used": parsed.get("web_search_used")
        if isinstance(parsed.get("web_search_used"), bool)
        else False,
        "web_extractor_used": parsed.get("web_extractor_used")
        if isinstance(parsed.get("web_extractor_used"), bool)
        else False,
        "web_source_urls": safe_urls,
        "request_id": parsed.get("request_id")
        if isinstance(parsed.get("request_id"), str)
        else None,
        "latency_ms": latency
        if type(latency := parsed.get("latency_ms")) is int and latency >= 0
        else None,
        "tool_trace": [
            item
            for item in parsed.get("tool_trace", [])[:32]
            if isinstance(item, str) and len(item) <= 100
        ]
        if isinstance(parsed.get("tool_trace"), list)
        else [],
    }
    return cast(dict[str, Any], jsonable_encoder(safe)), None


def _message_wire(value: AgentMessage) -> dict[str, Any]:
    model_receipt, receipt_warning = _safe_model_receipt(value.model_receipt_json)
    return {
        "message_id": value.message_id,
        "conversation_id": value.conversation_id,
        "role": value.role.value,
        "content": value.content,
        "sequence": value.sequence,
        "channel": value.channel.value if value.channel is not None else None,
        "external_message_ref": value.external_message_ref,
        "model": value.model,
        "request_id": value.request_id,
        "model_receipt": model_receipt,
        "model_receipt_warning": receipt_warning,
        "created_at": _time(value.created_at),
    }


def _receipt_wire(value: AgentToolReceipt) -> dict[str, Any]:
    return {
        "receipt_id": value.receipt_id,
        "conversation_id": value.conversation_id,
        "message_id": value.message_id,
        "capability": value.capability,
        "operation": value.operation,
        "arguments_sha256": value.arguments_sha256,
        "request_id": value.request_id,
        "source_codes": list(value.source_codes),
        "warning_codes": list(value.warning_codes),
        "error_codes": list(value.error_codes),
        "created_at": _time(value.created_at),
    }


def _require_action_gateway(state: AgentRuntimeState) -> AgentActionGateway:
    _require_runtime(state)
    if state.action_gateway is None:
        _raise_unavailable(state, write=True)
    return state.action_gateway


@router.get("/status")
def agent_status(request: Request) -> dict[str, Any]:
    """Return readiness without exposing API keys, credentials, or endpoint URLs."""

    return dict(_state(request).status)


@router.get("/artifacts/{artifact_name}")
def get_agent_chart_artifact(artifact_name: str) -> FileResponse:
    """Serve only a PNG below the project-owned technical artifact directory."""

    if _SAFE_ARTIFACT_NAME.fullmatch(artifact_name) is None:
        raise HTTPException(status_code=404, detail="Agent chart artifact was not found")
    root = (Path.cwd() / "data" / "artifacts" / "technical").resolve()
    candidate = (root / artifact_name).resolve()
    if candidate.parent != root or not candidate.is_file() or candidate.suffix.lower() != ".png":
        raise HTTPException(status_code=404, detail="Agent chart artifact was not found")
    return FileResponse(candidate, media_type="image/png", filename=artifact_name)


@router.get("/conversations")
def list_agent_conversations(
    request: Request,
    include_archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    state = _state(request)
    repository = _require_repository(state)
    values = repository.list_conversations(
        AGENT_OWNER_PRINCIPAL,
        include_archived=include_archived,
        limit=limit,
    )
    return {
        "count": len(values),
        "items": [_conversation_wire(item) for item in values],
    }


@router.post("/conversations", status_code=201)
def create_agent_conversation(
    request: Request,
    payload: CreateConversationRequest,
) -> dict[str, Any]:
    state = _state(request)
    _require_runtime(state)
    if state.context_service is None:
        _raise_unavailable(state, write=True)
    context_service = state.context_service
    conversation = context_service.create_conversation(
        owner_principal=AGENT_OWNER_PRINCIPAL,
        title=payload.title,
    )
    return {"conversation": _conversation_wire(conversation)}


@router.get("/conversations/{conversation_id}/messages")
def list_agent_messages(
    conversation_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    conversation = _owned_conversation(request, conversation_id)
    values = _require_repository(_state(request)).list_messages(
        conversation.conversation_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return {"count": len(values), "items": [_message_wire(item) for item in values]}


@router.post("/conversations/{conversation_id}/archive")
def archive_agent_conversation(
    conversation_id: str,
    request: Request,
    payload: ArchiveConversationRequest,
) -> dict[str, Any]:
    state = _state(request)
    _require_runtime(state)
    repository = _require_repository(state)
    _owned_conversation(request, conversation_id)
    try:
        conversation = repository.archive_conversation(
            conversation_id,
            owner_principal=AGENT_OWNER_PRINCIPAL,
            expected_version=payload.expected_version,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": "Agent conversation changed; reload and retry."},
        ) from None
    return {"conversation": _conversation_wire(conversation)}


@router.post("/conversations/{conversation_id}/handoff/telegram")
def create_telegram_handoff(
    conversation_id: str,
    request: Request,
    payload: TelegramHandoffRequest,
) -> dict[str, Any]:
    state = _state(request)
    _require_runtime(state)
    conversation = _owned_conversation(request, conversation_id, active=True)
    service = state.handoff_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AGENT_HANDOFF_UNAVAILABLE",
                "message": "Telegram handoff is not configured.",
            },
        )
    handoff, raw_token = service.create(
        conversation_id=conversation.conversation_id,
        owner_principal=AGENT_OWNER_PRINCIPAL,
        target_channel=AgentChannel.TELEGRAM,
        ttl_seconds=payload.ttl_seconds,
    )
    # The opaque token is returned exactly once.  Do not echo the source
    # conversation ID as a human-facing code; Telegram resolves it durably.
    return {
        "handoff_id": handoff.handoff_id,
        "target_channel": handoff.target_channel.value,
        "expires_at": _time(handoff.expires_at),
        "token": raw_token,
    }


@router.get("/conversations/{conversation_id}/receipts")
def list_agent_receipts(
    conversation_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    conversation = _owned_conversation(request, conversation_id)
    values = _require_repository(_state(request)).list_tool_receipts(
        conversation.conversation_id,
        limit=limit,
    )
    return {"count": len(values), "items": [_receipt_wire(item) for item in values]}


@router.get("/conversations/{conversation_id}/pending-actions")
def list_agent_pending_actions(
    conversation_id: str,
    request: Request,
    include_terminal: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    _owned_conversation(request, conversation_id)
    gateway = _require_action_gateway(_state(request))
    values = gateway.list(
        conversation_id,
        channel=AGENT_CHANNEL,
        principal=AGENT_OWNER_PRINCIPAL,
        include_terminal=include_terminal,
        limit=limit,
    )
    return {"count": len(values), "items": [pending_action_wire(item) for item in values]}


def _pending_action_conversation(
    request: Request,
    conversation_id: str,
    action_id: str,
) -> AgentActionGateway:
    _owned_conversation(request, conversation_id, active=True)
    gateway = _require_action_gateway(_state(request))
    action = gateway.get(action_id)
    if (
        action is None
        or action.conversation_id != conversation_id
        or action.channel is not AGENT_CHANNEL
        or action.principal != AGENT_OWNER_PRINCIPAL
    ):
        raise HTTPException(status_code=404, detail="Agent pending action was not found")
    return gateway


@router.post("/conversations/{conversation_id}/pending-actions/{action_id}/confirm")
async def confirm_agent_pending_action(
    conversation_id: str,
    action_id: str,
    request: Request,
    payload: PendingActionDecisionRequest,
) -> dict[str, Any]:
    gateway = _pending_action_conversation(request, conversation_id, action_id)
    try:
        result = await gateway.confirm(
            action_id=action_id,
            token=payload.confirmation_token,
            expected_version=payload.expected_version,
            channel=AGENT_CHANNEL,
            principal=AGENT_OWNER_PRINCIPAL,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": "Pending action changed or is no longer valid."},
        ) from None
    return {"action": pending_action_wire(result.action), "result": jsonable_encoder(result.result)}


@router.post("/conversations/{conversation_id}/pending-actions/{action_id}/reject")
def reject_agent_pending_action(
    conversation_id: str,
    action_id: str,
    request: Request,
    payload: PendingActionDecisionRequest,
) -> dict[str, Any]:
    gateway = _pending_action_conversation(request, conversation_id, action_id)
    try:
        action = gateway.reject(
            action_id=action_id,
            token=payload.confirmation_token,
            expected_version=payload.expected_version,
            channel=AGENT_CHANNEL,
            principal=AGENT_OWNER_PRINCIPAL,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": "Pending action changed or is no longer valid."},
        ) from None
    return {"action": pending_action_wire(action)}


def _sse(event: AgentTurnEvent, ordinal: int) -> bytes:
    encoded = json.dumps(
        jsonable_encoder(event.data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"event: {event.type}\nid: {ordinal}\ndata: {encoded}\n\n".encode()


_STREAM_END = object()


async def _event_stream(
    runtime: AgentRuntimeService,
    turn: AgentTurnRequest,
) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[AgentTurnEvent | object] = asyncio.Queue()

    async def emit(event: AgentTurnEvent) -> None:
        await queue.put(event)

    async def produce() -> None:
        try:
            await runtime.run_turn(turn, event_sink=emit)
        except Exception:
            # AgentRuntimeService emits a secret-safe ``failed`` event.  The
            # stream remains a valid SSE sequence even when the model fails.
            pass
        finally:
            await queue.put(_STREAM_END)

    task = asyncio.create_task(produce())
    ordinal = 0
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_END:
                break
            ordinal += 1
            yield _sse(cast(AgentTurnEvent, item), ordinal)
    finally:
        # Do not cancel a turn when a browser tab disconnects.  The runtime
        # owns durable append/receipt completion; a reconnect can read the
        # resulting messages and receipts from the history endpoints.
        if task.done():
            with contextlib.suppress(asyncio.CancelledError):
                task.result()


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_agent_message(
    conversation_id: str,
    request: Request,
    payload: SendMessageRequest,
) -> StreamingResponse:
    state = _state(request)
    runtime = _require_runtime(state)
    _owned_conversation(request, conversation_id, active=True)
    turn = AgentTurnRequest(
        conversation_id=conversation_id,
        owner_principal=AGENT_OWNER_PRINCIPAL,
        channel=AGENT_CHANNEL,
        content=payload.content,
        external_message_ref=payload.external_message_ref,
        ephemeral_context=(
            payload.ephemeral_context.to_dto()
            if payload.ephemeral_context is not None
            else None
        ),
    )
    return StreamingResponse(
        _event_stream(runtime, turn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "AGENT_CHANNEL",
    "AGENT_OWNER_PRINCIPAL",
    "ArchiveConversationRequest",
    "AgentRuntimeState",
    "CreateConversationRequest",
    "EphemeralContextRequest",
    "PendingActionDecisionRequest",
    "TelegramHandoffRequest",
    "SendMessageRequest",
    "agent_status",
    "build_agent_runtime_state",
    "get_agent_chart_artifact",
    "router",
    "unavailable_agent_state",
]
