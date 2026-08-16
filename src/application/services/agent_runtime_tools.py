"""Typed Agent tool-call collaborator used by the shared runtime loop.

The runtime owns turn orchestration and event ordering.  This collaborator
owns the model-facing tool boundary: bounded argument validation, capability
dispatch, confirmation preparation, and durable read receipts.  Keeping that
boundary here prevents the turn loop from depending on gateway-specific
receipt or schema details while preserving the existing wire results.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, cast

from application.ports.agent_action_gateway import AgentPendingActionGateway
from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.agent_tool_gateway import (
    AgentToolDescriptor,
    AgentToolGateway,
    AgentToolReceipt,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.services.agent_pending_action_service import (
    PendingActionProposal,
    pending_action_wire,
)
from application.services.agent_runtime_receipts import tool_error
from domain.agent.enums import AgentChannel
from domain.agent.models import AgentToolReceipt as DurableToolReceipt
from domain.agent.models import arguments_digest
from domain.common.errors import TradingPartnerError
from domain.common.ids import EntityIdPrefix

_SAFE_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")
_SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9._-]+\.png$")
_SAFE_ARTIFACT_URL = re.compile(r"^/api/agent/artifacts/[A-Za-z0-9._-]+\.png$")

ToolCallOutcome = tuple[
    object,
    AgentToolReceipt | None,
    tuple[PendingActionProposal, str] | None,
]
CapabilitySearcher = Callable[
    [str, int, str],
    tuple[AgentToolDescriptor, ...],
]


def _chart_artifact_url(value: object) -> str | None:
    """Extract only a persisted PNG basename from a compact chart result."""

    if isinstance(value, Mapping):
        direct_url = value.get("artifact_url")
        if isinstance(direct_url, str) and _SAFE_ARTIFACT_URL.fullmatch(direct_url):
            return direct_url
        artifact = value.get("chart_artifact")
        if isinstance(artifact, Mapping):
            raw_path = artifact.get("path")
            if isinstance(raw_path, str):
                basename = raw_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if _SAFE_ARTIFACT_NAME.fullmatch(basename):
                    return f"/api/agent/artifacts/{basename}"
        for item in value.values():
            found = _chart_artifact_url(item)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _chart_artifact_url(item)
            if found is not None:
                return found
    return None


class AgentRuntimeToolHandler:
    """Dispatch one model tool call and persist read-only tool metadata."""

    def __init__(
        self,
        *,
        gateway: AgentToolGateway,
        repository: AgentConversationRepository,
        clock: Clock,
        id_generator: IdGenerator,
        search_capabilities: CapabilitySearcher,
        pending_action_gateway: AgentPendingActionGateway | None = None,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator
        self._search_capabilities = search_capabilities
        self._pending_action_gateway = pending_action_gateway

    def validation_hint(
        self,
        *,
        tool_name: str,
        decoded: Mapping[str, Any] | None,
    ) -> dict[str, list[str]]:
        """Return only safe schema field names, never provider exception text."""

        missing: list[str] = []
        invalid: list[str] = []

        def add_missing(values: object) -> None:
            if not isinstance(values, (list, tuple)):
                return
            for item in values:
                if (
                    isinstance(item, str)
                    and _SAFE_FIELD_NAME.fullmatch(item)
                    and item not in missing
                ):
                    missing.append(item)

        def add_invalid(values: object) -> None:
            if not isinstance(values, (list, tuple)):
                return
            for item in values:
                if (
                    isinstance(item, str)
                    and _SAFE_FIELD_NAME.fullmatch(item)
                    and item not in invalid
                ):
                    invalid.append(item)

        if not isinstance(decoded, Mapping):
            return {"missing": missing, "invalid": invalid}
        if tool_name == "tp_capability_search":
            add_missing([key for key in ("query",) if key not in decoded])
            add_invalid([key for key in decoded if key not in {"query", "limit", "mode"}])
            if "query" in decoded and (
                not isinstance(decoded.get("query"), str) or not str(decoded.get("query")).strip()
            ):
                add_invalid(["query"])
            if "limit" in decoded and (
                type(decoded.get("limit")) is not int or not 1 <= int(decoded.get("limit", 0)) <= 8
            ):
                add_invalid(["limit"])
            if "mode" in decoded and decoded.get("mode") not in {
                "read",
                "propose",
                "prepare_action",
            }:
                add_invalid(["mode"])
        elif tool_name in {"tp_read", "tp_propose"}:
            add_missing([key for key in ("capability", "arguments") if key not in decoded])
            add_invalid(
                [key for key in decoded if key not in {"capability", "operation", "arguments"}]
            )
            capability = decoded.get("capability")
            operation = decoded.get("operation")
            arguments = decoded.get("arguments")
            if not isinstance(capability, str) or not capability.strip():
                add_invalid(["capability"])
            if operation is not None and not isinstance(operation, str):
                add_invalid(["operation"])
            if not isinstance(arguments, Mapping):
                add_invalid(["arguments"])
            descriptor_getter = getattr(self._gateway, "descriptor", None)
            descriptor: AgentToolDescriptor | None = None
            if callable(descriptor_getter) and isinstance(capability, str):
                try:
                    descriptor = cast(
                        AgentToolDescriptor | None,
                        descriptor_getter(
                            capability,
                            operation if isinstance(operation, str) else None,
                        ),
                    )
                except (LookupError, PermissionError, TypeError, ValueError):
                    descriptor = None
            if descriptor is not None and isinstance(arguments, Mapping):
                try:
                    schema = descriptor.arguments_schema
                except (AttributeError, TypeError, ValueError):
                    schema = {}
                if isinstance(schema, Mapping):
                    properties = schema.get("properties")
                    required = schema.get("required")
                    if isinstance(required, list):
                        add_missing([key for key in required if key not in arguments])
                    if (
                        isinstance(properties, Mapping)
                        and schema.get("additionalProperties") is False
                    ):
                        add_invalid([key for key in arguments if key not in properties])
        elif tool_name == "tp_prepare_action":
            add_missing(
                [
                    key
                    for key in ("capability", "operation", "arguments", "presented_summary")
                    if key not in decoded
                ]
            )
            add_invalid(
                [
                    key
                    for key in decoded
                    if key not in {"capability", "operation", "arguments", "presented_summary"}
                ]
            )
            if (
                not isinstance(decoded.get("capability"), str)
                or not str(decoded.get("capability", "")).strip()
            ):
                add_invalid(["capability"])
            if (
                not isinstance(decoded.get("operation"), str)
                or not str(decoded.get("operation", "")).strip()
            ):
                add_invalid(["operation"])
            if not isinstance(decoded.get("arguments"), Mapping):
                add_invalid(["arguments"])
            summary = decoded.get("presented_summary")
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 2_000:
                add_invalid(["presented_summary"])
        missing.sort()
        invalid.sort()
        return {"missing": missing, "invalid": invalid}

    async def handle(
        self,
        *,
        call_name: str,
        call_arguments: str,
        conversation_id: str,
        message_id: str,
        channel: AgentChannel,
        principal: str,
        capability_search_cache: dict[tuple[str, int, str], object] | None = None,
    ) -> ToolCallOutcome:
        """Handle one model call without exposing raw provider failures."""

        try:
            decoded = json.loads(call_arguments)
        except (TypeError, json.JSONDecodeError):
            return tool_error("AGENT_TOOL_ARGUMENTS_INVALID"), None, None
        if not isinstance(decoded, dict):
            return tool_error("AGENT_TOOL_ARGUMENTS_INVALID"), None, None
        if call_name == "tp_capability_search":
            return self._handle_capability_search(
                decoded,
                capability_search_cache=capability_search_cache,
            )
        if call_name == "tp_prepare_action":
            return self._handle_prepare_action(
                decoded,
                conversation_id=conversation_id,
                channel=channel,
                principal=principal,
            )
        if call_name not in {"tp_read", "tp_propose"}:
            return tool_error("AGENT_TOOL_UNKNOWN"), None, None
        return await self._handle_read_or_propose(
            call_name=call_name,
            decoded=decoded,
            conversation_id=conversation_id,
            message_id=message_id,
        )

    def _handle_capability_search(
        self,
        decoded: dict[str, Any],
        *,
        capability_search_cache: dict[tuple[str, int, str], object] | None,
    ) -> ToolCallOutcome:
        if capability_search_cache is None:
            capability_search_cache = {}
        query = decoded.get("query")
        limit = decoded.get("limit", 8)
        mode = decoded.get("mode", "read")
        hints = self.validation_hint(tool_name="tp_capability_search", decoded=decoded)
        if (
            not isinstance(query, str)
            or not query.strip()
            or type(limit) is not int
            or mode not in {"read", "propose", "prepare_action"}
        ):
            return tool_error("AGENT_TOOL_SCHEMA_INVALID", **hints), None, None
        try:
            bounded_limit = min(max(limit, 1), 8)
            cache_key = (" ".join(query.casefold().split()), bounded_limit, mode)
            cached = capability_search_cache.get(cache_key)
            if cached is not None:
                return cast(ToolCallOutcome, (cached, None, None))
            descriptors = self._search_capabilities(query, bounded_limit, mode)
        except TradingPartnerError as exc:
            return tool_error(exc.code), None, None
        except (LookupError, PermissionError, ValueError):
            return tool_error("AGENT_CAPABILITY_SEARCH_FAILED"), None, None
        payload: dict[str, object] = {
            "capabilities": [item.as_dict() for item in descriptors]
        }
        audit_method = getattr(self._gateway, "search_audit", None)
        if callable(audit_method):
            try:
                audit = audit_method(query, bounded_limit, mode=mode)
            except (LookupError, PermissionError, TypeError, ValueError, TradingPartnerError):
                audit = None
            if isinstance(audit, Mapping):
                payload["routing_audit"] = dict(audit)
        capability_search_cache[cache_key] = payload
        return payload, None, None

    def _handle_prepare_action(
        self,
        decoded: dict[str, Any],
        *,
        conversation_id: str,
        channel: AgentChannel,
        principal: str,
    ) -> ToolCallOutcome:
        if self._pending_action_gateway is None:
            return (
                {
                    "ok": False,
                    "status": "DISABLED",
                    "error": {
                        "code": "AGENT_ACTIONS_DISABLED",
                        "message": "当前 Agent 动作未配置；动作未写入、未确认、未执行。",
                    },
                },
                None,
                None,
            )
        capability = decoded.get("capability")
        operation = decoded.get("operation")
        arguments = decoded.get("arguments")
        summary = decoded.get("presented_summary", "")
        if (
            not isinstance(capability, str)
            or not isinstance(operation, str)
            or not isinstance(arguments, Mapping)
            or not isinstance(summary, str)
            or len(summary) > 2_000
        ):
            return (
                tool_error(
                    "AGENT_TOOL_SCHEMA_INVALID",
                    **self.validation_hint(tool_name="tp_prepare_action", decoded=decoded),
                ),
                None,
                None,
            )
        try:
            proposal = self._pending_action_gateway.prepare(
                conversation_id=conversation_id,
                channel=channel,
                principal=principal,
                capability=capability,
                operation=operation,
                arguments=arguments,
                presented_summary=summary,
            )
        except TradingPartnerError as exc:
            if exc.code == "AGENT_ACTION_SCHEMA_INVALID":
                return (
                    tool_error(
                        exc.code,
                        **self.validation_hint(tool_name="tp_prepare_action", decoded=decoded),
                    ),
                    None,
                    None,
                )
            return tool_error(exc.code), None, None
        typed_proposal = cast(PendingActionProposal, proposal)
        return (
            {
                "ok": True,
                "status": typed_proposal.action.status.value,
                "pending_action": pending_action_wire(typed_proposal.action),
            },
            None,
            (typed_proposal, typed_proposal.confirmation_token),
        )

    async def _handle_read_or_propose(
        self,
        *,
        call_name: str,
        decoded: dict[str, Any],
        conversation_id: str,
        message_id: str,
    ) -> ToolCallOutcome:
        capability = decoded.get("capability")
        operation = decoded.get("operation")
        arguments = decoded.get("arguments")
        if (
            not isinstance(capability, str)
            or (operation is not None and not isinstance(operation, str))
            or (call_name == "tp_propose" and not isinstance(operation, str))
            or not isinstance(arguments, Mapping)
        ):
            return (
                tool_error(
                    "AGENT_TOOL_SCHEMA_INVALID",
                    **self.validation_hint(tool_name=call_name, decoded=decoded),
                ),
                None,
                None,
            )
        try:
            result = (
                await self._gateway.propose(capability, operation, arguments)
                if call_name == "tp_propose" and isinstance(operation, str)
                else await self._gateway.read(capability, operation, arguments)
            )
        except TradingPartnerError as exc:
            return tool_error(exc.code), None, None
        except (LookupError, PermissionError, ValueError):
            return (
                tool_error(
                    "AGENT_TOOL_PROPOSE_DENIED"
                    if call_name == "tp_propose"
                    else "AGENT_TOOL_READ_DENIED",
                    **self.validation_hint(tool_name=call_name, decoded=decoded),
                ),
                None,
                None,
            )
        except Exception as exc:  # noqa: BLE001 - classify without exposing provider text
            if exc.__class__.__name__ == "ToolError":
                return (
                    tool_error(
                        "AGENT_TOOL_SCHEMA_INVALID",
                        **self.validation_hint(tool_name=call_name, decoded=decoded),
                    ),
                    None,
                    None,
                )
            return tool_error(
                "AGENT_TOOL_PROPOSE_DENIED"
                if call_name == "tp_propose"
                else "AGENT_TOOL_READ_DENIED"
            ), None, None
        typed_result = result
        receipt = typed_result.receipt
        durable = DurableToolReceipt(
            receipt_id=self._id_generator.new(EntityIdPrefix.AGENT_TOOL_RECEIPT),
            conversation_id=conversation_id,
            message_id=message_id,
            capability=capability,
            operation=operation or "__direct__",
            arguments_sha256=arguments_digest(dict(arguments)),
            request_id=receipt.request_id or "unavailable",
            source_codes=receipt.source_codes,
            warning_codes=receipt.warning_codes,
            error_codes=(receipt.error_code,) if receipt.error_code else (),
            created_at=self._clock.now(),
        )
        self._repository.append_tool_receipt(durable)
        read_payload = cast(dict[str, object], typed_result.as_dict())
        if call_name == "tp_read" and capability == "technical_render_chart":
            artifact_url = _chart_artifact_url(typed_result.result)
            if artifact_url is not None:
                read_payload["artifact_url"] = artifact_url
        return read_payload, receipt, None


__all__ = ["AgentRuntimeToolHandler", "CapabilitySearcher", "ToolCallOutcome"]
