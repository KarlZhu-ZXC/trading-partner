from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from application.dto.monitoring import MonitorRuleInput
from application.ports.agent_model_provider import ModelResponse, ModelToolCall
from application.services.monitor_event_analysis_service import MonitorEventAnalysisService
from application.services.monitor_notification_rendering import _append_model_analysis
from domain.common.errors import ProviderTimeoutError
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition, MonitorEvent, MonitorRunObservation
from domain.notifications.enums import NotificationChannel, NotificationSourceType
from domain.notifications.models import NotificationMessage

NOW = datetime(2026, 8, 25, 1, tzinfo=UTC)


def _facts() -> tuple[MonitorDefinition, MonitorEvent, MonitorRunObservation]:
    rule = MonitorRuleInput(
        rule_code="PRICE_BREAKOUT",
        description="价格突破 100 后进入复核；触价本身不授权交易。",
        rule_type=MonitorRuleType.PRICE_ABOVE,
        severity=MonitorSeverity.HIGH,
        instrument_id="equity:US:TEST",
        price_threshold=Decimal("100"),
        max_fact_age_seconds=3600,
    ).to_domain()
    monitor = MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000201",
        version=1,
        name="TEST 突破监控",
        subject_id=None,
        primary_instrument_id="equity:US:TEST",
        cadence=MonitorCadence.INTERVAL,
        interval_minutes=60,
        status=MonitorStatus.ACTIVE,
        rules=(rule,),
        confirmed_by="user",
        idempotency_key="test-monitor-event-analysis",
        created_at=NOW,
    )
    event = MonitorEvent(
        event_id="monitor_event_00000000-0000-7000-8000-000000000201",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        event_type=MonitorEventType.TRIGGERED,
        severity=rule.severity,
        observed_value=Decimal("101"),
        threshold_value=Decimal("100"),
        fact_as_of=NOW,
        message="Rule condition triggered.",
        created_at=NOW,
    )
    observation = MonitorRunObservation(
        run_id="monitor_run_00000000-0000-7000-8000-000000000201",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        instrument_id=rule.instrument_id,
        severity=rule.severity,
        state=MonitorRuleStateValue.TRIGGERED,
        observed_value=Decimal("101"),
        threshold_value=Decimal("100"),
        distance_value=Decimal("1"),
        distance_percent=Decimal("1"),
        fact_as_of=NOW,
        fact_age_seconds=0,
        warning_codes=(),
        error_codes=(),
        message="Rule condition triggered.",
    )
    return monitor, event, observation


@pytest.mark.asyncio
async def test_event_analysis_uses_schema_tool_and_hard_character_limit() -> None:
    captured = None

    class Provider:
        model = "deepseek-v4-flash-vision-exp"
        reasoning_mode = "thinking"

        async def complete(self, request):  # type: ignore[no-untyped-def]
            nonlocal captured
            captured = request
            return ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="call_analysis",
                        name="submit_monitor_event_analysis",
                        arguments=json.dumps(
                            {"analysis": "突破条件已经触发，应关注后续能否保持。" * 8},
                            ensure_ascii=False,
                        ),
                    ),
                ),
                model=self.model,
            )

        async def aclose(self) -> None:
            return None

    monitor, event, observation = _facts()
    service = MonitorEventAnalysisService(Provider(), max_chars=80)  # type: ignore[arg-type]
    assert service._timeout_seconds == 80.0  # noqa: SLF001

    result = await service.analyze(monitor, (event,), (observation,))

    assert len(result.analysis) == 80
    assert result.analysis.endswith("…")
    assert result.warning_codes == ()
    assert captured is not None
    assert captured.reasoning_effort == "max"
    assert captured.max_output_tokens == 384
    assert captured.tools[0]["function"]["strict"] is True


@pytest.mark.asyncio
async def test_event_analysis_failure_is_bounded_and_fail_open() -> None:
    class Provider:
        model = "test-model"
        reasoning_mode = "none"

        async def complete(self, _request):  # type: ignore[no-untyped-def]
            raise ProviderTimeoutError("timed out")

        async def aclose(self) -> None:
            return None

    monitor, event, observation = _facts()
    service = MonitorEventAnalysisService(Provider())  # type: ignore[arg-type]

    result = await service.analyze(monitor, (event,), (observation,))

    assert result.analysis == "模型分析暂不可用；确定性规则结果仍然有效。"
    assert result.warning_codes == ("MONITOR_EVENT_ANALYSIS_UNAVAILABLE",)


def test_model_analysis_is_last_and_capped_in_notification() -> None:
    base = NotificationMessage(
        notification_id="monitor_notification_test",
        source_type=NotificationSourceType.MONITOR_EVENT,
        source_id="monitor_event_test",
        channel=NotificationChannel.TELEGRAM,
        title="🚨 TEST · 新触发",
        body="确定性规则内容",
        created_at=NOW,
    )

    rendered = _append_model_analysis(base, "分析" * 200)

    assert rendered.body.startswith("确定性规则内容")
    assert rendered.body.splitlines()[-2] == "MODEL_ANALYSIS"
    assert len(rendered.body.splitlines()[-1]) == 160
    assert rendered.body.endswith("…")
    assert len(rendered.body) <= 4096
