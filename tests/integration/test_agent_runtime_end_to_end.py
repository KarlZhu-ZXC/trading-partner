"""End-to-end checks for the shared Agent Runtime composition boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from application.dto.agent import AgentTurnEvent, AgentTurnRequest
from application.ports.agent_model_provider import (
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)
from application.services.agent_context_service import AgentContextService
from application.services.agent_runtime_service import AgentRuntimeService
from bootstrap import build_application
from domain.agent.enums import AgentChannel, AgentPendingActionStatus
from domain.common.enums import ResearchSubjectType, VendorId
from domain.common.errors import PersistenceError
from domain.portfolio.enums import AccountEnvironment, AccountPositionSide
from domain.portfolio.models import AccountPosition, AccountSnapshot
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.agent_conversation_repository import (
    SqlAlchemyAgentConversationRepository,
)
from infrastructure.persistence.agent_pending_action_repository import (
    SqlAlchemyAgentPendingActionRepository,
)
from interfaces.agent.action_gateway import AgentActionGateway
from interfaces.agent.capability_gateway import AgentCapabilityGateway
from interfaces.agent.prompts import AGENT_SYSTEM_PROMPT
from interfaces.mcp.server import create_capability_registry


class FakeModelProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)

    async def aclose(self) -> None:
        return None


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> ModelToolCall:
    return ModelToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


@pytest.mark.asyncio
async def test_agent_runtime_reads_durable_positions_and_confirms_research_write(
    migrated_sqlite_url: str,
    test_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise read receipts and the explicit channel/token action gate together."""

    settings = test_settings.model_copy(update={"database_url": migrated_sqlite_url})
    container = build_application(settings)
    try:
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        snapshot = AccountSnapshot(
            snapshot_id="snapshot_runtime_positions",
            account_ref="manual-runtime-account",
            provider=VendorId.MANUAL_CSV,
            environment=AccountEnvironment.MANUAL,
            base_currency="USD",
            account_as_of=timestamp,
            fetched_at=timestamp,
            cash=Decimal("10000"),
            buying_power=Decimal("10000"),
            net_assets=Decimal("15000"),
            margin_used=Decimal("0"),
            positions=(
                AccountPosition(
                    instrument_id="equity:US:NVDA",
                    side=AccountPositionSide.LONG,
                    quantity=Decimal("2"),
                    sellable_quantity=Decimal("2"),
                    average_cost=Decimal("100"),
                    diluted_cost=Decimal("100"),
                    market_price=Decimal("120"),
                    market_price_at=timestamp,
                    market_value=Decimal("240"),
                    unrealized_pnl=Decimal("40"),
                    realized_pnl=Decimal("0"),
                    currency="USD",
                ),
            ),
            open_orders=(),
            degraded=False,
            warning_codes=(),
        )
        SqlAlchemyAccountSnapshotRepository(
            container.resources.database.engine
        ).append_account(snapshot)

        created = container.services.research_subjects.create_subject(
            subject_type=ResearchSubjectType.THEME,
            title="Runtime research scope",
            summary="A durable scope used by the runtime integration test.",
            primary_instrument_id=None,
            topic_tags=(),
            linked_subject_ids=(),
            confirmed_by="user",
            idempotency_key="runtime-subject-create",
        )
        assert created.ok and created.data is not None
        subject_id = created.data.subject_id

        # A durable positions read must never fall through to AccountService.refresh.
        async def unexpected_refresh(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("Agent positions read unexpectedly refreshed an account")

        monkeypatch.setattr(
            container.services.portfolio,
            "get_account_snapshot",
            unexpected_refresh,
        )

        registry = create_capability_registry(container)
        read_gateway = AgentCapabilityGateway(registry)
        action_gateway = AgentActionGateway.from_dependencies(
            repository=SqlAlchemyAgentPendingActionRepository(
                container.resources.database.engine
            ),
            registry=registry,
            clock=container.context.clock,
            id_generator=container.context.id_generator,
        )
        conversation_repository = SqlAlchemyAgentConversationRepository(
            container.resources.database.engine
        )
        context = AgentContextService(
            repository=conversation_repository,
            clock=container.context.clock,
            id_generator=container.context.id_generator,
        )
        conversation = context.create_conversation(
            owner_principal="local-console",
            title="Runtime integration",
        )
        action_arguments = {
            "case_id": subject_id,
            "title": "Runtime research scope (confirmed)",
            "summary": "Updated scope after an explicit current-channel confirmation.",
            "reviewed_by": "external_agent",
            "idempotency_key": "runtime-subject-update",
        }
        model = FakeModelProvider(
            [
                ModelResponse(
                    tool_calls=(
                        _call(
                            "search_positions",
                            "tp_capability_search",
                            {"query": "positions"},
                        ),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        _call(
                            "read_positions",
                            "tp_read",
                            {
                                "capability": "account_get",
                                "operation": "positions",
                                "arguments": {"snapshot_id": snapshot.snapshot_id},
                            },
                        ),
                    )
                ),
                ModelResponse(text="已读取本地持仓快照。"),
                ModelResponse(
                    tool_calls=(
                        _call(
                            "prepare_update",
                            "tp_prepare_action",
                            {
                                "capability": "investment_case_manage",
                                "operation": "update",
                                "arguments": action_arguments,
                                "presented_summary": "更新研究标的元数据，等待当前会话确认。",
                            },
                        ),
                    )
                ),
                ModelResponse(text="已生成待确认研究更新动作，尚未写入。"),
            ]
        )
        runtime = AgentRuntimeService(
            repository=conversation_repository,
            context_service=context,
            model_provider=model,
            tool_gateway=read_gateway,
            clock=container.context.clock,
            id_generator=container.context.id_generator,
            system_prompt=AGENT_SYSTEM_PROMPT,
            pending_action_gateway=action_gateway,
        )

        events: list[AgentTurnEvent] = []

        async def capture(event: AgentTurnEvent) -> None:
            events.append(event)

        read_result = await runtime.run_turn(
            AgentTurnRequest(
                conversation_id=conversation.conversation_id,
                owner_principal="local-console",
                channel=AgentChannel.CONSOLE,
                content="读取我的持仓。",
            ),
            event_sink=capture,
        )
        assert read_result.text == "已读取本地持仓快照。"
        assert read_result.tool_trace == ("tp_capability_search", "tp_read")
        assert len(read_result.tool_receipts) == 1
        assert read_result.tool_receipts[0].operation == "positions"
        assert {tool.name for tool in model.requests[0].tools} == {
            "tp_capability_search",
            "tp_read",
            "tp_prepare_action",
        }
        assert any(
            "equity:US:NVDA" in (message.content or "")
            for request in model.requests
            for message in request.messages
            if getattr(message, "role", None) == "tool"
        )

        write_result = await runtime.run_turn(
            AgentTurnRequest(
                conversation_id=conversation.conversation_id,
                owner_principal="local-console",
                channel=AgentChannel.CONSOLE,
                content="更新这个研究标的的范围。",
            ),
            event_sink=capture,
        )
        assert "尚未写入" in write_result.text
        assert "tp_confirm" not in write_result.tool_trace
        pending_event = next(event for event in events if event.type == "pending_action")
        pending_wire = pending_event.data["pending_action"]
        assert isinstance(pending_wire, dict)
        action_id = pending_wire["action_id"]
        token = pending_event.data["confirmation_token"]
        assert isinstance(action_id, str) and isinstance(token, str)
        pending_repository = SqlAlchemyAgentPendingActionRepository(
            container.resources.database.engine
        )
        pending = pending_repository.get_pending_action(action_id)
        assert pending is not None
        assert pending.status is AgentPendingActionStatus.PRESENTED
        assert container.services.research_subjects.get_subject(subject_id).data.title == (
            "Runtime research scope"
        )

        with pytest.raises(PersistenceError, match="identity mismatch"):
            await action_gateway.confirm(
                action_id=action_id,
                token=token,
                channel=AgentChannel.CONSOLE,
                principal="wrong-principal",
                expected_version=pending.version,
            )
        still_pending = pending_repository.get_pending_action(action_id)
        assert still_pending is not None
        assert still_pending.status is AgentPendingActionStatus.PRESENTED

        execution = await action_gateway.confirm(
            action_id=action_id,
            token=token,
            channel=AgentChannel.CONSOLE,
            principal="local-console",
            expected_version=pending.version,
        )
        assert execution.action.status is AgentPendingActionStatus.SUCCEEDED
        updated = container.services.research_subjects.get_subject(subject_id)
        assert updated.ok and updated.data is not None
        assert updated.data.title == "Runtime research scope (confirmed)"

        messages = conversation_repository.list_messages(
            conversation.conversation_id,
            limit=20,
        )
        assert [item.role.value for item in messages] == [
            "USER",
            "ASSISTANT",
            "USER",
            "ASSISTANT",
        ]
        receipts = conversation_repository.list_tool_receipts(
            conversation.conversation_id,
            limit=20,
        )
        assert len(receipts) == 1
        assert receipts[0].capability == "account_get"
        assert receipts[0].operation == "positions"
    finally:
        await container.aclose()
