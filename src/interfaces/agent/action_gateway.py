"""Transport-neutral Agent-D pending-action gateway.

Console and Telegram adapters share this class.  It exposes opaque one-time
tokens only from ``prepare``; confirmation never accepts caller-supplied model
arguments and always reuses the Compact registry validation path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from application.dto.review_item import ReviewItemTransitionInput
from application.ports.agent_action_gateway import AgentActionInvocationResult
from application.ports.agent_pending_action_repository import AgentPendingActionRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.services.agent_pending_action_service import (
    AgentPendingActionService,
    PendingActionExecution,
    PendingActionProposal,
)
from application.services.review_item_service import ReviewItemService
from domain.agent.enums import AgentChannel
from domain.agent.models import AgentPendingAction
from domain.common.errors import TradingPartnerError
from interfaces.agent.capability_gateway import compact_tool_result
from interfaces.mcp.tools.compact import (
    CapabilityEffect,
    CompactCapabilityRegistry,
)


class AgentActionGateway:
    """Build and execute pending actions behind a channel-neutral interface."""

    def __init__(self, service: AgentPendingActionService) -> None:
        self._service = service

    @classmethod
    def from_dependencies(
        cls,
        *,
        repository: AgentPendingActionRepository,
        registry: CompactCapabilityRegistry,
        clock: Clock,
        id_generator: IdGenerator,
        review_item_service: ReviewItemService | None = None,
    ) -> AgentActionGateway:
        return cls(
            AgentPendingActionService(
                repository=repository,
                operation_gateway=CompactAgentActionOperationGateway(
                    registry,
                    review_item_service=review_item_service,
                ),
                clock=clock,
                id_generator=id_generator,
            )
        )

    @property
    def service(self) -> AgentPendingActionService:
        return self._service

    def prepare(
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
        return self._service.propose(
            conversation_id=conversation_id,
            channel=channel,
            principal=principal,
            capability=capability,
            operation=operation,
            arguments=arguments,
            presented_summary=presented_summary,
        )

    def reissue_confirmation(
        self,
        *,
        action_id: str,
        conversation_id: str,
        channel: AgentChannel,
        principal: str,
        expected_version: int,
    ) -> PendingActionProposal:
        return self._service.reissue_confirmation(
            action_id=action_id,
            conversation_id=conversation_id,
            channel=channel,
            principal=principal,
            expected_version=expected_version,
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
        return await self._service.confirm(
            action_id=action_id,
            token=token,
            channel=channel,
            principal=principal,
            expected_version=expected_version,
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
        return self._service.reject(
            action_id=action_id,
            token=token,
            channel=channel,
            principal=principal,
            expected_version=expected_version,
        )

    def get(self, action_id: str) -> AgentPendingAction | None:
        return self._service.get(action_id)

    def get_by_token(
        self,
        token: str,
        *,
        channel: AgentChannel,
        principal: str,
    ) -> AgentPendingAction | None:
        return self._service.get_by_token(
            token,
            channel=channel,
            principal=principal,
        )

    def list(
        self,
        conversation_id: str,
        *,
        channel: AgentChannel,
        principal: str,
        include_terminal: bool = False,
        limit: int = 100,
    ) -> tuple[AgentPendingAction, ...]:
        return self._service.list(
            conversation_id,
            channel=channel,
            principal=principal,
            include_terminal=include_terminal,
            limit=limit,
        )


class CompactAgentActionOperationGateway:
    """Application-port adapter over the closed Compact registry."""

    def __init__(
        self,
        registry: CompactCapabilityRegistry,
        *,
        review_item_service: ReviewItemService | None = None,
    ) -> None:
        self._registry = registry
        self._review_item_service = review_item_service

    def validate_operation(
        self,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if capability == "decision_workbench_review_queue":
            return self._validate_review_transition(operation, arguments)
        descriptor = self._registry.find_operation(capability, operation)
        if descriptor.policy.effect not in {
            CapabilityEffect.APPEND,
            CapabilityEffect.APPEND_OPEN_WORLD,
            CapabilityEffect.MANAGE,
            CapabilityEffect.MANAGE_OPEN_WORLD,
        }:
            raise TradingPartnerError(
                "Agent action operation policy is not writable",
                code="AGENT_ACTION_NOT_ALLOWED",
            )
        return self._registry.validate_operation(capability, operation, dict(arguments))

    async def invoke_operation(
        self,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AgentActionInvocationResult:
        if capability == "decision_workbench_review_queue":
            return self._invoke_review_transition(operation, arguments)
        descriptor = self._registry.find_operation(capability, operation)
        if descriptor.policy.effect not in {
            CapabilityEffect.APPEND,
            CapabilityEffect.APPEND_OPEN_WORLD,
            CapabilityEffect.MANAGE,
            CapabilityEffect.MANAGE_OPEN_WORLD,
        }:
            raise TradingPartnerError(
                "Agent action operation policy is not writable",
                code="AGENT_ACTION_NOT_ALLOWED",
            )
        raw = await self._registry.invoke_validated(
            capability,
            operation,
            dict(arguments),
            enforce_confirmation=False,
        )
        if isinstance(raw, Mapping):
            error_code: object = raw.get("error_code")
            if error_code is None and isinstance(raw.get("error"), Mapping):
                error_code = raw["error"].get("code")
            if error_code is None and isinstance(raw.get("errors"), (list, tuple)):
                first_error = raw["errors"][0] if raw["errors"] else None
                if isinstance(first_error, Mapping):
                    error_code = first_error.get("code")
            if isinstance(error_code, str) and error_code.isascii() and len(error_code) <= 128:
                raise TradingPartnerError("Agent action failed", code=error_code)
        compacted = compact_tool_result(raw)
        receipt: dict[str, object] = {
            "status": "SUCCEEDED",
            "capability": capability,
            "operation": operation,
        }
        if isinstance(raw, Mapping):
            for key in ("request_id", "error_code"):
                value = raw.get(key)
                if isinstance(value, str) and value.isascii() and len(value) <= 160:
                    receipt[key] = value
            warnings = raw.get("warnings")
            if isinstance(warnings, (list, tuple)):
                warning_codes: list[str] = []
                for warning in warnings[:32]:
                    if not isinstance(warning, Mapping):
                        continue
                    warning_code = warning.get("code")
                    if isinstance(warning_code, str) and warning_code.isascii():
                        warning_codes.append(warning_code[:128])
                receipt["warning_codes"] = warning_codes
        return AgentActionInvocationResult(
            result=compacted,
            receipt_json=json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )[:16_384],
        )

    def _validate_review_transition(
        self,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate one exact Review Queue transition without registry writes."""

        if self._review_item_service is None:
            raise TradingPartnerError(
                "Review Queue action is unavailable",
                code="AGENT_ACTION_NOT_ALLOWED",
            )
        if operation not in {"acknowledge", "resolve"}:
            raise TradingPartnerError(
                "Review Queue action is not allowed",
                code="AGENT_ACTION_NOT_ALLOWED",
            )
        payload = dict(arguments)
        if payload.get("actor") != "user":
            raise TradingPartnerError(
                "Review Queue transitions require actor=user",
                code="AGENT_ACTION_NOT_ALLOWED",
            )
        payload["actor"] = "user"
        payload["status"] = "ACKNOWLEDGED" if operation == "acknowledge" else "RESOLVED"
        try:
            return ReviewItemTransitionInput.model_validate(payload).model_dump(mode="python")
        except Exception as exc:  # Pydantic validation is intentionally safe
            raise TradingPartnerError(
                "Review Queue transition arguments are invalid",
                code="AGENT_ACTION_SCHEMA_INVALID",
            ) from exc

    def _invoke_review_transition(
        self,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AgentActionInvocationResult:
        normalized = self._validate_review_transition(operation, arguments)
        assert self._review_item_service is not None
        result = self._review_item_service.transition(
            ReviewItemTransitionInput.model_validate(normalized)
        )
        payload = {
            "ok": True,
            "status": "SUCCEEDED",
            "capability": "decision_workbench_review_queue",
            "operation": operation,
            "data": result.model_dump(mode="json"),
        }
        receipt = {
            "status": "SUCCEEDED",
            "capability": "decision_workbench_review_queue",
            "operation": operation,
        }
        return AgentActionInvocationResult(
            result=compact_tool_result(
                payload,
                capability="decision_workbench_review_queue",
                operation=operation,
            ),
            receipt_json=json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )[:16_384],
        )


__all__ = ["AgentActionGateway", "CompactAgentActionOperationGateway"]
