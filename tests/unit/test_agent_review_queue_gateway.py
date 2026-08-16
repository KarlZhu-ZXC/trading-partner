"""Private Agent Review Queue routing and confirmation contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from application.services.agent_pending_action_service import AgentPendingActionService
from application.services.review_item_service import ReviewItemService
from domain.agent.enums import AgentChannel, AgentPendingActionStatus
from domain.common.errors import TradingPartnerError
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection
from infrastructure.persistence.agent_pending_action_repository import (
    SqlAlchemyAgentPendingActionRepository,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository
from infrastructure.system.id_generator import Uuid7IdGenerator
from interfaces.agent.action_gateway import CompactAgentActionOperationGateway
from interfaces.agent.capability_gateway import AgentCapabilityGateway
from interfaces.mcp.tools.compact import CompactCapabilityRegistry


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 13, 10, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self) -> None:
        self.count = 0

    def new(self, _prefix: object) -> str:
        self.count += 1
        return f"review_item_{self.count}"


def _service() -> tuple[ReviewItemService, _Clock]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    clock = _Clock()
    return ReviewItemService(SqlAlchemyReviewItemRepository(engine), clock, _Ids()), clock


def _create_item(service: ReviewItemService) -> str:
    item = service.reconcile(
        (
            ReviewItemProjection(
                source_key="agenda-overdue-1",
                source_type=ReviewItemSourceType.CATALYST_AGENDA,
                source_ref="agenda_1",
                subject_id="case_1",
                title="Outcome overdue",
                detail="Link the durable outcome.",
                severity=ReviewItemSeverity.ATTENTION,
                recommended_action="LINK_OUTCOME",
                href="/agenda#agenda_1",
            ),
        ),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    return item.review_item_id


@pytest.mark.asyncio
async def test_review_queue_is_private_durable_only_and_has_routing_metadata() -> None:
    service, clock = _service()
    _create_item(service)
    gateway = AgentCapabilityGateway(
        CompactCapabilityRegistry(),
        review_item_service=service,
        clock=clock,
    )

    descriptors = gateway.search("review queue", limit=8)
    assert {item.operation for item in descriptors} == {"open_items", "summary"}
    assert all(item.effect == "READ_DURABLE" for item in descriptors)
    assert all(item.routing["reason"] == "exact_match" for item in descriptors)
    assert all("query_sha256" in item.routing for item in descriptors)
    assert all("review queue" not in str(item.routing) for item in descriptors)

    result = await gateway.read("decision_workbench_review_queue", "open_items", {})
    assert result.result["freshness"] == "durable"
    assert result.result["data"]["items"][0]["href"] == "/agenda#agenda_1"
    assert result.receipt.source_codes == ("PRIMARY:review_queue",)


@pytest.mark.asyncio
async def test_review_queue_actions_require_user_identity_version_and_resolution_note() -> None:
    service, _clock = _service()
    item_id = _create_item(service)
    gateway = CompactAgentActionOperationGateway(
        CompactCapabilityRegistry(),
        review_item_service=service,
    )
    base = {
        "review_item_id": item_id,
        "expected_version": 1,
        "idempotency_key": "agent-ack-1",
        "authorization_note": "User acknowledged this queue item in Console.",
        "actor": "user",
    }
    normalized = gateway.validate_operation(
        "decision_workbench_review_queue", "acknowledge", base
    )
    assert normalized["status"] == "ACKNOWLEDGED"
    assert normalized["actor"] == "user"
    acknowledged = await gateway.invoke_operation(
        "decision_workbench_review_queue", "acknowledge", normalized
    )
    assert acknowledged.result["data"]["status"] == "ACKNOWLEDGED"

    resolve_args = {
        **base,
        "expected_version": 2,
        "idempotency_key": "agent-resolve-1",
        "resolution_note": "User linked the durable outcome.",
    }
    resolved = await gateway.invoke_operation(
        "decision_workbench_review_queue",
        "resolve",
        resolve_args,
    )
    assert resolved.result["data"]["status"] == "RESOLVED"

    with pytest.raises(TradingPartnerError) as bad_actor:
        gateway.validate_operation(
            "decision_workbench_review_queue",
            "acknowledge",
            {**base, "actor": "external_agent"},
        )
    assert bad_actor.value.code == "AGENT_ACTION_NOT_ALLOWED"

    with pytest.raises(TradingPartnerError) as missing_note:
        gateway.validate_operation(
            "decision_workbench_review_queue",
            "resolve",
            {**base, "expected_version": 2, "idempotency_key": "missing-note"},
        )
    assert missing_note.value.code == "AGENT_ACTION_SCHEMA_INVALID"


def test_review_queue_prepare_action_search_is_allowlisted_only() -> None:
    service, _clock = _service()
    gateway = AgentCapabilityGateway(
        CompactCapabilityRegistry(),
        review_item_service=service,
        action_allowlist=(
            ("decision_workbench_review_queue", "acknowledge"),
        ),
    )
    descriptors = gateway.search("review", mode="prepare_action", limit=8)
    assert [(item.capability, item.operation) for item in descriptors] == [
        ("decision_workbench_review_queue", "acknowledge")
    ]
    assert descriptors[0].confirmation_required is True
    assert descriptors[0].auto_allowed is False


@pytest.mark.asyncio
async def test_review_queue_prepare_creates_pending_action_without_mutating_item() -> None:
    review_service, clock = _service()
    item_id = _create_item(review_service)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    action_gateway = CompactAgentActionOperationGateway(
        CompactCapabilityRegistry(),
        review_item_service=review_service,
    )
    pending_service = AgentPendingActionService(
        repository=SqlAlchemyAgentPendingActionRepository(engine),
        operation_gateway=action_gateway,
        clock=clock,
        id_generator=Uuid7IdGenerator(),
    )

    proposal = pending_service.propose(
        conversation_id="agent_conversation_review_queue",
        channel=AgentChannel.CONSOLE,
        principal="local-console",
        capability="decision_workbench_review_queue",
        operation="acknowledge",
        arguments={
            "review_item_id": item_id,
            "expected_version": 1,
            "idempotency_key": "agent-review-pending-1",
            "authorization_note": "User requested acknowledgement in Console.",
            "actor": "user",
        },
        presented_summary="Acknowledge the exact Review Queue item.",
    )

    assert proposal.action.status is AgentPendingActionStatus.PRESENTED
    assert proposal.action.capability == "decision_workbench_review_queue"
    assert proposal.action.operation == "acknowledge"
    read_gateway = AgentCapabilityGateway(
        CompactCapabilityRegistry(),
        review_item_service=review_service,
        clock=clock,
    )
    current = await read_gateway.read(
        "decision_workbench_review_queue",
        "open_items",
        {},
    )
    assert current.result["data"]["items"][0]["status"] == "OPEN"
