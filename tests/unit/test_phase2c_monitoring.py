"""Focused Monitoring persistence, state-transition, and MCP coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from application.dto.monitoring import (
    MonitorCreateInput,
    MonitorDashboardInput,
    MonitorEvaluateInput,
    MonitorEventResolveInput,
    MonitorListInput,
    MonitorRuleInput,
    MonitorUpdateInput,
)
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope, WarningInfo
from application.dto.us_market import USQuoteDTO
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_service import MonitorService
from domain.common.enums import Freshness, TradingSession
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from interfaces.mcp.server import PUBLIC_TOOL_NAMES, create_mcp_server

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


def _quote(
    last: str, warning_codes: tuple[str, ...] = ()
) -> ToolEnvelope[USQuoteDTO]:
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
    warnings = tuple(
        WarningInfo(code=code, message="Fact quality warning.", details={})
        for code in warning_codes
    )
    return ToolEnvelope.success(
        request_id="req_quote",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(),
        data=data,
        degraded=bool(warnings),
        warnings=warnings,
    )


@pytest.mark.asyncio
async def test_price_monitor_emits_only_state_transitions(
    tmp_path, fixed_clock, id_generator
) -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        description="Price fell below the configured floor.",
        rule_type=MonitorRuleType.PRICE_BELOW,
        severity=MonitorSeverity.HIGH,
        instrument_id="equity:US:NVDA",
        price_threshold=Decimal("12"),
        max_fact_age_seconds=3600,
    )
    repository = _repository(tmp_path)
    repository.create(_monitor(rule))
    fixed_clock.set(NOW)
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(return_value=_quote("10"))
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )

    first = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))
    second = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))
    market.get_market_snapshot.return_value = _quote("13")
    third = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))

    assert first.events_created == 1
    assert second.events_created == 0
    assert third.events_created == 1
    events = repository.list_events(None, 10)
    assert {item.event_type for item in events} == {
        MonitorEventType.TRIGGERED,
        MonitorEventType.RECOVERED,
    }
    final_state = repository.get_rule_states("monitor_00000000-0000-7000-8000-000000000001")[0]
    assert final_state.state is MonitorRuleStateValue.QUIET


@pytest.mark.asyncio
async def test_warning_only_monitor_run_succeeds_and_preserves_warnings(
    tmp_path, fixed_clock, id_generator
) -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        description="Price fell below the configured floor.",
        rule_type=MonitorRuleType.PRICE_BELOW,
        instrument_id="equity:US:NVDA",
        price_threshold=Decimal("12"),
        max_fact_age_seconds=3600,
    )
    repository = _repository(tmp_path)
    repository.create(_monitor(rule))
    fixed_clock.set(NOW)
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(
        return_value=_quote("10", ("DELAYED_US_DATA", "EXTENDED_HOURS_PRICE"))
    )
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )

    result = await evaluator.evaluate(MonitorEvaluateInput(as_of=NOW))

    assert result.status is MonitorRunStatus.SUCCEEDED
    assert result.rules_evaluated == 1
    assert result.warning_codes == ("DELAYED_US_DATA", "EXTENDED_HOURS_PRICE")
    assert result.error_codes == ()


def test_monitor_repository_keeps_append_only_versions(tmp_path) -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        description="Price fell below the configured floor.",
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
        created_at=NOW + timedelta(days=1),
    )
    repository.append_version(second)

    assert repository.get_current(first.monitor_id) == second
    assert repository.list_current(MonitorStatus.ACTIVE) == ()
    assert repository.get_by_idempotency_key("monitor-create-1") == first
    assert repository.get_created_at(first.monitor_id) == NOW


def test_monitor_dashboard_distinguishes_created_and_updated_times(
    tmp_path, fixed_clock, id_generator
) -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        description="Price fell below the configured floor.",
        rule_type="PRICE_BELOW",
        instrument_id="equity:US:NVDA",
        price_threshold=Decimal("100"),
    )
    repository = _repository(tmp_path)
    first = _monitor(rule)
    repository.create(first)
    updated_at = NOW + timedelta(days=1)
    repository.append_version(
        replace(
            first,
            version=2,
            name="NVDA downside edited",
            idempotency_key="monitor-update-2",
            created_at=updated_at,
        )
    )
    fixed_clock.set(updated_at)
    service = MonitorService(repository, MagicMock(), fixed_clock, id_generator)

    item = service.dashboard(MonitorDashboardInput()).items[0]

    assert item.monitor_created_at == NOW
    assert item.monitor_updated_at == updated_at


def test_monitor_rule_description_is_required_for_new_inputs() -> None:
    with pytest.raises(ValidationError, match="description"):
        MonitorRuleInput(
            rule_code="price_floor",
            rule_type="PRICE_BELOW",
            instrument_id="equity:US:NVDA",
            price_threshold=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_expired_monitor_is_skipped_without_fetch_or_event(
    tmp_path, fixed_clock, id_generator
) -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        description="Price fell below the configured floor.",
        rule_type="PRICE_BELOW",
        instrument_id="equity:US:NVDA",
        price_threshold=Decimal("100"),
    )
    repository = _repository(tmp_path)
    repository.create(
        replace(
            _monitor(rule),
            created_at=NOW - timedelta(hours=2),
            valid_until=NOW - timedelta(hours=1),
        )
    )
    fixed_clock.set(NOW)
    market = MagicMock()
    market.get_market_snapshot = AsyncMock()
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )

    result = await evaluator.evaluate(MonitorEvaluateInput(cadence="US_POST_MARKET", as_of=NOW))

    assert result.monitors_evaluated == 0
    assert result.rules_evaluated == 0
    assert result.events_created == 0
    assert result.warning_codes == ("MONITOR_EXPIRED",)
    assert repository.list_events(None, 10) == ()
    market.get_market_snapshot.assert_not_awaited()


def test_monitor_inputs_normalize_conversational_enum_casing() -> None:
    rule = MonitorRuleInput(
        rule_code="price_floor",
        description="Price fell below the configured floor.",
        rule_type=" price_below ",
        severity="high",
        instrument_id="equity:US:NVDA",
        price_threshold=Decimal("100"),
    )
    created = MonitorCreateInput(
        name="NVDA floor",
        cadence="us_post_market",
        rules=(rule,),
        valid_until=NOW + timedelta(days=7),
        confirmed_by="user",
        idempotency_key="monitor-create-lowercase",
    )
    updated = MonitorUpdateInput(
        monitor_id="monitor_example",
        expected_version=1,
        name="NVDA floor",
        cadence="on_demand",
        status="paused",
        rules=(rule,),
        confirmed_by="user",
        idempotency_key="monitor-update-lowercase",
    )
    resolved = MonitorEventResolveInput(
        event_id="monitor_event_example",
        action="acknowledge",
        note="reviewed",
        confirmed_by="user",
        idempotency_key="monitor-resolve-lowercase",
    )

    assert MonitorListInput(status=" active ").status is MonitorStatus.ACTIVE
    assert created.cadence is MonitorCadence.US_POST_MARKET
    assert created.valid_until == NOW + timedelta(days=7)
    assert rule.rule_type is MonitorRuleType.PRICE_BELOW
    assert rule.severity is MonitorSeverity.HIGH
    assert updated.cadence is MonitorCadence.ON_DEMAND
    assert updated.status is MonitorStatus.PAUSED
    assert resolved.action.value == "ACKNOWLEDGE"


@pytest.mark.asyncio
async def test_compact_monitoring_handlers_are_registered_and_delegate() -> None:
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
    for name in (
        "create",
        "get",
        "list",
        "dashboard",
        "list_runs",
        "update",
        "list_events",
        "resolve_event",
    ):
        getattr(coordinator, name).return_value = failure
    coordinator.evaluate = AsyncMock(return_value=failure)
    container.services.monitoring = coordinator
    manager = create_mcp_server(container)._tool_manager

    tools = manager.list_tools()
    assert {tool.name for tool in tools} == set(PUBLIC_TOOL_NAMES)
    rule = {
        "rule_code": "price_floor",
        "description": "Price fell below the configured floor.",
        "rule_type": "PRICE_BELOW",
        "severity": "HIGH",
        "instrument_id": "equity:US:NVDA",
        "price_threshold": "100",
        "max_fact_age_seconds": 3600,
    }
    await manager.call_tool(
        "monitor_manage",
        {
            "request": {
                "operation": "create",
                "name": "NVDA floor",
                "rules": [rule],
                "confirmed_by": "user",
                "idempotency_key": "monitor-create",
            }
        },
    )
    await manager.call_tool(
        "monitor_read",
        {"request": {"operation": "definitions", "monitor_id": "monitor_example"}},
    )
    await manager.call_tool("monitor_read", {"request": {"operation": "definitions"}})
    await manager.call_tool(
        "monitor_read",
        {"request": {"operation": "definitions", "status": "active"}},
    )
    await manager.call_tool(
        "monitor_manage",
        {
            "request": {
                "operation": "update",
                "monitor_id": "monitor_example",
                "expected_version": 1,
                "name": "NVDA floor",
                "cadence": "ON_DEMAND",
                "status": "PAUSED",
                "rules": [rule],
                "confirmed_by": "user",
                "idempotency_key": "monitor-update",
            }
        },
    )
    await manager.call_tool("monitor_read", {"request": {"operation": "events"}})
    await manager.call_tool("monitor_read", {"request": {"operation": "dashboard"}})
    await manager.call_tool(
        "monitor_read",
        {"request": {"operation": "runs", "monitor_id": "monitor_example"}},
    )
    await manager.call_tool(
        "monitor_manage",
        {
            "request": {
                "operation": "resolve_event",
                "event_id": "monitor_event_example",
                "action": "ACKNOWLEDGE",
                "note": "reviewed",
                "confirmed_by": "user",
                "idempotency_key": "event-resolution",
            }
        },
    )
    await manager.call_tool("monitor_evaluate", {})

    coordinator.create.assert_called_once()
    assert coordinator.list.call_args.args[0].status is MonitorStatus.ACTIVE
    coordinator.update.assert_called_once()
    coordinator.dashboard.assert_called_once()
    coordinator.list_runs.assert_called_once()
    coordinator.resolve_event.assert_called_once()
    coordinator.evaluate.assert_awaited_once()
