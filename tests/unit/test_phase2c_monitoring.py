"""Focused Monitoring persistence, state-transition, and MCP coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine

from application.dto.monitoring import MonitorEvaluateInput, MonitorRuleInput
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.dto.us_market import USQuoteDTO
from application.services.monitor_evaluation_service import MonitorEvaluationService
from domain.common.enums import Freshness, TradingSession
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from interfaces.mcp.server import (
    PHASE2C_MONITORING_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _monitor(rule: MonitorRuleInput) -> MonitorDefinition:
    return MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000001",
        version=1,
        name="NVDA downside",
        case_id=None,
        primary_instrument_id="equity:US:NVDA",
        cadence=MonitorCadence.US_POST_MARKET,
        status=MonitorStatus.ACTIVE,
        rules=(rule.to_domain(),),
        confirmed_by="user",
        idempotency_key="monitor-create-1",
        created_at=NOW,
    )


def _repository(tmp_path) -> SqlAlchemyMonitorRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'monitor.db'}")
    Base.metadata.create_all(engine)
    return SqlAlchemyMonitorRepository(engine)


def _quote(last: str) -> ToolEnvelope[USQuoteDTO]:
    data = USQuoteDTO(
        instrument_id="equity:US:NVDA",
        quote_at=NOW,
        session=TradingSession.REGULAR,
        last=Decimal(last),
        open=None,
        high=None,
        low=None,
        previous_close=None,
        volume=None,
        average_volume=None,
        market_cap=None,
        beta=None,
        week_52_low=None,
        week_52_high=None,
    )
    return ToolEnvelope.success(
        request_id="req_quote",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=data,
    )


@pytest.mark.asyncio
async def test_price_monitor_emits_only_state_transitions(
    tmp_path, fixed_clock, id_generator
) -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        rule_type=MonitorRuleType.PRICE_BELOW,
        severity=MonitorSeverity.HIGH,
        instrument_id="equity:US:NVDA",
        price_threshold=Decimal("12"),
        max_fact_age_seconds=3600,
    )
    repository = _repository(tmp_path)
    repository.create(_monitor(rule))
    fixed_clock.set(NOW)
    us = MagicMock()
    us.get_market_snapshot = AsyncMock(return_value=_quote("10"))
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        us,
        MagicMock(),
        fixed_clock,
        id_generator,
    )

    first = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))
    second = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))
    us.get_market_snapshot.return_value = _quote("13")
    third = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))

    assert first.events_created == 1
    assert second.events_created == 0
    assert third.events_created == 1
    events = repository.list_events(None, 10)
    assert {item.event_type for item in events} == {
        MonitorEventType.TRIGGERED,
        MonitorEventType.RECOVERED,
    }
    final_state = repository.get_rule_states(
        "monitor_00000000-0000-7000-8000-000000000001"
    )[0]
    assert final_state.state is MonitorRuleStateValue.QUIET


def test_monitor_repository_keeps_append_only_versions(tmp_path) -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        rule_type="PRICE_BELOW",
        instrument_id="equity:US:NVDA",
        price_threshold=Decimal("100"),
    )
    repository = _repository(tmp_path)
    first = _monitor(rule)
    repository.create(first)
    second = MonitorDefinition(
        monitor_id=first.monitor_id,
        version=2,
        name="Paused",
        case_id=None,
        primary_instrument_id=first.primary_instrument_id,
        cadence=first.cadence,
        status=MonitorStatus.PAUSED,
        rules=first.rules,
        confirmed_by="user",
        idempotency_key="monitor-update-2",
        created_at=NOW,
    )
    repository.append_version(second)

    assert repository.get_current(first.monitor_id) == second
    assert repository.list_current(MonitorStatus.ACTIVE) == ()
    assert repository.get_by_idempotency_key("monitor-create-1") == first


@pytest.mark.asyncio
async def test_six_monitoring_mcp_handlers_are_registered() -> None:
    failure = ToolEnvelope.failure(
        request_id="req_monitor",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.UNKNOWN,
        errors=(ErrorInfo(code="STUB", message="stub", retryable=False, details={}),),
    )
    container = MagicMock()
    container.settings.mcp_server_name = "monitor-test"
    coordinator = MagicMock()
    for name in ("create", "get", "list", "update", "list_events", "resolve_event"):
        getattr(coordinator, name).return_value = failure
    coordinator.evaluate = AsyncMock(return_value=failure)
    container.monitor_tool_coordinator = coordinator
    manager = create_mcp_server(container)._tool_manager

    assert {tool.name for tool in manager.list_tools()} == set(PUBLIC_TOOL_NAMES)
    assert len(PUBLIC_TOOL_NAMES) == 52
    assert len(PHASE2C_MONITORING_TOOL_NAMES) == 6
    rule = {
        "rule_code": "price_floor",
        "rule_type": "PRICE_BELOW",
        "severity": "HIGH",
        "instrument_id": "equity:US:NVDA",
        "price_threshold": "100",
        "max_fact_age_seconds": 3600,
    }
    await manager.call_tool(
        "monitor_create",
        {
            "name": "NVDA floor",
            "rules": [rule],
            "confirmed_by": "user",
            "idempotency_key": "monitor-create",
        },
    )
    await manager.call_tool("monitor_query", {"monitor_id": "monitor_example"})
    await manager.call_tool("monitor_query", {})
    await manager.call_tool(
        "monitor_update",
        {
            "monitor_id": "monitor_example",
            "expected_version": 1,
            "name": "NVDA floor",
            "cadence": "ON_DEMAND",
            "status": "PAUSED",
            "rules": [rule],
            "confirmed_by": "user",
            "idempotency_key": "monitor-update",
        },
    )
    await manager.call_tool("monitor_event_list", {})
    await manager.call_tool(
        "monitor_event_resolve",
        {
            "event_id": "monitor_event_example",
            "action": "ACKNOWLEDGE",
            "note": "reviewed",
            "confirmed_by": "user",
            "idempotency_key": "event-resolution",
        },
    )
    await manager.call_tool("monitor_evaluate", {})

    coordinator.create.assert_called_once()
    coordinator.update.assert_called_once()
    coordinator.resolve_event.assert_called_once()
    coordinator.evaluate.assert_awaited_once()
