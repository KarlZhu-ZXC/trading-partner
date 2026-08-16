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
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from application.dto.agent import (
    EPHEMERAL_CONTEXT_EXCERPT_MAX_CHARS,
    EPHEMERAL_CONTEXT_MAX_BYTES,
    EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
    EPHEMERAL_CONTEXT_PATH_MAX_CHARS,
    EPHEMERAL_CONTEXT_ROUTE_HASH_MAX_CHARS,
    EPHEMERAL_CONTEXT_SELECTION_MAX_CHARS,
    EPHEMERAL_CONTEXT_SURFACE_MAX_CHARS,
    AgentTurnEvent,
    AgentTurnRequest,
    EphemeralContext,
)
from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.agent_model_provider import AgentModelProvider
from application.services.agent_context_service import AgentContextService
from application.services.agent_conversation_metrics import AgentConversationMetricsService
from application.services.agent_handoff_service import AgentHandoffService
from application.services.agent_pending_action_service import pending_action_wire
from application.services.agent_preferences_service import AgentPreferencesService
from application.services.agent_runtime_service import AgentRuntimeService
from bootstrap import ApplicationContainer
from domain.agent.enums import AgentChannel, AgentConversationStatus, AgentTurnStatus
from domain.agent.models import AgentConversation, AgentMessage, AgentToolReceipt, AgentTurn
from domain.agent.preferences import DEFAULT_AGENT_PREFERENCES, AgentPreferences
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
    route_hash: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_ROUTE_HASH_MAX_CHARS,
        pattern=r"^[A-Za-z0-9._~:/?#=&%+-]+$",
    )
    surface: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_SURFACE_MAX_CHARS,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]*$",
    )
    selected_subject_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    selected_monitor_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    selected_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    active_tab: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_SURFACE_MAX_CHARS,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]*$",
    )
    workbench_subject_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=EPHEMERAL_CONTEXT_NAV_FIELD_MAX_CHARS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )

    @model_validator(mode="after")
    def validate_total_size(self) -> EphemeralContextRequest:
        total_bytes = sum(
            len(value.encode("utf-8"))
            for value in (
                self.location,
                self.selection,
                self.content_excerpt,
                self.route_hash,
                self.surface,
                self.selected_subject_id,
                self.selected_monitor_id,
                self.selected_run_id,
                self.active_tab,
                self.workbench_subject_id,
            )
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
            route_hash=self.route_hash,
            surface=self.surface,
            selected_subject_id=self.selected_subject_id,
            selected_monitor_id=self.selected_monitor_id,
            selected_run_id=self.selected_run_id,
            active_tab=self.active_tab,
            workbench_subject_id=self.workbench_subject_id,
        )


class SendMessageRequest(_RequestModel):
    content: str = Field(min_length=1, max_length=64_000)
    model_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._:-]*$",
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
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


class PendingActionReissueRequest(_RequestModel):
    expected_version: int = Field(ge=1)


class TelegramHandoffRequest(_RequestModel):
    ttl_seconds: int | None = Field(default=None, ge=30, le=3600)


class UpdateAgentPreferencesRequest(_RequestModel):
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    authorization_note: str = Field(min_length=1, max_length=2_000)
    language: Literal["zh-CN", "en"] | None = None
    response_density: Literal["compact", "standard", "detailed"] | None = None
    preferred_source_codes: list[str] | None = Field(default=None, max_length=32)
    risk_style: Literal["balanced", "cautious", "direct"] | None = None
    default_chart: bool | None = None

    @model_validator(mode="after")
    def require_patch(self) -> UpdateAgentPreferencesRequest:
        if not any(
            getattr(self, name) is not None
            for name in (
                "language",
                "response_density",
                "preferred_source_codes",
                "risk_style",
                "default_chart",
            )
        ):
            raise ValueError("At least one presentation preference is required")
        return self


class ResetAgentPreferencesRequest(_RequestModel):
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    authorization_note: str = Field(min_length=1, max_length=2_000)


