"""Confirmation-gated Agent-D action lifecycle.

The service owns the durable CAS state machine but delegates the eventual write
to the already-registered Compact operation.  Preparing an action never calls
an adapter; confirming it performs one exact, single-use invocation.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from application.ports.agent_action_gateway import AgentActionOperationGateway
from application.ports.agent_pending_action_repository import AgentPendingActionRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.agent.enums import AgentChannel, AgentPendingActionStatus
from domain.agent.models import AgentPendingAction, arguments_digest
from domain.common.errors import DataContractError, PersistenceError, TradingPartnerError
from domain.common.ids import EntityIdPrefix

DEFAULT_ACTION_TTL_SECONDS = 10 * 60
MAX_ACTION_TTL_SECONDS = 30 * 60
_ACTION_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("investment_case_manage", "update"),
        ("research_judgment_propose", "research_state"),
        ("research_judgment_propose", "thesis_revision"),
        ("monitor_manage", "create"),
        ("monitor_manage", "update"),
        ("monitor_manage", "resolve_event"),
        ("watchlist_manage", "add"),
        ("watchlist_manage", "remove"),
    }
)
_TERMINAL = frozenset(
    {
        AgentPendingActionStatus.SUCCEEDED,
        AgentPendingActionStatus.REJECTED,
        AgentPendingActionStatus.EXPIRED,
        AgentPendingActionStatus.FAILED,
        AgentPendingActionStatus.UNKNOWN,
    }
)
_SUMMARY_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SUMMARY_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|authorization|token|secret)\s*[:=]\s*[^\s,;]+"
)
_CONFIRMATION_HIDDEN_FIELDS = frozenset(
    {
        "authorization_note",
        "confirmed_by",
        "idempotency_key",
        "proposed_by",
        "reviewed_by",
        "submitted_via",
    }
)


@dataclass(frozen=True, slots=True)
class PendingActionProposal:
    action: AgentPendingAction
    confirmation_token: str


@dataclass(frozen=True, slots=True)
class PendingActionExecution:
    action: AgentPendingAction
    result: object


def pending_action_wire(action: AgentPendingAction) -> dict[str, object]:
    """Bounded durable projection safe for Console/Telegram clients."""

    receipt: object | None = None
    if action.result_receipt_json is not None:
        try:
            value = json.loads(action.result_receipt_json)
            receipt = value if isinstance(value, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            receipt = None
    return {
        "action_id": action.action_id,
        "conversation_id": action.conversation_id,
        "channel": action.channel.value,
        "principal": action.principal,
        "capability": action.capability,
        "operation": action.operation,
        "arguments_sha256": action.arguments_sha256,
        "presented_summary": action.presented_summary,
        "confirmation_details": _confirmation_details(action.normalized_arguments),
        "status": action.status.value,
        "version": action.version,
        "expires_at": action.expires_at.isoformat(),
        "created_at": action.created_at.isoformat(),
        "updated_at": action.updated_at.isoformat(),
        "result_receipt": receipt,
    }


def _confirmation_details(arguments: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    """Return a deterministic, secret-safe view of the exact validated action."""

    details: list[dict[str, str]] = []

    def visit(value: object, path: str, depth: int) -> None:
        if len(details) >= 48 or depth > 6:
            return
        if isinstance(value, Mapping):
            for key in sorted(value, key=lambda item: str(item)):
                name = str(key)
                lowered = name.lower()
                if lowered in _CONFIRMATION_HIDDEN_FIELDS or any(
                    marker in lowered for marker in ("secret", "password", "api_key", "token")
                ):
                    continue
                visit(value[key], f"{path}.{name}" if path else name, depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value[:20]):
                visit(item, f"{path}[{index}]", depth + 1)
            return
        if value is None:
            rendered = "null"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        details.append(
            {
                "path": path[:160],
                "value": _safe_summary(rendered)[:500] if rendered.strip() else "(空)",
            }
        )

    visit(arguments, "", 0)
    return tuple(details)


def _token_digest(token: str) -> str:
    if not isinstance(token, str) or not token or len(token) > 512:
        raise DataContractError("confirmation token is invalid", code="AGENT_ACTION_TOKEN_INVALID")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _safe_summary(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(
            "pending action summary must not be blank",
            code="AGENT_ACTION_SUMMARY_INVALID",
        )
    value = value.strip()
    value = _SUMMARY_URL.sub("[REDACTED_URL]", value)
    value = _SUMMARY_SECRET.sub("[REDACTED_SECRET]", value)
    return value[:16_000]


def _safe_receipt(
    *,
    status: AgentPendingActionStatus,
    capability: str,
    operation: str,
    result: object | None = None,
    code: str | None = None,
) -> str:
    """Persist only bounded identifiers/codes, never an adapter payload."""

    value: dict[str, object] = {
        "status": status.value,
        "capability": capability,
        "operation": operation,
    }
    if code is not None and code.isascii() and len(code) <= 128:
        value["code"] = code
    if isinstance(result, Mapping):
        for key in ("request_id", "error_code"):
            item = result.get(key)
            if isinstance(item, str) and len(item) <= 160 and item.isascii():
                value[key] = item
        warnings = result.get("warnings")
        if isinstance(warnings, (list, tuple)):
            warning_codes: list[str] = []
            for warning in warnings[:32]:
                if not isinstance(warning, Mapping):
                    continue
                warning_code = warning.get("code")
                if isinstance(warning_code, str) and warning_code.isascii():
                    warning_codes.append(warning_code[:128])
            value["warning_codes"] = warning_codes
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:16_384]


class AgentPendingActionService:
    """Prepare, present, confirm, reject, and execute one exact action."""

    def __init__(
        self,
        *,
        repository: AgentPendingActionRepository,
        operation_gateway: AgentActionOperationGateway,
        clock: Clock,
        id_generator: IdGenerator,
        ttl_seconds: int = DEFAULT_ACTION_TTL_SECONDS,
    ) -> None:
        if not 1 <= ttl_seconds <= MAX_ACTION_TTL_SECONDS:
            raise ValueError("invalid pending action TTL")
        self._repository = repository
        self._operation_gateway = operation_gateway
        self._clock = clock
        self._ids = id_generator
        self._ttl = ttl_seconds

    @property
    def allowlist(self) -> frozenset[tuple[str, str]]:
        return _ACTION_ALLOWLIST

    def get(self, action_id: str) -> AgentPendingAction | None:
        self._repository.expire_due(now=self._clock.now())
        return self._repository.get_pending_action(action_id)

    def get_by_token(
        self,
        token: str,
        *,
        channel: AgentChannel,
        principal: str,
    ) -> AgentPendingAction | None:
        self._repository.expire_due(now=self._clock.now())
        action = self._repository.get_by_token_sha256(_token_digest(token))
        if (
            action is None
            or action.channel is not channel
            or action.principal != principal
        ):
            return None
        return action

    def list(
        self,
        conversation_id: str,
        *,
        channel: AgentChannel,
        principal: str,
        include_terminal: bool = False,
        limit: int = 100,
    ) -> tuple[AgentPendingAction, ...]:
        self._repository.expire_due(now=self._clock.now())
        return self._repository.list_pending_actions(
            conversation_id,
            channel=channel,
            principal=principal,
            include_terminal=include_terminal,
            limit=limit,
        )

    def _validate_action_shape(
        self,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        key = (capability, operation)
        if key not in _ACTION_ALLOWLIST:
            raise DataContractError(
                "Agent action is outside the confirmation allowlist",
                code="AGENT_ACTION_NOT_ALLOWED",
            )
        if not isinstance(arguments, Mapping):
            raise DataContractError(
                "Agent action arguments must be an object",
                code="AGENT_ACTION_SCHEMA_INVALID",
            )
        try:
            normalized = self._operation_gateway.validate_operation(
                capability,
                operation,
                dict(arguments),
            )
        except TradingPartnerError:
            raise
        except Exception as error:  # Pydantic ValidationError/ToolError
            raise DataContractError(
                "Agent action arguments do not match the exact operation schema",
                code="AGENT_ACTION_SCHEMA_INVALID",
            ) from error
        payload = normalized.get("payload")
        if capability == "research_judgment_propose":
            if not isinstance(payload, Mapping):
                raise DataContractError(
                    "Agent proposal payload is invalid",
                    code="AGENT_ACTION_SCHEMA_INVALID",
                )
            expected_kind = "watchlist_item" if operation == "research_state" else "thesis_revision"
            if payload.get("kind") != expected_kind:
                raise DataContractError(
                    "Agent proposal kind is outside the confirmation allowlist",
                    code="AGENT_ACTION_NOT_ALLOWED",
                )
            if operation == "research_state" and payload.get("action") not in {
                "create",
                "update_status",
            }:
                raise DataContractError(
                    "Agent selection candidate action is not allowed",
                    code="AGENT_ACTION_NOT_ALLOWED",
                )
        # Research Subject update is metadata-only by exact DTO construction;
        # no lifecycle or primary-instrument field is accepted by this variant.
        return normalized

    def propose(
        self,
        *,
        conversation_id: str,
        channel: AgentChannel,
        principal: str,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
        presented_summary: str,
    ) -> PendingActionProposal:
        normalized = self._validate_action_shape(capability, operation, arguments)
        now = self._clock.now()
        token = secrets.token_urlsafe(32)
        value = AgentPendingAction(
            action_id=self._ids.new(EntityIdPrefix.AGENT_PENDING_ACTION),
            conversation_id=conversation_id,
            channel=channel,
            principal=principal,
            normalized_arguments=normalized,
            arguments_sha256=arguments_digest(normalized),
            presented_summary=_safe_summary(presented_summary),
            expires_at=now + timedelta(seconds=self._ttl),
            created_at=now,
            updated_at=now,
            status=AgentPendingActionStatus.PROPOSED,
            version=1,
            capability=capability,
            operation=operation,
            token_sha256=_token_digest(token),
        )
        created = self._repository.create_pending_action(value)
        # The model only creates PROPOSED.  The application presents it to the
        # channel before returning the one-time token, preserving the strict CAS
        # path while avoiding a second public "present" endpoint.
        presented = self._repository.transition_exact(
            created.action_id,
            AgentPendingActionStatus.PRESENTED,
            arguments_sha256=created.arguments_sha256,
            channel=channel,
            principal=principal,
            expected_version=created.version,
            token_sha256=value.token_sha256,
            now=now,
        )
        return PendingActionProposal(action=presented, confirmation_token=token)

    def _identity_transition(
        self,
        action: AgentPendingAction,
        status: AgentPendingActionStatus,
        *,
        token: str,
        channel: AgentChannel,
        principal: str,
    ) -> AgentPendingAction:
        if action.channel is not channel or action.principal != principal:
            raise PersistenceError(
                "Agent pending action confirmation identity mismatch",
                retryable=False,
                code="AGENT_PENDING_ACTION_IDENTITY_MISMATCH",
            )
        return self._repository.transition_exact(
            action.action_id,
            status,
            arguments_sha256=action.arguments_sha256,
            channel=channel,
            principal=principal,
            expected_version=action.version,
            token_sha256=_token_digest(token),
            now=self._clock.now(),
        )

    def reject(
        self,
        *,
        action_id: str,
        token: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int | None = None,
    ) -> AgentPendingAction:
        action = self._repository.get_pending_action(action_id)
        if action is None:
            raise PersistenceError("Agent pending action was not found", retryable=False)
        if expected_version is not None and action.version != expected_version:
            raise PersistenceError(
                "Agent pending action version conflict",
                retryable=False,
                code="AGENT_PENDING_ACTION_VERSION_CONFLICT",
            )
        if action.status is AgentPendingActionStatus.PROPOSED:
            action = self._identity_transition(
                action,
                AgentPendingActionStatus.PRESENTED,
                token=token,
                channel=channel,
                principal=principal,
            )
        if action.status is not AgentPendingActionStatus.PRESENTED:
            raise PersistenceError(
                "Agent pending action is no longer rejectable",
                retryable=False,
                code="AGENT_PENDING_ACTION_STATE_CONFLICT",
            )
        return self._identity_transition(
            action,
            AgentPendingActionStatus.REJECTED,
            token=token,
            channel=channel,
            principal=principal,
        )

    async def confirm(
        self,
        *,
        action_id: str,
        token: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int | None = None,
    ) -> PendingActionExecution:
        action = self._repository.get_pending_action(action_id)
        if action is None:
            raise PersistenceError("Agent pending action was not found", retryable=False)
        if expected_version is not None and action.version != expected_version:
            raise PersistenceError(
                "Agent pending action version conflict",
                retryable=False,
                code="AGENT_PENDING_ACTION_VERSION_CONFLICT",
            )
        if action.status is AgentPendingActionStatus.PROPOSED:
            action = self._identity_transition(
                action,
                AgentPendingActionStatus.PRESENTED,
                token=token,
                channel=channel,
                principal=principal,
            )
        if action.status is not AgentPendingActionStatus.PRESENTED:
            code = (
                "AGENT_PENDING_ACTION_ALREADY_USED"
                if action.status in _TERMINAL
                else "AGENT_PENDING_ACTION_STATE_CONFLICT"
            )
            raise PersistenceError(
                "Agent pending action cannot be confirmed",
                retryable=False,
                code=code,
            )
        confirmed = self._identity_transition(
            action,
            AgentPendingActionStatus.CONFIRMED,
            token=token,
            channel=channel,
            principal=principal,
        )
        executing = self._identity_transition(
            confirmed,
            AgentPendingActionStatus.EXECUTING,
            token=token,
            channel=channel,
            principal=principal,
        )
        arguments = dict(executing.normalized_arguments)
        # The model cannot claim a user confirmation.  Existing operation DTOs
        # use these bounded actor fields; channel remains the durable provenance.
        if executing.capability == "investment_case_manage":
            arguments["reviewed_by"] = "user"
        elif executing.capability in {"monitor_manage", "watchlist_manage"}:
            arguments["confirmed_by"] = "user"
        elif executing.capability == "research_judgment_propose":
            arguments["proposed_by"] = "external_agent"
        try:
            normalized = self._validate_action_shape(
                executing.capability,
                executing.operation,
                arguments,
            )
            invocation = await self._operation_gateway.invoke_operation(
                executing.capability,
                executing.operation,
                normalized,
            )
            compacted = invocation.result
            receipt = invocation.receipt_json
            completed = self._repository.transition_exact(
                executing.action_id,
                AgentPendingActionStatus.SUCCEEDED,
                arguments_sha256=executing.arguments_sha256,
                channel=channel,
                principal=principal,
                expected_version=executing.version,
                token_sha256=_token_digest(token),
                result_receipt_json=receipt,
                now=self._clock.now(),
            )
            return PendingActionExecution(action=completed, result=compacted)
        except TradingPartnerError as error:
            receipt = _safe_receipt(
                status=AgentPendingActionStatus.FAILED,
                capability=executing.capability,
                operation=executing.operation,
                code=error.code,
            )
            failed = self._repository.transition_exact(
                executing.action_id,
                AgentPendingActionStatus.FAILED,
                arguments_sha256=executing.arguments_sha256,
                channel=channel,
                principal=principal,
                expected_version=executing.version,
                token_sha256=_token_digest(token),
                result_receipt_json=receipt,
                now=self._clock.now(),
            )
            return PendingActionExecution(
                action=failed,
                result={"ok": False, "error": {"code": error.code}},
            )
        except Exception:
            receipt = _safe_receipt(
                status=AgentPendingActionStatus.UNKNOWN,
                capability=executing.capability,
                operation=executing.operation,
                code="AGENT_ACTION_UNKNOWN",
            )
            unknown = self._repository.transition_exact(
                executing.action_id,
                AgentPendingActionStatus.UNKNOWN,
                arguments_sha256=executing.arguments_sha256,
                channel=channel,
                principal=principal,
                expected_version=executing.version,
                token_sha256=_token_digest(token),
                result_receipt_json=receipt,
                now=self._clock.now(),
            )
            return PendingActionExecution(
                action=unknown,
                result={"ok": False, "error": {"code": "AGENT_ACTION_UNKNOWN"}},
            )


__all__ = [
    "AgentPendingActionService",
    "DEFAULT_ACTION_TTL_SECONDS",
    "PendingActionExecution",
    "PendingActionProposal",
    "pending_action_wire",
]
