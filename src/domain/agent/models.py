"""Bounded domain records for the shared Console/Telegram Agent Runtime.

The runtime stores conversation context and audit metadata only.  Provider
payloads, credentials, HTTP headers, and raw model responses deliberately have
no field in these records.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from domain.agent.enums import (
    AgentChannel,
    AgentConversationStatus,
    AgentMessageRole,
    AgentPendingActionStatus,
    AgentTurnStatus,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(
    value: object,
    field_name: str,
    maximum: int,
    *,
    allow_blank: bool = False,
) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise DataContractError(f"{field_name} must be bounded text")
    if not allow_blank and not value.strip():
        raise DataContractError(f"{field_name} must not be blank")
    # Conversation text and summaries retain their exact whitespace; identity
    # fields are normalized so equivalent lookups remain deterministic.
    if field_name in {"content", "rolling_summary", "presented_summary"}:
        return value
    return value.strip()


def _time(value: datetime, field_name: str) -> None:
    require_aware_datetime(value, field_name=field_name)


def _sequence(value: object, field_name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DataContractError(f"{field_name} must be an integer >= {minimum}")
    return value


def _enum(value: object, enum_type: type[Any], field_name: str) -> Any:
    if not isinstance(value, enum_type):
        raise DataContractError(f"{field_name} is invalid")
    return value


def _sha256(value: object, field_name: str = "arguments_sha256") -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DataContractError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def canonical_json(value: Mapping[str, object]) -> str:
    """Return bounded, deterministic JSON for pending-action arguments."""

    if not isinstance(value, Mapping):
        raise DataContractError("normalized_arguments must be an object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise DataContractError("normalized_arguments must be JSON serializable") from exc
    if len(encoded) > 64_000:
        raise DataContractError("normalized_arguments exceeds the durable bound")
    return encoded


def _json_default(value: object) -> object:
    """Bounded JSON normalization for Pydantic/DTO scalar values."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    # Decimal is intentionally rendered as text to preserve exact precision;
    # no financial arithmetic occurs in the pending-action record.
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def arguments_digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _codes(value: Sequence[str] | None, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise DataContractError(f"{field_name} must be a sequence of codes")
    if len(value) > 64:
        raise DataContractError(f"{field_name} contains too many codes")
    result: list[str] = []
    for item in value:
        result.append(_text(item, field_name, 128))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AgentConversation:
    conversation_id: str
    owner_principal: str
    title: str
    created_at: datetime
    updated_at: datetime
    status: AgentConversationStatus = AgentConversationStatus.ACTIVE
    rolling_summary: str = ""
    summary_through_sequence: int = 0
    next_message_sequence: int = 1
    version: int = 1

    def __post_init__(self) -> None:
        _text(self.conversation_id, "conversation_id", 160)
        _text(self.owner_principal, "owner_principal", 256)
        _text(self.title, "title", 240)
        _enum(self.status, AgentConversationStatus, "status")
        _text(self.rolling_summary, "rolling_summary", 32_000, allow_blank=True)
        _sequence(self.summary_through_sequence, "summary_through_sequence")
        _sequence(self.next_message_sequence, "next_message_sequence", minimum=1)
        _sequence(self.version, "version", minimum=1)
        _time(self.created_at, "created_at")
        _time(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must not precede created_at")


@dataclass(frozen=True, slots=True)
class AgentChannelBinding:
    binding_id: str
    conversation_id: str
    channel: AgentChannel
    external_conversation_ref: str
    created_at: datetime
    updated_at: datetime
    is_active: bool = True

    def __post_init__(self) -> None:
        _text(self.binding_id, "binding_id", 160)
        _text(self.conversation_id, "conversation_id", 160)
        _enum(self.channel, AgentChannel, "channel")
        _text(self.external_conversation_ref, "external_conversation_ref", 512)
        if type(self.is_active) is not bool:
            raise DataContractError("is_active must be a boolean")
        _time(self.created_at, "created_at")
        _time(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must not precede created_at")


@dataclass(frozen=True, slots=True)
class AgentMessage:
    message_id: str
    conversation_id: str
    role: AgentMessageRole
    content: str
    created_at: datetime
    sequence: int = 0
    channel: AgentChannel | None = None
    external_message_ref: str | None = None
    model: str | None = None
    request_id: str | None = None
    model_receipt_json: str | None = None

    def __post_init__(self) -> None:
        _text(self.message_id, "message_id", 160)
        _text(self.conversation_id, "conversation_id", 160)
        _enum(self.role, AgentMessageRole, "role")
        _text(self.content, "content", 256_000, allow_blank=True)
        _sequence(self.sequence, "sequence")
        if self.channel is not None:
            _enum(self.channel, AgentChannel, "channel")
        if self.external_message_ref is not None:
            _text(self.external_message_ref, "external_message_ref", 512)
            if self.channel is None:
                raise DataContractError("external_message_ref requires a channel")
        if self.model is not None:
            _text(self.model, "model", 256)
        if self.request_id is not None:
            _text(self.request_id, "request_id", 160)
        if self.model_receipt_json is not None:
            _text(self.model_receipt_json, "model_receipt_json", 16_384, allow_blank=True)
        _time(self.created_at, "created_at")


_SAFE_ERROR_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """Durable lifecycle record for one user message/model execution.

    Only a bounded machine error code is persisted on failure.  Exception
    messages, response bodies and credentials deliberately have no storage
    field and are never copied into this record.
    """

    turn_id: str
    conversation_id: str
    user_message_id: str
    channel: AgentChannel
    status: AgentTurnStatus
    started_at: datetime
    updated_at: datetime
    assistant_message_id: str | None = None
    model_id: str | None = None
    reasoning_effort: str | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _text(self.turn_id, "turn_id", 160)
        _text(self.conversation_id, "conversation_id", 160)
        _text(self.user_message_id, "user_message_id", 160)
        if self.assistant_message_id is not None:
            _text(self.assistant_message_id, "assistant_message_id", 160)
        _enum(self.channel, AgentChannel, "channel")
        _enum(self.status, AgentTurnStatus, "status")
        if self.model_id is not None:
            _text(self.model_id, "model_id", 256)
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "low",
            "medium",
            "high",
            "max",
        }:
            raise DataContractError("reasoning_effort is invalid")
        if self.error_code is not None and (
            not isinstance(self.error_code, str)
            or _SAFE_ERROR_CODE.fullmatch(self.error_code) is None
        ):
            raise DataContractError("error_code must be a safe bounded code")
        _sequence(self.version, "version", minimum=1)
        _time(self.started_at, "started_at")
        _time(self.updated_at, "updated_at")
        if self.updated_at < self.started_at:
            raise DataContractError("updated_at must not precede started_at")
        if self.completed_at is not None:
            _time(self.completed_at, "completed_at")
            if self.completed_at < self.started_at:
                raise DataContractError("completed_at must not precede started_at")
        terminal = self.status in {
            AgentTurnStatus.COMPLETED,
            AgentTurnStatus.FAILED,
            AgentTurnStatus.CANCELLED,
        }
        if terminal and self.completed_at is None:
            raise DataContractError("terminal Agent turns require completed_at")
        if not terminal and self.completed_at is not None:
            raise DataContractError("active Agent turns cannot have completed_at")

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            AgentTurnStatus.COMPLETED,
            AgentTurnStatus.FAILED,
            AgentTurnStatus.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class AgentToolReceipt:
    receipt_id: str
    conversation_id: str
    capability: str
    operation: str
    arguments_sha256: str
    request_id: str
    created_at: datetime
    message_id: str | None = None
    source_codes: tuple[str, ...] = field(default_factory=tuple)
    warning_codes: tuple[str, ...] = field(default_factory=tuple)
    error_codes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _text(self.receipt_id, "receipt_id", 160)
        _text(self.conversation_id, "conversation_id", 160)
        _text(self.capability, "capability", 160)
        _text(self.operation, "operation", 160)
        _sha256(self.arguments_sha256)
        _text(self.request_id, "request_id", 160)
        if self.message_id is not None:
            _text(self.message_id, "message_id", 160)
        _codes(self.source_codes, "source_codes")
        _codes(self.warning_codes, "warning_codes")
        _codes(self.error_codes, "error_codes")
        _time(self.created_at, "created_at")

    @property
    def arguments_hash(self) -> str:
        return self.arguments_sha256


@dataclass(frozen=True, slots=True)
class AgentPendingAction:
    action_id: str
    conversation_id: str
    channel: AgentChannel
    principal: str
    normalized_arguments: Mapping[str, object]
    arguments_sha256: str
    presented_summary: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    status: AgentPendingActionStatus = AgentPendingActionStatus.PROPOSED
    version: int = 1
    # Action routing and confirmation secrets are persisted separately from the
    # normalized arguments.  ``None`` keeps pre-0045 rows readable.
    capability: str = ""
    operation: str = ""
    token_sha256: str | None = None
    result_receipt_json: str | None = None

    def __post_init__(self) -> None:
        _text(self.action_id, "action_id", 160)
        _text(self.conversation_id, "conversation_id", 160)
        _enum(self.channel, AgentChannel, "channel")
        _text(self.principal, "principal", 256)
        expected_digest = arguments_digest(self.normalized_arguments)
        _sha256(self.arguments_sha256)
        if expected_digest != self.arguments_sha256:
            raise DataContractError("arguments_sha256 does not match normalized_arguments")
        _text(self.presented_summary, "presented_summary", 16_000, allow_blank=True)
        _text(
            self.capability,
            "capability",
            160,
            allow_blank=self.token_sha256 is None,
        )
        _text(
            self.operation,
            "operation",
            160,
            allow_blank=self.token_sha256 is None,
        )
        if self.token_sha256 is not None:
            _sha256(self.token_sha256, "token_sha256")
        if self.result_receipt_json is not None:
            _text(self.result_receipt_json, "result_receipt_json", 16_384, allow_blank=True)
        _enum(self.status, AgentPendingActionStatus, "status")
        _sequence(self.version, "version", minimum=1)
        _time(self.expires_at, "expires_at")
        _time(self.created_at, "created_at")
        _time(self.updated_at, "updated_at")
        if self.expires_at <= self.created_at:
            raise DataContractError("expires_at must follow created_at")
        if self.updated_at < self.created_at:
            raise DataContractError("updated_at must not precede created_at")

    @property
    def arguments_hash(self) -> str:
        return self.arguments_sha256


@dataclass(frozen=True, slots=True)
class AgentChannelCursor:
    cursor_id: str
    channel: AgentChannel
    cursor_key: str
    last_update_id: int
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        _text(self.cursor_id, "cursor_id", 160)
        _enum(self.channel, AgentChannel, "channel")
        _text(self.cursor_key, "cursor_key", 256)
        if type(self.last_update_id) is not int or self.last_update_id < -1:
            raise DataContractError("last_update_id must be an integer >= -1")
        _sequence(self.version, "version", minimum=1)
        _time(self.updated_at, "updated_at")

    @property
    def update_id(self) -> int:
        return self.last_update_id


@dataclass(frozen=True, slots=True)
class AgentChannelHandoff:
    """One-time, cross-channel conversation handoff without stored token text."""

    handoff_id: str
    conversation_id: str
    owner_principal: str
    target_channel: AgentChannel
    token_sha256: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _text(self.handoff_id, "handoff_id", 160)
        _text(self.conversation_id, "conversation_id", 160)
        _text(self.owner_principal, "owner_principal", 256)
        _enum(self.target_channel, AgentChannel, "target_channel")
        _sha256(self.token_sha256, "token_sha256")
        _sequence(self.version, "version", minimum=1)
        _time(self.expires_at, "expires_at")
        _time(self.created_at, "created_at")
        if self.expires_at <= self.created_at:
            raise DataContractError("handoff expires_at must follow created_at")
        if self.consumed_at is not None:
            _time(self.consumed_at, "consumed_at")


Handoff = AgentChannelHandoff


# Compatibility aliases for callers that use the shorter entity names.
Conversation = AgentConversation
ChannelBinding = AgentChannelBinding
Message = AgentMessage
ToolReceipt = AgentToolReceipt
PendingAction = AgentPendingAction
ChannelCursor = AgentChannelCursor