@dataclass(frozen=True, slots=True)
class AgentRuntimeState:
    """Lifespan-owned collaborators and secret-safe readiness diagnostics."""

    repository: AgentConversationRepository | None
    context_service: AgentContextService | None
    capability_gateway: AgentCapabilityGateway | None
    runtime: AgentRuntimeService | None
    status: dict[str, Any]
    model_providers: Mapping[str, AgentModelProvider] = field(default_factory=dict)
    action_gateway: AgentActionGateway | None = None
    handoff_service: AgentHandoffService | None = None
    preferences_service: AgentPreferencesService | None = None
    metrics_service: AgentConversationMetricsService | None = None


def _diagnostic(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _reconcile_orphaned_agent_turns(
    repository: AgentConversationRepository,
    *,
    clock: Any,
) -> int:
    """Converge pre-start active turns without rerunning any model or tool."""

    reconciled = 0
    conversations = repository.list_conversations(
        AGENT_OWNER_PRINCIPAL,
        include_archived=True,
        limit=500,
    )
    getter = getattr(repository, "latest_turn", None)
    updater = getattr(repository, "update_turn", None)
    if not callable(getter) or not callable(updater):
        return 0
    for conversation in conversations:
        turn = getter(conversation.conversation_id)
        if turn is None or turn.status not in {
            AgentTurnStatus.RUNNING,
            AgentTurnStatus.WAITING_TOOL,
        }:
            continue
        now = clock.now()
        try:
            updater(
                turn.turn_id,
                status=AgentTurnStatus.FAILED,
                expected_version=turn.version,
                error_code="AGENT_TURN_PROCESS_INTERRUPTED",
                completed_at=now,
                now=now,
            )
        except TradingPartnerError:
            continue
        reconciled += 1
    return reconciled


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
    service_graph = getattr(container, "services", None)
    container_context = getattr(container, "context", None)
    review_item_service = getattr(service_graph, "review_items", None)
    gateway = AgentCapabilityGateway(
        registry,
        review_item_service=review_item_service,
        clock=getattr(container_context, "clock", None),
    )
    context_service: AgentContextService | None = None
    runtime: AgentRuntimeService | None = None
    action_gateway: AgentActionGateway | None = None
    handoff_service: AgentHandoffService | None = None
    preferences_service: AgentPreferencesService | None = None
    metrics_service = cast(
        AgentConversationMetricsService | None,
        getattr(operations, "agent_metrics", None),
    )
    if metrics_service is None and repository is not None:
        metrics_service = AgentConversationMetricsService(repository)
    diagnostics: list[dict[str, str]] = []
    reconciled_turn_count = 0

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
            reconciled_turn_count = _reconcile_orphaned_agent_turns(
                repository,
                clock=clock,
            )
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
                    review_item_service=review_item_service,
                )
            handoff_repository = cast(Any, getattr(operations, "agent_handoffs", None))
            if handoff_repository is not None:
                handoff_service = AgentHandoffService(
                    repository=handoff_repository,
                    clock=clock,
                    id_generator=id_generator,
                )
            preferences_repository = cast(Any, getattr(operations, "agent_preferences", None))
            if preferences_repository is not None:
                preferences_service = AgentPreferencesService(
                    preferences_repository,
                    clock,
                    id_generator,
                )

    resources = getattr(container, "resources", None)
    model_provider = getattr(resources, "agent_model_provider", None)
    raw_model_providers = getattr(resources, "agent_model_providers", {})
    model_providers = (
        dict(raw_model_providers)
        if isinstance(raw_model_providers, Mapping)
        else {}
    )
    default_model_id = getattr(settings, "default_agent_llm_id", None)
    if not model_providers and model_provider is not None:
        default_model_id = default_model_id or "default"
        model_providers[default_model_id] = model_provider
    if default_model_id not in model_providers:
        default_model_id = next(iter(model_providers), None)
    if default_model_id is not None:
        model_provider = model_providers.get(default_model_id)
    model_metadata: dict[str, object] = {
        "model": None,
        "default_model_id": None,
        "models": [],
        "providers": [],
        "api_style": None,
        "reasoning_mode": None,
        "native_web_search": None,
        "native_web_extractor": None,
    }
    try:
        configs = getattr(settings, "resolved_agent_llm_configs", {})
        endpoint = (
            configs.get(default_model_id)
            if isinstance(configs, Mapping)
            and default_model_id is not None
            and default_model_id in configs
            else getattr(settings, "resolved_llm_config", None)
        )
        if endpoint is not None:
            model_metadata = {
                "model": getattr(endpoint, "model", None),
                "default_model_id": default_model_id,
                "models": [
                    {
                        "id": model_id,
                        "provider": model_id,
                        "model": getattr(config, "model", None),
                        "api_style": getattr(config, "api_style", None),
                        "reasoning_mode": getattr(config, "reasoning_mode", None),
                        "reasoning_effort": getattr(config, "reasoning_effort", None),
                        "reasoning_efforts": (
                            ["high", "max"]
                            if getattr(config, "reasoning_mode", "none") == "thinking"
                            else ["low", "medium", "high", "max"]
                            if getattr(config, "reasoning_mode", "none") == "effort"
                            else []
                        ),
                        "native_web_search": getattr(config, "native_web_search", None),
                        "is_default": model_id == default_model_id,
                    }
                    for model_id, config in configs.items()
                    if model_id in model_providers
                ]
                if isinstance(configs, Mapping) and configs
                else [
                    {
                        "id": default_model_id or "default",
                        "provider": default_model_id or "default",
                        "model": getattr(endpoint, "model", None),
                        "api_style": getattr(endpoint, "api_style", None),
                        "reasoning_mode": getattr(endpoint, "reasoning_mode", None),
                        "reasoning_effort": getattr(endpoint, "reasoning_effort", None),
                        "reasoning_efforts": (
                            ["high", "max"]
                            if getattr(endpoint, "reasoning_mode", "none") == "thinking"
                            else ["low", "medium", "high", "max"]
                            if getattr(endpoint, "reasoning_mode", "none") == "effort"
                            else []
                        ),
                        "native_web_search": getattr(endpoint, "native_web_search", None),
                        "is_default": True,
                    }
                ],
                "api_style": getattr(endpoint, "api_style", None),
                "reasoning_mode": getattr(endpoint, "reasoning_mode", None),
                "native_web_search": getattr(endpoint, "native_web_search", None),
                "native_web_extractor": getattr(endpoint, "native_web_extractor", None),
            }
            model_metadata["providers"] = model_metadata["models"]
    except Exception:  # noqa: BLE001 - diagnostics must remain secret-safe
        model_metadata = {
            "model": None,
            "default_model_id": None,
            "models": [],
            "providers": [],
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
            model_providers=model_providers,
            default_model_id=default_model_id or "default",
            tool_gateway=gateway,
            clock=cast(Any, getattr(getattr(container, "context", None), "clock", None)),
            id_generator=cast(
                Any,
                getattr(getattr(container, "context", None), "id_generator", None),
            ),
            system_prompt=build_agent_system_prompt(),
            pending_action_gateway=action_gateway,
            preferences_service=preferences_service,
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
        "reconciled_turn_count": reconciled_turn_count,
    }
    return AgentRuntimeState(
        repository=repository,
        context_service=context_service,
        capability_gateway=gateway,
        runtime=runtime,
        model_providers=model_providers,
        action_gateway=action_gateway,
        handoff_service=handoff_service,
        preferences_service=preferences_service,
        metrics_service=metrics_service,
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
        preferences_service=None,
        metrics_service=None,
        status={
            "channel": AGENT_CHANNEL.value,
            "owner_principal": AGENT_OWNER_PRINCIPAL,
            "enabled": False,
            "available": False,
            "state": "UNAVAILABLE",
            "diagnostics": [_diagnostic(code, message)],
            "model_configured": False,
            "model": None,
            "default_model_id": None,
            "models": [],
            "providers": [],
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
        "selected_model": parsed.get("selected_model")
        if isinstance(parsed.get("selected_model"), str)
        else None,
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
        "artifact_urls": [
            item
            for item in parsed.get("artifact_urls", [])[:20]
            if isinstance(item, str)
            and re.fullmatch(r"/api/agent/artifacts/[A-Za-z0-9._-]+\.png", item)
        ]
        if isinstance(parsed.get("artifact_urls"), list)
        else [],
        "selected_provider_id": parsed.get("selected_provider_id")
        if isinstance(parsed.get("selected_provider_id"), str)
        else None,
        "route_reason": parsed.get("route_reason")
        if isinstance(parsed.get("route_reason"), str)
        else None,
        "fallback_from": parsed.get("fallback_from")
        if isinstance(parsed.get("fallback_from"), str)
        else None,
        "fallback_code": parsed.get("fallback_code")
        if isinstance(parsed.get("fallback_code"), str)
        else None,
        "evidence_manifest": (
            parsed.get("evidence_manifest")
            if isinstance(parsed.get("evidence_manifest"), dict)
            and len(json.dumps(parsed.get("evidence_manifest"), ensure_ascii=False)) <= 16_384
            else None
        ),
        "capability_search_audits": (
            [
                item
                for item in parsed.get("capability_search_audits", [])[:16]
                if isinstance(item, dict)
            ]
            if isinstance(parsed.get("capability_search_audits"), list)
            else []
        ),
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


def _turn_wire(value: AgentTurn) -> dict[str, Any]:
    """Project only bounded lifecycle metadata for Console refresh/replay."""

    return {
        "turn_id": value.turn_id,
        "conversation_id": value.conversation_id,
        "user_message_id": value.user_message_id,
        "assistant_message_id": value.assistant_message_id,
        "channel": value.channel.value,
        "status": value.status.value,
        "error_code": value.error_code,
        "model_id": value.model_id,
        "reasoning_effort": value.reasoning_effort,
        "started_at": _time(value.started_at),
        "updated_at": _time(value.updated_at),
        "completed_at": _time(value.completed_at) if value.completed_at else None,
        "version": value.version,
    }


def _latest_turn_wire(
    repository: AgentConversationRepository,
    conversation_id: str,
) -> dict[str, Any] | None:
    getter = getattr(repository, "latest_turn", None)
    if getter is None:
        return None
    value = getter(conversation_id)
    return _turn_wire(value) if value is not None else None


def _require_action_gateway(state: AgentRuntimeState) -> AgentActionGateway:
    _require_runtime(state)
    if state.action_gateway is None:
        _raise_unavailable(state, write=True)
    return state.action_gateway


@router.get("/status")
def agent_status(request: Request) -> dict[str, Any]:
    """Return readiness without exposing API keys, credentials, or endpoint URLs."""

    from interfaces.cli.agent import supervisor_status_snapshot

    return {
        **dict(_state(request).status),
        "components": supervisor_status_snapshot(),
    }


def _preferences_wire(value: AgentPreferences | None) -> dict[str, Any]:
    if value is not None:
        return {
            "preferences_id": value.preferences_id,
            **value.as_dict(),
            "created_at": _time(value.created_at) if value.created_at else None,
        }
    return {
        "preferences_id": None,
        "language": "zh-CN",
        "response_density": "standard",
        "preferred_source_codes": [],
        "risk_style": "balanced",
        "default_chart": DEFAULT_AGENT_PREFERENCES["default_chart"],
        "web_background": DEFAULT_AGENT_PREFERENCES["web_background"],
        "version": 0,
        "created_at": None,
        "updated_at": None,
    }


def _require_preferences(state: AgentRuntimeState) -> AgentPreferencesService:
    if state.preferences_service is None:
        raise HTTPException(status_code=503, detail="Agent preferences are unavailable")
    return state.preferences_service


@router.get("/preferences")
def get_agent_preferences(request: Request) -> dict[str, Any]:
    service = _require_preferences(_state(request))
    return {"preferences": _preferences_wire(service.get(AGENT_OWNER_PRINCIPAL))}


@router.put("/preferences")
def update_agent_preferences(
    request: Request,
    payload: UpdateAgentPreferencesRequest,
) -> dict[str, Any]:
    service = _require_preferences(_state(request))
    patch = payload.model_dump(
        exclude={"expected_version", "idempotency_key", "authorization_note"},
        exclude_none=True,
    )
    try:
        value = service.update(
            AGENT_OWNER_PRINCIPAL,
            patch,
            expected_version=payload.expected_version,
            actor="user",
            idempotency_key=payload.idempotency_key,
            authorization_note=payload.authorization_note,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": "Agent preferences were not updated."},
        ) from None
    return {"preferences": _preferences_wire(value)}


@router.post("/preferences/reset")
def reset_agent_preferences(
    request: Request,
    payload: ResetAgentPreferencesRequest,
) -> dict[str, Any]:
    service = _require_preferences(_state(request))
    try:
        value = service.reset(
            AGENT_OWNER_PRINCIPAL,
            expected_version=payload.expected_version,
            actor="user",
            idempotency_key=payload.idempotency_key,
            authorization_note=payload.authorization_note,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": "Agent preferences were not reset."},
        ) from None
    return {"preferences": _preferences_wire(value)}


@router.get("/preferences/history")
def get_agent_preferences_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    service = _require_preferences(_state(request))
    items = service.history(AGENT_OWNER_PRINCIPAL, limit=limit)
    return {
        "items": [
            {
                "revision_id": item.revision_id,
                "operation": item.operation,
                "actor": item.actor,
                "preferences": _preferences_wire(item.preferences),
                "created_at": _time(item.created_at),
            }
            for item in items
        ]
    }


def _reasoning_efforts_for_mode(mode: object) -> tuple[str, ...]:
    if mode == "thinking":
        return ("high", "max")
    if mode == "effort":
        return ("low", "medium", "high", "max")
    return ()


@router.get("/providers/{provider_id}/models")
async def list_agent_provider_models(
    provider_id: str,
    request: Request,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return a bounded, secret-safe model directory for one configured Provider."""

    if re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,63}", provider_id) is None:
        raise HTTPException(status_code=404, detail="Agent Provider was not found")
    state = _state(request)
    _require_runtime(state)
    provider = state.model_providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Agent Provider was not found")
    config = getattr(provider, "config", None)
    configured_model = getattr(config, "model", None) or getattr(provider, "model", None)
    list_models = getattr(provider, "list_models", None)
    try:
        if callable(list_models):
            catalog = await list_models(force_refresh=refresh)
            raw_items = getattr(catalog, "models", ())
            fetched_at = getattr(catalog, "fetched_at", None)
            cached = bool(getattr(catalog, "cached", False))
        else:
            raw_items = ()
            fetched_at = None
            cached = True
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": error.code,
                "message": "Unable to load the Provider model directory.",
            },
        ) from None
    except Exception:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "AGENT_MODEL_CATALOG_UNAVAILABLE",
                "message": "Unable to load the Provider model directory.",
            },
        ) from None

    fallback_efforts = _reasoning_efforts_for_mode(getattr(config, "reasoning_mode", "none"))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = tuple(raw_items) or ((configured_model,) if configured_model else ())
    for raw_item in candidates[:200]:
        model_id = raw_item if isinstance(raw_item, str) else getattr(raw_item, "id", None)
        if (
            not isinstance(model_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", model_id) is None
            or model_id in seen
        ):
            continue
        raw_efforts = () if isinstance(raw_item, str) else getattr(
            raw_item,
            "reasoning_efforts",
            (),
        )
        efforts = tuple(
            value
            for value in ("low", "medium", "high", "max")
            if value in raw_efforts
        ) or fallback_efforts
        seen.add(model_id)
        items.append(
            {
                "id": model_id,
                "label": model_id,
                "is_default": model_id == configured_model,
                "reasoning_efforts": list(efforts),
            }
        )
    return {
        "provider_id": provider_id,
        "default_model": configured_model,
        "api_style": getattr(config, "api_style", None),
        "reasoning_mode": getattr(config, "reasoning_mode", "none"),
        "native_web_search": getattr(config, "native_web_search", "disabled"),
        "fetched_at": _time(fetched_at) if isinstance(fetched_at, datetime) else None,
        "cached": cached,
        "models": items,
    }


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
    items: list[dict[str, Any]] = []
    for value in values:
        item = _conversation_wire(value)
        item["latest_turn"] = _latest_turn_wire(repository, value.conversation_id)
        items.append(item)
    return {
        "count": len(values),
        "items": items,
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
    repository = _require_repository(_state(request))
    return {
        "count": len(values),
        "items": [_message_wire(item) for item in values],
        "latest_turn": _latest_turn_wire(repository, conversation.conversation_id),
    }


@router.get("/conversations/{conversation_id}/turns")
def list_agent_turns(
    conversation_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    newest_first: bool = True,
) -> dict[str, Any]:
    """Return durable turn lifecycle records so a refreshed Console can recover."""

    conversation = _owned_conversation(request, conversation_id)
    repository = _require_repository(_state(request))
    getter = getattr(repository, "list_turns", None)
    if getter is None:
        return {"count": 0, "items": [], "latest_turn": None}
    values = getter(
        conversation.conversation_id,
        limit=limit,
        newest_first=newest_first,
    )
    return {
        "count": len(values),
        "items": [_turn_wire(item) for item in values],
        "latest_turn": _turn_wire(values[0]) if values and newest_first else _latest_turn_wire(
            repository,
            conversation.conversation_id,
        ),
    }


@router.get("/conversations/{conversation_id}/metrics")
def get_agent_conversation_metrics(
    conversation_id: str,
    request: Request,
) -> dict[str, Any]:
    _owned_conversation(request, conversation_id)
    service = _state(request).metrics_service
    if service is None:
        raise HTTPException(status_code=503, detail="Agent metrics are unavailable")
    return {"metrics": service.aggregate(conversation_id).as_dict()}


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


@router.post("/conversations/{conversation_id}/pending-actions/{action_id}/reissue")
def reissue_agent_pending_action(
    conversation_id: str,
    action_id: str,
    request: Request,
    payload: PendingActionReissueRequest,
) -> dict[str, Any]:
    gateway = _pending_action_conversation(request, conversation_id, action_id)
    try:
        proposal = gateway.reissue_confirmation(
            action_id=action_id,
            conversation_id=conversation_id,
            expected_version=payload.expected_version,
            channel=AGENT_CHANNEL,
            principal=AGENT_OWNER_PRINCIPAL,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": "Pending action changed or is no longer valid."},
        ) from None
    # The opaque token is returned exactly once.  ``pending_action_wire`` is
    # intentionally token/hash-free and remains safe to cache in the Console.
    return {
        "action": pending_action_wire(proposal.action),
        "confirmation_token": proposal.confirmation_token,
    }


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


def _replay_turn_events(
    repository: AgentConversationRepository,
    turn: AgentTurn,
    *,
    skip_receipt_ids: set[str] | None = None,
    skip_message_started: bool = False,
) -> tuple[AgentTurnEvent, ...]:
    """Build a deterministic terminal replay from durable records only."""

    events: list[AgentTurnEvent] = []
    if not skip_message_started:
        events.append(
            AgentTurnEvent(
                type="message_started",
                data={
                    "conversation_id": turn.conversation_id,
                    "user_message_id": turn.user_message_id,
                    "turn_id": turn.turn_id,
                    "model_id": turn.model_id,
                    "reasoning_effort": turn.reasoning_effort,
                    "replay": True,
                },
            )
        )
    receipts = getattr(repository, "list_tool_receipts", None)
    if callable(receipts):
        for receipt in receipts(turn.conversation_id, limit=500):
            if (
                receipt.message_id != turn.user_message_id
                or (skip_receipt_ids is not None and receipt.receipt_id in skip_receipt_ids)
            ):
                continue
            events.append(
                AgentTurnEvent(
                    type="tool_finished",
                    data={
                        "conversation_id": turn.conversation_id,
                        "turn_id": turn.turn_id,
                        "receipt": _receipt_wire(receipt),
                        "replay": True,
                    },
                )
            )
    messages = getattr(repository, "list_messages", None)
    assistant: AgentMessage | None = None
    if callable(messages):
        for item in messages(turn.conversation_id, after_sequence=0, limit=500):
            if item.message_id == turn.assistant_message_id:
                assistant = item
                break
    if assistant is not None:
        events.append(
            AgentTurnEvent(
                type="text_delta",
                data={
                    "conversation_id": turn.conversation_id,
                    "turn_id": turn.turn_id,
                    "text": assistant.content,
                    "delta": assistant.content,
                    "replay": True,
                },
            )
        )
    if turn.status is AgentTurnStatus.COMPLETED:
        receipt, _warning = _safe_model_receipt(
            assistant.model_receipt_json if assistant is not None else None
        )
        events.append(
            AgentTurnEvent(
                type="completed",
                data={
                    "conversation_id": turn.conversation_id,
                    "turn_id": turn.turn_id,
                    "user_message_id": turn.user_message_id,
                    "assistant_message_id": turn.assistant_message_id,
                    "model_id": turn.model_id,
                    "model": assistant.model if assistant is not None else None,
                    "model_receipt": receipt,
                    "replay": True,
                },
            )
        )
    elif turn.status is AgentTurnStatus.CANCELLED:
        events.append(
            AgentTurnEvent(
                type="cancelled",
                data={
                    "conversation_id": turn.conversation_id,
                    "turn_id": turn.turn_id,
                    "code": turn.error_code or "AGENT_TURN_CANCELLED",
                    "replay": True,
                },
            )
        )
    elif turn.status is AgentTurnStatus.FAILED:
        events.append(
            AgentTurnEvent(
                type="failed",
                data={
                    "conversation_id": turn.conversation_id,
                    "turn_id": turn.turn_id,
                    "code": turn.error_code or "AGENT_RUNTIME_FAILED",
                    "message": "Agent 本轮未完成。",
                    "replay": True,
                },
            )
        )
    return tuple(events)


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
        model_id=payload.model_id,
        model=payload.model,
        reasoning_effort=payload.reasoning_effort,
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


@router.post("/conversations/{conversation_id}/turns/{turn_id}/cancel")
async def cancel_agent_turn(
    conversation_id: str,
    turn_id: str,
    request: Request,
) -> dict[str, Any]:
    """Cancel one exact latest Console turn; repeated cancellation is safe."""

    state = _state(request)
    runtime = _require_runtime(state)
    _owned_conversation(request, conversation_id, active=True)
    try:
        turn = await runtime.cancel_turn(
            conversation_id=conversation_id,
            turn_id=turn_id,
            owner_principal=AGENT_OWNER_PRINCIPAL,
        )
    except TradingPartnerError as error:
        status = 409 if error.code != "AGENT_TURN_NOT_FOUND" else 404
        raise HTTPException(
            status_code=status,
            detail={
                "code": error.code,
                "message": "Agent turn cannot be cancelled in its current state.",
            },
        ) from None
    return {"turn": _turn_wire(turn)}


@router.post("/conversations/{conversation_id}/turns/{turn_id}/retry")
async def retry_agent_turn(
    conversation_id: str,
    turn_id: str,
    request: Request,
) -> StreamingResponse:
    """Retry one failed/interrupted turn using its durable original prompt."""

    state = _state(request)
    runtime = _require_runtime(state)
    conversation = _owned_conversation(request, conversation_id, active=True)
    repository = _require_repository(state)
    getter = getattr(repository, "get_turn", None)
    turn = getter(turn_id) if callable(getter) else None
    if turn is None or turn.conversation_id != conversation.conversation_id:
        raise HTTPException(status_code=404, detail="Agent turn was not found")
    runtime.recover_interrupted_turn(turn_id)
    turn = getter(turn_id) if callable(getter) else turn
    if turn is None or turn.status is not AgentTurnStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AGENT_TURN_RETRY_NOT_ALLOWED",
                "message": "Only a failed Agent turn can be retried.",
            },
        )
    messages = repository.list_messages(
        conversation_id,
        after_sequence=0,
        limit=500,
    )
    original = next(
        (item for item in messages if item.message_id == turn.user_message_id),
        None,
    )
    if original is None or original.role.value != "USER":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AGENT_TURN_RETRY_SOURCE_UNAVAILABLE",
                "message": "The original Agent prompt is unavailable for retry.",
            },
        )
    retry_request = AgentTurnRequest(
        conversation_id=conversation_id,
        owner_principal=AGENT_OWNER_PRINCIPAL,
        channel=AGENT_CHANNEL,
        content=original.content,
        model_id=turn.model_id,
        reasoning_effort=turn.reasoning_effort,
    )
    return StreamingResponse(
        _event_stream(runtime, retry_request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _reconnect_turn_stream(
    runtime: AgentRuntimeService | None,
    repository: AgentConversationRepository,
    turn_id: str,
) -> AsyncIterator[bytes]:
    ordinal = 0
    started = False
    seen_receipt_ids: set[str] = set()
    while True:
        if runtime is not None:
            runtime.recover_interrupted_turn(turn_id)
        getter = getattr(repository, "get_turn", None)
        turn = getter(turn_id) if callable(getter) else None
        if turn is None:
            return
        if turn.is_terminal:
            for event in _replay_turn_events(
                repository,
                turn,
                skip_receipt_ids=seen_receipt_ids,
                skip_message_started=started,
            ):
                ordinal += 1
                yield _sse(event, ordinal)
            return
        if not started:
            started = True
            event = AgentTurnEvent(
                type="message_started",
                data={
                    "conversation_id": turn.conversation_id,
                    "turn_id": turn.turn_id,
                    "user_message_id": turn.user_message_id,
                    "model_id": turn.model_id,
                    "reasoning_effort": turn.reasoning_effort,
                    "replay": True,
                },
            )
            ordinal += 1
            yield _sse(event, ordinal)
        receipts = getattr(repository, "list_tool_receipts", None)
        if callable(receipts):
            for receipt in receipts(turn.conversation_id, limit=500):
                if (
                    receipt.message_id != turn.user_message_id
                    or receipt.receipt_id in seen_receipt_ids
                ):
                    continue
                seen_receipt_ids.add(receipt.receipt_id)
                ordinal += 1
                yield _sse(
                    AgentTurnEvent(
                        type="tool_finished",
                        data={
                            "conversation_id": turn.conversation_id,
                            "turn_id": turn.turn_id,
                            "receipt": _receipt_wire(receipt),
                            "replay": True,
                        },
                    ),
                    ordinal,
                )
        await asyncio.sleep(0.2)


@router.get("/conversations/{conversation_id}/turns/{turn_id}/stream")
async def reconnect_agent_turn_stream(
    conversation_id: str,
    turn_id: str,
    request: Request,
) -> StreamingResponse:
    """Replay/poll durable turn state without invoking a Provider again."""

    conversation = _owned_conversation(request, conversation_id)
    state = _state(request)
    repository = _require_repository(state)
    getter = getattr(repository, "get_turn", None)
    turn = getter(turn_id) if callable(getter) else None
    if turn is None or turn.conversation_id != conversation.conversation_id:
        raise HTTPException(status_code=404, detail="Agent turn was not found")
    runtime = state.runtime
    if runtime is None and not turn.is_terminal:
        _raise_unavailable(state, write=False)
    return StreamingResponse(
        _reconnect_turn_stream(runtime, repository, turn_id),
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
    "PendingActionReissueRequest",
    "TelegramHandoffRequest",
    "SendMessageRequest",
    "agent_status",
    "build_agent_runtime_state",
    "cancel_agent_turn",
    "get_agent_chart_artifact",
    "list_agent_provider_models",
    "reconnect_agent_turn_stream",
    "retry_agent_turn",
    "router",
    "unavailable_agent_state",
]
