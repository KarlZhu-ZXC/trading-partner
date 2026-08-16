"""Telegram adapter and durable Monitor notification outbox coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine

from application.dto.monitor_notifications import NotificationSendReceipt
from application.dto.monitoring import MonitorEvaluateInput, MonitorRuleInput
from application.dto.tool_envelope import ErrorInfo, SourceReference, ToolEnvelope
from application.dto.us_market import USQuoteDTO
from application.services.monitor_evaluation_service import (
    MonitorEvaluationService,
    _append_judgment_notification,
    _data_recovery_message,
    _notification_event_label,
    _notification_event_symbol,
    _notification_messages,
    _notification_price_change_lines,
    _NotificationPriceContext,
    _post_market_summary_message,
    _rule_condition,
    _rule_meaning,
)
from application.services.monitor_judgment_service import MonitorJudgmentService
from application.services.monitor_notification_service import MonitorNotificationService
from domain.common.diagnostics import ProviderFailureDiagnostic
from domain.common.enums import Freshness, Market, SourceRole, TradingSession
from domain.common.errors import ProviderTimeoutError
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorNotificationChannel,
    MonitorNotificationStatus,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorJudgmentPolicy,
    MonitorNotificationOutboxEntry,
    MonitorRun,
    MonitorRunObservation,
)
from domain.notifications.enums import NotificationChannel, NotificationSourceType
from domain.notifications.models import NotificationMessage
from domain.trade_plan.enums import TradePlanComparator, TradePlanFactType
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from infrastructure.providers.notifications.telegram import (
    TelegramMonitorNotificationAdapter,
    _format_notification_html,
)

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def test_fact_comparison_condition_uses_readable_operator_and_hides_last_metric() -> None:
    rule = MonitorRuleInput(
        rule_code="XAU_TOP_REVIEW_4430",
        description="XAUUSD 升至 4430 或以上时进入顶部复核。",
        rule_type=MonitorRuleType.FACT_COMPARISON,
        severity=MonitorSeverity.HIGH,
        instrument_id="commodity_spot:OTC:XAUUSD",
        max_fact_age_seconds=18000,
        fact_type=TradePlanFactType.PRICE,
        metric_key="last",
        comparator=TradePlanComparator.GTE,
        numeric_threshold=Decimal("4430"),
    ).to_domain()

    assert _rule_condition(rule) == "≥ 4430"


def test_technical_condition_uses_readable_timeframe_and_indicator_name() -> None:
    rule = MonitorRuleInput(
        rule_code="GDXU_RSI_OVERHEAT_80",
        description="GDXU 日线 RSI14 达到 80 或以上时复核过热风险。",
        rule_type=MonitorRuleType.FACT_COMPARISON,
        severity=MonitorSeverity.MEDIUM,
        instrument_id="etf:US:GDXU",
        max_fact_age_seconds=172800,
        fact_type=TradePlanFactType.TECHNICAL,
        metric_key="rsi_14",
        comparator=TradePlanComparator.GTE,
        numeric_threshold=Decimal("80"),
        technical_interval="1d",
    ).to_domain()

    assert _rule_condition(rule) == "日线 RSI14 ≥ 80"


def _outbox_entry() -> MonitorNotificationOutboxEntry:
    return MonitorNotificationOutboxEntry(
        notification_id="notification_00000000-0000-7000-8000-000000000001",
        source_type=NotificationSourceType.MONITOR_EVENT,
        source_id="monitor_event_00000000-0000-7000-8000-000000000001",
        channel=MonitorNotificationChannel.TELEGRAM,
        title="🚨 GC=F · TRIGGERED",
        body="规则：GC_PULLBACK_ALERT_4080 < 4080",
        status=MonitorNotificationStatus.PENDING,
        attempt_count=0,
        next_attempt_at=NOW,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_telegram_adapter_sends_json_and_returns_message_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.telegram.org"
        assert request.url.path.endswith("/sendRichMessage")
        assert b'"chat_id":"-100123"' in request.content
        assert b'"rich_message":{"html":' in request.content
        assert b'"skip_entity_detection":true' in request.content
        assert b"&lt;" in request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TelegramMonitorNotificationAdapter(
        bot_token="123456:TOP_SECRET_TOKEN",
        chat_id="-100123",
        client=client,
    )

    receipt = await adapter.send(_outbox_entry())

    assert receipt.delivered is True
    assert receipt.provider_message_id == "77"
    assert "TOP_SECRET_TOKEN" not in repr(receipt)
    await client.aclose()


@pytest.mark.asyncio
async def test_telegram_adapter_maps_rate_limit_without_exposing_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "sensitive provider detail",
                "parameters": {"retry_after": 90},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TelegramMonitorNotificationAdapter(
        bot_token="123456:TOP_SECRET_TOKEN",
        chat_id="123",
        client=client,
    )

    receipt = await adapter.send(_outbox_entry())

    assert receipt.error_code == "TELEGRAM_RATE_LIMITED"
    assert receipt.retryable is True
    assert receipt.retry_after_seconds == 90
    assert "sensitive provider detail" not in repr(receipt)
    await client.aclose()


def test_monitor_notification_formats_price_first_mobile_rule_cards() -> None:
    body = """Gold monitor
当前价格：4078.3
价格时间：2026-07-29T08:51:19-04:00
上次价格：4081.2
价格变化：-2.9 (-0.07%)
数据来源：ig_weekend_gold
CHANGES
• [MEDIUM] GC_PULLBACK_ALERT_4080 → TRIGGERED
RULES
RULE                      COND    VALUE   DIST   STATE      LEVEL
------------------------  ------  ------  -----  ---------  ------
GC_PULLBACK_ALERT_4080    < 4080  4078.3  -1.7   TRIGGERED  MEDIUM
GC_STRUCTURE_FAIL_3940    < 3940  4078.3  138.3  QUIET      HIGH
数据提示：DELAYED_US_DATA, FUTURES_CONTRACT_NOT_SPOT
周末口径：IG Weekend Gold CFD 仅作为 XAUUSD 周末波动代理；不是现货黄金或 LBMA 基准价。
"""

    rendered = _format_notification_html(
        title="🚨 GC=F · TRIGGERED",
        body=body,
    )

    assert rendered.startswith("<b>🚨 GC=F · 4078.3 · TRIGGERED</b>")
    assert "💰 <b>当前价格：4078.3</b>" not in rendered
    assert (
        "🕒 2026-07-29 08:51 UTC-04:00 · 📡 <b>IG Weekend Gold（Apify）</b>" in rendered
    )
    assert "↩️ 上次 4081.2 · 较上次 <b>-2.9 (-0.07%)</b>" in rendered
    assert "🟥 <b>新告警触发</b>" in rendered
    assert "状态较上次发生变化" in rendered
    assert "IG Weekend Gold（Apify）" in rendered
    assert "🔴 <code>GC_PULLBACK_ALERT_4080</code>" in rendered
    assert "<b>规则概览</b>" in rendered
    assert "<table bordered striped>" in rendered
    assert "<th>状态</th><th>规则与含义</th>" in rendered
    assert "🔴 <b>触发</b> · 中" in rendered
    assert "<details><summary>⚪ 未触发 · 1 项</summary>" in rendered
    assert "⚪️ <b>未触发</b> · 高" in rendered
    assert "GC_STRUCTURE_FAIL_3940" not in rendered
    assert "<pre>" not in rendered
    assert "数据提示：DELAYED_US_DATA" in rendered
    assert "周末口径：IG Weekend Gold CFD 仅作为 XAUUSD 周末波动代理" in rendered
    assert rendered.index("新告警触发") < rendered.index("规则概览")


def test_cross_instrument_title_and_same_run_judgment_are_compact() -> None:
    rules = {
        "GDXU_EXIT": MagicMock(instrument_id="etf:US:GDXU"),
        "GDXU_RSI": MagicMock(instrument_id="etf:US:GDXU"),
    }
    events = (
        MagicMock(rule_code="GDXU_EXIT"),
        MagicMock(rule_code="GDXU_RSI"),
    )
    assert _notification_event_symbol(events, rules, "XAUUSD") == "GDXU"

    base = NotificationMessage(
        notification_id="monitor_notification_base",
        source_type=NotificationSourceType.MONITOR_EVENT,
        source_id="monitor_event_base",
        channel=NotificationChannel.TELEGRAM,
        title="🚨 GDXU · 2项变化",
        body=(
            "黄金监控\n标的：XAUUSD\n当前价格：4354.8\nCHANGES\n两项确定性变化\n"
            "RULES\n• 状态：TRIGGERED · 条件：last GTE 132 · 含义：进入阻力区 · 级别：H"
        ),
        created_at=NOW,
    )
    judgment = NotificationMessage(
        notification_id="monitor_notification_judgment",
        source_type=NotificationSourceType.MONITOR_EVENT,
        source_id="monitor_event_judgment",
        channel=NotificationChannel.TELEGRAM,
        title="复合判断",
        body="结论：WAIT\n状态：盘前报价可用，日线仍截止上一收盘",
        created_at=NOW,
    )

    merged = _append_judgment_notification(base, judgment)

    assert merged.source_id == "monitor_event_base"
    assert "两项确定性变化" in merged.body
    assert "盘前报价可用" in merged.body
    assert len(merged.body) <= 4096
    rendered = _format_notification_html(merged.title, merged.body)
    assert "🧭 <b>复合判断</b>" in rendered
    assert "盘前报价可用" in rendered


def test_model_degradation_merges_into_deterministic_card_with_same_run_context() -> None:
    rule = MonitorRuleInput(
        rule_code="XAU_BREAKOUT_2400",
        description="黄金突破 2400 后提醒。",
        rule_type=MonitorRuleType.PRICE_ABOVE,
        severity=MonitorSeverity.HIGH,
        instrument_id="commodity_spot:OTC:XAUUSD",
        price_threshold=Decimal("2400"),
        max_fact_age_seconds=3600,
    ).to_domain()
    monitor = MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000101",
        version=1,
        name="XAUUSD 关键位",
        subject_id=None,
        primary_instrument_id="commodity_spot:OTC:XAUUSD",
        cadence=MonitorCadence.INTERVAL,
        interval_minutes=60,
        status=MonitorStatus.ACTIVE,
        rules=(rule,),
        confirmed_by="user",
        idempotency_key="xau-model-degradation-card",
        created_at=NOW,
    )
    observation = MonitorRunObservation(
        run_id="monitor_run_00000000-0000-7000-8000-000000000101",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        instrument_id=rule.instrument_id,
        severity=rule.severity,
        state=MonitorRuleStateValue.TRIGGERED,
        observed_value=Decimal("2405"),
        threshold_value=Decimal("2400"),
        distance_value=Decimal("5"),
        distance_percent=Decimal("0.2083333333"),
        fact_as_of=NOW,
        fact_age_seconds=0,
        warning_codes=(),
        error_codes=(),
        message="Rule condition triggered.",
    )
    event = MonitorEvent(
        event_id="monitor_event_00000000-0000-7000-8000-000000000101",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        event_type=MonitorEventType.TRIGGERED,
        severity=rule.severity,
        observed_value=Decimal("2405"),
        threshold_value=Decimal("2400"),
        fact_as_of=NOW,
        message="Rule condition triggered.",
        created_at=NOW,
    )
    base = _notification_messages(
        monitor,
        (event,),
        (observation,),
        {},
        ("dukascopy",),
        MagicMock(new=MagicMock(return_value="monitor_notification_base")),
    )[0]
    degradation = NotificationMessage(
        notification_id="monitor_notification_judgment",
        source_type=NotificationSourceType.MONITOR_EVENT,
        source_id="monitor_event_00000000-0000-7000-8000-000000000102",
        channel=NotificationChannel.TELEGRAM,
        title="🧭 XAUUSD · 判断不可用",
        body=(
            "状态：复合判断暂时不可用；确定性规则结果仍然有效。\n"
            "错误码：PROVIDER_TIMEOUT_ERROR\n"
            "说明：本轮未生成模型结论；请稍后重试。"
        ),
        created_at=NOW,
    )

    merged = _append_judgment_notification(base, degradation)
    assert merged.body.count("JUDGMENT") == 1
    assert "当前价格：2405" in merged.body
    assert "价格时间：2026-07-29T12:00:00+00:00" in merged.body
    assert "数据来源：dukascopy" in merged.body
    assert "条件：> 2400" in merged.body
    assert "错误码：PROVIDER_TIMEOUT_ERROR" in merged.body
    assert "未定义" not in merged.body
    assert "UNKNOWN" not in merged.body
    assert "建议数量0" not in merged.body


def test_data_interruption_is_one_compact_operational_card() -> None:
    rule = MonitorRuleInput(
        rule_code="XAU_PRICE",
        description="黄金价格规则。",
        rule_type=MonitorRuleType.PRICE_ABOVE,
        severity=MonitorSeverity.HIGH,
        instrument_id="commodity_spot:OTC:XAUUSD",
        price_threshold=Decimal("2400"),
        max_fact_age_seconds=3600,
    ).to_domain()
    monitor = MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000111",
        version=1,
        name="XAUUSD monitor",
        subject_id=None,
        primary_instrument_id="commodity_spot:OTC:XAUUSD",
        cadence=MonitorCadence.INTERVAL,
        interval_minutes=60,
        status=MonitorStatus.ACTIVE,
        rules=(rule,),
        confirmed_by="user",
        idempotency_key="xau-data-interruption",
        created_at=NOW,
    )
    diagnostic = ProviderFailureDiagnostic(
        provider="dukascopy",
        stage="primary_quote",
        error_code="PROVIDER_TIMEOUT_ERROR",
        retryable=True,
        attempt_count=1,
        error_type="timeout",
        status_class="none",
        status_code=None,
    )
    observation = MonitorRunObservation(
        run_id="monitor_run_00000000-0000-7000-8000-000000000111",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        instrument_id=rule.instrument_id,
        severity=rule.severity,
        state=MonitorRuleStateValue.NOT_EVALUATED,
        observed_value=None,
        threshold_value=Decimal("2400"),
        distance_value=None,
        distance_percent=None,
        fact_as_of=None,
        fact_age_seconds=None,
        warning_codes=(),
        error_codes=("PROVIDER_TIMEOUT_ERROR",),
        message="Required fact was unavailable.",
        diagnostics=(diagnostic,),
    )
    event = MonitorEvent(
        event_id="monitor_event_00000000-0000-7000-8000-000000000111",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        event_type=MonitorEventType.NOT_EVALUATED,
        severity=rule.severity,
        observed_value=None,
        threshold_value=Decimal("2400"),
        fact_as_of=None,
        message="Required fact was unavailable.",
        created_at=NOW,
    )

    message = _notification_messages(
        monitor,
        (event,),
        (observation,),
        {},
        (),
        MagicMock(new=MagicMock(return_value="monitor_notification_interruption")),
    )[0]

    assert message.title == "⛔ XAUUSD · 数据源中断"
    assert "影响：1 条规则暂停计算；未改变原有触发结论" in message.body
    assert "dukascopy / primary_quote / PROVIDER_TIMEOUT_ERROR" in message.body
    assert "CHANGES" not in message.body and "RULES" not in message.body


def test_data_recovery_is_blue_and_not_described_as_market_recovery() -> None:
    monitor = MagicMock(
        name="XAUUSD monitor",
        primary_instrument_id="commodity_spot:OTC:XAUUSD",
        rules=(),
    )
    observation = MagicMock(
        instrument_id="commodity_spot:OTC:XAUUSD",
        rule_code="XAU_PRICE",
        observed_value=Decimal("2410"),
        fact_as_of=NOW,
        state=MonitorRuleStateValue.QUIET,
    )

    message = _data_recovery_message(
        run_id="monitor_run_recovery",
        monitor=monitor,
        observations=(observation,),
        recovered=(observation,),
        data_sources=("dukascopy",),
        id_generator=MagicMock(new=MagicMock(return_value="monitor_notification_recovery")),
        created_at=NOW,
    )

    assert message.title == "🔵 XAUUSD · 数据恢复"
    assert "数据状态：已恢复" in message.body
    assert "不代表价格上涨或行情转好" in message.body
    assert "告警解除" not in message.body


@pytest.mark.asyncio
async def test_retryable_monitor_quote_read_recovers_before_declaring_outage(
    fixed_clock, id_generator
) -> None:
    failure = ToolEnvelope.failure(
        request_id="req_retryable_quote_failure",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        errors=(
            ErrorInfo(
                code="PROVIDER_TIMEOUT_ERROR",
                message="Provider timed out.",
                retryable=True,
                details={
                    "provider_diagnostics": [
                        {
                            "provider": "dukascopy",
                            "stage": "primary_quote",
                            "error_code": "PROVIDER_TIMEOUT_ERROR",
                            "retryable": True,
                            "attempt_count": 1,
                            "error_type": "timeout",
                            "status_class": "none",
                            "status_code": None,
                        }
                    ]
                },
            ),
        ),
    )
    success = ToolEnvelope.success(
        request_id="req_quote_retry_success",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(SourceReference(name="dukascopy", role=SourceRole.PRIMARY),),
        data=USQuoteDTO(
            instrument_id="commodity_spot:OTC:XAUUSD",
            quote_at=NOW,
            session=TradingSession.REGULAR,
            last=Decimal("2400"),
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
        ),
    )
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(side_effect=(failure, success))
    evaluator = MonitorEvaluationService(
        MagicMock(),
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
        provider_retry_attempts=3,
        provider_retry_delay_seconds=0,
    )

    fact = await evaluator._price_fact(  # noqa: SLF001
        "commodity_spot:OTC:XAUUSD", NOW
    )

    assert fact.value == Decimal("2400")
    assert fact.error_codes == ()
    assert "MONITOR_PROVIDER_READ_RETRIED" in fact.warning_codes
    assert market.get_market_snapshot.await_count == 2


@pytest.mark.asyncio
async def test_evaluator_model_timeout_enqueues_one_same_run_operational_card(
    migrated_sqlite_url, fixed_clock, id_generator
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyMonitorRepository(engine)
    instrument_id = "future:US:GC=F"
    monitor = MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000105",
        version=1,
        name="XAUUSD composite monitor",
        subject_id=None,
        primary_instrument_id=instrument_id,
        cadence=MonitorCadence.INTERVAL,
        interval_minutes=60,
        status=MonitorStatus.ACTIVE,
        rules=(
            MonitorRuleInput(
                rule_code="XAU_BREAKOUT_2400_TIMEOUT",
                description="黄金突破 2400 后提醒。",
                rule_type=MonitorRuleType.PRICE_ABOVE,
                severity=MonitorSeverity.HIGH,
                instrument_id=instrument_id,
                price_threshold=Decimal("2400"),
                max_fact_age_seconds=3600,
            ).to_domain(),
        ),
        confirmed_by="user",
        idempotency_key="xau-model-timeout-evaluator",
        created_at=NOW,
        judgment_policy=MonitorJudgmentPolicy(
            playbook="等待确定性确认",
            reference_instrument_ids=(instrument_id,),
            confirmed_state_json="{}",
        ),
    )
    repository.create(monitor)
    quote = ToolEnvelope.success(
        request_id="req_xau_timeout_quote",
        market=Market.US,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(SourceReference(name="yfinance", role=SourceRole.PRIMARY),),
        data=USQuoteDTO(
            instrument_id=instrument_id,
            quote_at=NOW,
            session=TradingSession.REGULAR,
            last=Decimal("2395"),
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
        ),
    )
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(return_value=quote)
    market.get_market_bars = AsyncMock()
    provider = MagicMock(
        provider_name="bailian",
        model="qwen3.8-max",
        reasoning_effort="high",
    )
    provider.judge = AsyncMock(side_effect=ProviderTimeoutError("timed out"))
    judgment_service = MonitorJudgmentService(
        repository,
        market,
        provider,
        fixed_clock,
        id_generator,
    )
    judgment_service._features = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=(
            {"warning_codes": (), "sessions_aligned": True},
            ("sessions_aligned",),
            "timeout-feature-signature",
        )
    )
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
        judgment_service=judgment_service,
    )
    fixed_clock.set(NOW)

    run = await evaluator.evaluate(
        MonitorEvaluateInput(monitor_ids=(monitor.monitor_id,), as_of=NOW)
    )

    pending = repository.list_due_notifications(
        MonitorNotificationChannel.TELEGRAM,
        NOW,
        20,
    )
    assert run.events_created == 1
    assert len(pending) == 1
    card = pending[0]
    assert "当前价格：2395" in card.body
    assert f"价格时间：{NOW.isoformat()}" in card.body
    assert "数据来源：yfinance" in card.body
    assert "条件：> 2400" in card.body
    assert "含义：黄金突破 2400 后提醒" in card.body
    assert "错误码：PROVIDER_TIMEOUT_ERROR" in card.body
    assert "未定义" not in card.body
    assert "UNKNOWN" not in card.body
    assert "建议数量0" not in card.body
    market.get_market_snapshot.assert_awaited_once()
    market.get_market_bars.assert_not_awaited()
    engine.dispose()


def test_post_market_model_degradation_stays_in_existing_digest() -> None:
    rule = MonitorRuleInput(
        rule_code="XAU_BREAKOUT_2400_DIGEST",
        description="黄金突破 2400 后提醒。",
        rule_type=MonitorRuleType.PRICE_ABOVE,
        severity=MonitorSeverity.HIGH,
        instrument_id="future:US:GC=F",
        price_threshold=Decimal("2400"),
        max_fact_age_seconds=3600,
    ).to_domain()
    monitor = MonitorDefinition(
        monitor_id="monitor_00000000-0000-7000-8000-000000000102",
        version=1,
        name="XAUUSD 盘后",
        subject_id=None,
        primary_instrument_id="future:US:GC=F",
        cadence=MonitorCadence.US_POST_MARKET,
        status=MonitorStatus.ACTIVE,
        rules=(rule,),
        confirmed_by="user",
        idempotency_key="xau-model-degradation-digest",
        created_at=NOW,
    )
    observation = MonitorRunObservation(
        run_id="monitor_run_00000000-0000-7000-8000-000000000102",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        instrument_id=rule.instrument_id,
        severity=rule.severity,
        state=MonitorRuleStateValue.TRIGGERED,
        observed_value=Decimal("2405"),
        threshold_value=Decimal("2400"),
        distance_value=Decimal("5"),
        distance_percent=Decimal("0.2083333333"),
        fact_as_of=NOW,
        fact_age_seconds=0,
        warning_codes=(),
        error_codes=(),
        message="Rule condition triggered.",
    )
    event = MonitorEvent(
        event_id="monitor_event_00000000-0000-7000-8000-000000000103",
        monitor_id=monitor.monitor_id,
        monitor_version=1,
        rule_code=rule.rule_code,
        event_type=MonitorEventType.TRIGGERED,
        severity=rule.severity,
        observed_value=Decimal("2405"),
        threshold_value=Decimal("2400"),
        fact_as_of=NOW,
        message="Rule condition triggered.",
        created_at=NOW,
    )
    run = MonitorRun(
        run_id=observation.run_id,
        requested_monitor_ids=(monitor.monitor_id,),
        selected_monitor_ids=(monitor.monitor_id,),
        cadence=MonitorCadence.US_POST_MARKET,
        as_of=NOW,
        started_at=NOW,
        completed_at=NOW,
        status=MonitorRunStatus.PARTIAL,
        monitors_evaluated=1,
        rules_evaluated=1,
        events_created=2,
        warning_codes=(),
        error_codes=("PROVIDER_TIMEOUT_ERROR",),
        observation_history_complete=True,
        observations=(observation,),
    )
    degradation = NotificationMessage(
        notification_id="monitor_notification_judgment_digest",
        source_type=NotificationSourceType.MONITOR_EVENT,
        source_id="monitor_event_00000000-0000-7000-8000-000000000104",
        channel=NotificationChannel.TELEGRAM,
        title="🧭 XAUUSD · 判断不可用",
        body=(
            "状态：复合判断暂时不可用；确定性规则结果仍然有效。\n"
            "错误码：PROVIDER_TIMEOUT_ERROR\n"
            "说明：本轮未生成模型结论；请稍后重试。"
        ),
        created_at=NOW,
    )
    message = _post_market_summary_message(
        run,
        (monitor,),
        MagicMock(new=MagicMock(return_value="monitor_notification_digest")),
        events=(event,),
        monitor_sources_by_monitor={monitor.monitor_id: ("dukascopy",)},
        judgment_notifications_by_monitor={monitor.monitor_id: degradation},
    )

    assert message.source_id == run.run_id
    assert message.body.count("JUDGMENT") == 1
    assert message.body.count("错误码：PROVIDER_TIMEOUT_ERROR") == 1
    rendered = _format_notification_html(message.title, message.body)
    assert "复合判断状态" in rendered
    assert "当前价格：2405" in message.body
    assert "数据来源：dukascopy" in message.body
    assert "错误码：PROVIDER_TIMEOUT_ERROR" in rendered
    assert "未定义" not in rendered
    assert "UNKNOWN" not in rendered
    assert "建议数量0" not in rendered


def test_post_market_digest_formats_zero_change_run_as_mobile_cards() -> None:
    body = """POST_MARKET_SUMMARY
运行时间：2026-07-29T20:20:15+00:00
本轮变化：0
MONITOR
TTWO Case 关键价位监控
标的：TTWO
当前价格：246.43
价格时间：2026-07-29T16:20:00-04:00
RULES
RULE                       COND     VALUE   DIST   STATE      LEVEL
-------------------------  -------  ------  -----  ---------  ------
TTWO_FIRST_CONFIRM_233_9   > 233.9  246.43  12.53  TRIGGERED  MEDIUM
TTWO_NO_CHASE_249_4        > 249.4  246.43  -2.97  QUIET      MEDIUM
END_MONITOR
数据提示：EXTENDED_HOURS_PRICE
"""

    rendered = _format_notification_html(
        title="📊 美股盘后 Monitor · 1 标的 · 0 变化",
        body=body,
    )

    assert "✅ 本轮无状态变化" in rendered
    assert "<b>TTWO · 246.43</b>" in rendered
    assert "TTWO_FIRST_CONFIRM_233_9" not in rendered
    assert "<table bordered striped>" in rendered
    assert "<th>状态</th><th>规则与含义</th>" in rendered
    assert "<details><summary>⚪ 未触发 · 1 项</summary>" in rendered
    assert "&gt; 249.4" in rendered
    assert "数据提示：EXTENDED_HOURS_PRICE" in rendered
    assert "<pre>" not in rendered


def test_compact_notification_cards_escape_text_and_share_data_cause() -> None:
    body = """Long <monitor>
标的：GC=F
当前价格：不可用
上一有效价格：4081.2
价格口径：上一有效价格（当前不可用）
价格时间：2026-07-29T08:51:19-04:00
CHANGES
• [HIGH] 状态变化 → NOT_EVALUATED
RULES
• 状态：NOT_EVALUATED · 条件：< 4080 · 含义：<script>alert(1)</script> · 级别：HIGH
• 状态：NOT_EVALUATED · 条件：> 4000 · 含义：另一个规则 · 级别：MEDIUM
数据原因：QUOTE_MISSING
"""

    rendered = _format_notification_html(
        title="⚠️ GC=F · NOT_EVALUATED",
        body=body,
    )

    assert rendered.startswith("<b>⚠️ GC=F · 4081.2 · NOT_EVALUATED</b>")
    assert "⚠️ 价格口径：上一有效价格（当前不可用）" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "QUOTE_MISSING" in rendered
    assert rendered.count("QUOTE_MISSING") == 1
    assert "💰 <b>当前价格：4081.2</b>" not in rendered
    assert "<b>数据不可用</b>" in rendered
    assert "GC_PULLBACK" not in rendered
    assert "<pre>" not in rendered


def test_rule_meaning_and_dukascopy_provenance_are_bounded() -> None:
    description = "黄金价格回落至关键支撑区域并触发风控提醒，后续观察确认且风险敞口仍然升高。"
    meaning = _rule_meaning(description)
    assert meaning == description.removesuffix("。")
    assert not meaning.endswith("…")
    assert len(_rule_meaning("无标点" * 100)) <= 160

    body = """Monitor
标的：XAUUSD
当前价格：2400
价格时间：2026-08-03T12:00:00+00:00
CHANGES
• [MEDIUM] 状态变化 → TRIGGERED
RULES
• 状态：TRIGGERED · 条件：< 2500 · 含义：观察黄金回落 · 级别：M
口径：Dukascopy OTC，非 LBMA
"""
    rendered = _format_notification_html("🚨 XAUUSD · TRIGGERED", body)
    assert "口径：Dukascopy OTC，非 LBMA" in rendered
    assert "DUKASCOPY_SWFX_NOT_LBMA" not in rendered


def test_detailed_transition_changes_render_each_point_and_escape_html() -> None:
    body = """XAUUSD monitor
标的：XAUUSD
当前价格：4081
价格时间：2026-08-03T12:00:00+00:00
CHANGES
• [HIGH] XAU_BREAKOUT_4080 · 条件：> 4080 · 含义：突破 <关键位> & 观察 → TRIGGERED
• [MEDIUM] XAU_PULLBACK_4080 · 条件：< 4080 · 含义：回落 <关键位> & 已恢复 → RECOVERED
RULES
• 状态：TRIGGERED · 条件：> 4080 · 含义：突破 <关键位> · 级别：HIGH
"""

    rendered = _format_notification_html("🚨 XAUUSD · 2项变化", body)

    assert "🟨 <b>监控状态变化 · 2 项</b>" in rendered
    assert "🔴 <b>&gt; 4080</b> · <b>已触发</b> · 高" in rendered
    assert "🟢 <b>&lt; 4080</b> · <b>告警解除</b> · 中" in rendered
    assert "含义：突破 &lt;关键位&gt; &amp; 观察" in rendered
    assert "含义：回落 &lt;关键位&gt; &amp; 已恢复" in rendered
    assert rendered.index("&gt; 4080") < rendered.index("&lt; 4080")
    assert rendered.count("状态变化") == 1


def test_recovered_transition_means_alarm_cleared_not_bullish_price_recovery() -> None:
    body = """XAUUSD monitor
标的：XAUUSD
当前价格：4079
CHANGES
• [MEDIUM] XAU_BREAKOUT_4080 · 条件：> 4080 · 含义：突破关键位 → RECOVERED
RULES
• 状态：QUIET · 条件：> 4080 · 含义：突破关键位 · 级别：MEDIUM
"""

    rendered = _format_notification_html("🟢 XAUUSD · 告警解除", body)

    assert "🟩 <b>告警已解除</b>" in rendered
    assert "原触发条件当前已不成立；不代表价格上涨或行情转好。" in rendered
    assert "触发点位已恢复" not in rendered
    assert "<table bordered striped>" in rendered


def test_mobile_rule_overview_uses_two_columns_deduplicates_and_collapses_quiet() -> None:
    body = "\n".join(
        (
            "XAUUSD monitor",
            "标的：XAUUSD",
            "当前价格：4390",
            "RULES",
            "• 状态：TRIGGERED · 条件：≥ 4380 · "
            "含义：XAUUSD ≥ 4380：突破结构仍在，继续观察黄金方向 · 级别：HIGH",
            "• 状态：QUIET · 条件：≤ 4310 · "
            "含义：XAUUSD ≤ 4310：进入 4290–4310 第一档，新增仓位 · 级别：MEDIUM",
            "• 状态：QUIET · 条件：日线 RSI14 ≥ 80 · "
            "含义：GDXU 日线 RSI14 ≥ 80：进入杠杆工具过热复核 · 级别：MEDIUM",
        )
    )

    rendered = _format_notification_html("🚨 XAUUSD · 新触发", body)

    assert "<th>状态</th><th>规则与含义</th>" in rendered
    assert "<th>含义</th>" not in rendered
    assert "<b>需关注 · 1 项</b>" in rendered
    assert "<details><summary>⚪ 未触发 · 2 项</summary>" in rendered
    assert "<b>≥ 4380</b> · 突破结构仍在，继续观察黄金方向" in rendered
    assert "XAUUSD ≥ 4380：" not in rendered
    assert "GDXU 日线 RSI14 ≥ 80：进入杠杆工具过热复核" in rendered
    assert "…" not in rendered


def test_post_market_digest_renders_each_changed_rule_and_price_delta() -> None:
    body = """POST_MARKET_SUMMARY
运行时间：2026-08-03T12:00:00+00:00
本轮变化：2
MONITOR
XAUUSD monitor
标的：XAUUSD
当前价格：100
价格时间：2026-08-03T12:00:00+00:00
上次价格：99
价格变化：+1 (+1.01%)
CHANGES
• [HIGH] XAU_BREAKOUT_100 · 条件：> 100 · 含义：突破关键位 → TRIGGERED
• [MEDIUM] XAU_PULLBACK_99 · 条件：< 99 · 含义：回落关键位 → RECOVERED
RULES
• 状态：TRIGGERED · 条件：> 100 · 含义：突破关键位 · 级别：HIGH
END_MONITOR
"""

    rendered = _format_notification_html(
        "📊 美股盘后 Monitor · 1 标的 · 2 变化",
        body,
    )

    assert "XAUUSD · 100" in rendered
    assert "↩️ 上次 99 · 较上次 <b>+1 (+1.01%)</b>" in rendered
    assert "🔴 <b>&gt; 100</b> · <b>已触发</b> · 高" in rendered
    assert "🟢 <b>&lt; 99</b> · <b>告警解除</b> · 中" in rendered
    assert "含义：突破关键位" in rendered
    assert "含义：回落关键位" in rendered
    assert "<pre>" not in rendered


def test_price_change_percent_rounds_half_up_and_avoids_negative_zero() -> None:
    repeating = _notification_price_change_lines(
        _NotificationPriceContext(
            instrument_id="equity:US:TEST",
            symbol="TEST",
            price="100",
            price_time=NOW.isoformat(),
            current_available=True,
            previous_price=Decimal("3"),
        )
    )
    half_up = _notification_price_change_lines(
        _NotificationPriceContext(
            instrument_id="equity:US:TEST",
            symbol="TEST",
            price="100.005",
            price_time=NOW.isoformat(),
            current_available=True,
            previous_price=Decimal("100"),
        )
    )
    negative_zero = _notification_price_change_lines(
        _NotificationPriceContext(
            instrument_id="equity:US:TEST",
            symbol="TEST",
            price="99.996",
            price_time=NOW.isoformat(),
            current_available=True,
            previous_price=Decimal("100"),
        )
    )

    assert repeating == ("上次价格：3", "价格变化：+97 (+3233.33%)")
    assert half_up == ("上次价格：100", "价格变化：+0.005 (+0.01%)")
    assert negative_zero == ("上次价格：100", "价格变化：-0.004 (0.00%)")


def test_single_transition_title_contains_bounded_condition() -> None:
    rule = MonitorRuleInput(
        rule_code="XAU_PULLBACK_4080",
        description="黄金回落至 4080 下方提醒。",
        rule_type=MonitorRuleType.PRICE_BELOW,
        severity=MonitorSeverity.MEDIUM,
        instrument_id="future:US:GC=F",
        price_threshold=Decimal("4080"),
        max_fact_age_seconds=3600,
    ).to_domain()
    event = MonitorEvent(
        event_id="monitor_event_00000000-0000-7000-8000-000000000002",
        monitor_id="monitor_00000000-0000-7000-8000-000000000002",
        monitor_version=1,
        rule_code=rule.rule_code,
        event_type=MonitorEventType.TRIGGERED,
        severity=rule.severity,
        observed_value=Decimal("4070"),
        threshold_value=Decimal("4080"),
        fact_as_of=NOW,
        message="Rule condition triggered.",
        created_at=NOW,
    )

    assert _notification_event_label((event,), {rule.rule_code: rule}) == "< 4080 新触发"
    rendered = _format_notification_html(
        "🚨 XAUUSD · < 4080 新触发",
        """XAUUSD monitor
标的：XAUUSD
当前价格：4042.110
价格时间：2026-08-03T12:00:00+00:00
CHANGES
• [MEDIUM] XAU_PULLBACK_4080 · 条件：< 4080 · 含义：黄金回落提醒 → TRIGGERED
RULES
• 状态：TRIGGERED · 条件：< 4080 · 含义：黄金回落提醒 · 级别：MEDIUM
""",
    )
    assert rendered.startswith("<b>🚨 XAUUSD · 4042.110 · &lt; 4080 新触发</b>")


@pytest.mark.asyncio
async def test_monitor_transition_and_post_market_digest_are_durable(
    tmp_path, fixed_clock, id_generator
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'notifications.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    repository.create(
        MonitorDefinition(
            monitor_id="monitor_00000000-0000-7000-8000-000000000001",
            version=1,
            name="黄金回落提醒",
            subject_id=None,
            primary_instrument_id="future:US:GC=F",
            cadence=MonitorCadence.US_POST_MARKET,
            status=MonitorStatus.ACTIVE,
            rules=(
                    MonitorRuleInput(
                        rule_code="GC_PULLBACK_ALERT_4080",
                        description="黄金回落至 4080 下方提醒。",
                    rule_type=MonitorRuleType.PRICE_BELOW,
                    severity=MonitorSeverity.MEDIUM,
                    instrument_id="future:US:GC=F",
                    price_threshold=Decimal("4080"),
                    max_fact_age_seconds=3600,
                ).to_domain(),
                    MonitorRuleInput(
                        rule_code="GC_ABOVE_4000",
                        description="黄金保持在 4000 上方。",
                    rule_type=MonitorRuleType.PRICE_ABOVE,
                    severity=MonitorSeverity.MEDIUM,
                    instrument_id="future:US:GC=F",
                    price_threshold=Decimal("4000"),
                    max_fact_age_seconds=3600,
                ).to_domain(),
            ),
            confirmed_by="user",
            idempotency_key="notification-monitor",
            created_at=NOW,
        )
    )
    fixed_clock.set(NOW)
    quote = ToolEnvelope.success(
        request_id="req_quote",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(SourceReference(name="yfinance", role=SourceRole.PRIMARY),),
        data=USQuoteDTO(
            instrument_id="future:US:GC=F",
            quote_at=NOW,
            session=TradingSession.REGULAR,
            last=Decimal("4070"),
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
        ),
    )
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(return_value=quote)
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )
    sender = MagicMock()
    sender.send = AsyncMock(
        return_value=NotificationSendReceipt(
            delivered=True,
            retryable=False,
            provider_message_id="88",
        )
    )
    service = MonitorNotificationService(
        repository,
        sender,
        fixed_clock,
        enabled=True,
        configured=True,
    )

    request = MonitorEvaluateInput(cadence=MonitorCadence.US_POST_MARKET, as_of=NOW)
    first_run = await evaluator.evaluate(request)
    counts_before = repository.notification_counts(
        MonitorNotificationChannel.TELEGRAM
    )
    pending = repository.list_due_notifications(
        MonitorNotificationChannel.TELEGRAM, NOW, 20
    )
    assert len(pending) == 1
    digest = pending[0]
    assert digest.source_event_id is None
    assert digest.source_run_id == first_run.run_id
    assert "数据来源：yfinance" in digest.body
    assert "状态变化" not in digest.body
    assert "GC_PULLBACK_ALERT_4080" in digest.body
    assert "条件：< 4080" in digest.body
    assert "含义：黄金回落至 4080 下方提醒" in digest.body
    assert "GC_ABOVE_4000" in digest.body
    assert "条件：> 4000" in digest.body
    assert "含义：黄金保持在 4000 上方" in digest.body
    delivery = await service.flush_pending()
    second_run = await evaluator.evaluate(request)
    second_delivery = await service.flush_pending()
    counts_after = repository.notification_counts(
        MonitorNotificationChannel.TELEGRAM
    )

    assert first_run.events_created == 2
    assert counts_before == {MonitorNotificationStatus.PENDING: 1}
    assert delivery.delivered == 1
    assert second_run.events_created == 0
    assert second_delivery.pending_selected == 1
    assert second_delivery.delivered == 1
    assert counts_after == {MonitorNotificationStatus.DELIVERED: 2}
    messages = tuple(call.args[0] for call in sender.send.await_args_list)
    assert all(item.source_event_id is None for item in messages)
    assert any("含义：黄金回落至 4080 下方提醒" in item.body for item in messages)
    assert any("含义：黄金保持在 4000 上方" in item.body for item in messages)
    digests = tuple(
        item for item in messages if item.body.startswith("POST_MARKET_SUMMARY")
    )
    assert len(digests) == 2
    assert all(item.source_run_id is not None for item in digests)
    assert all("当前价格：4070" in item.body for item in digests)
    assert "价格变化：" not in digests[0].body
    assert "上次价格：4070" in digests[1].body
    assert "价格变化：0 (0.00%)" in digests[1].body
    assert all("连续合约存在换月风险" in item.body for item in digests)
    assert sender.send.await_count == 2
    engine.dispose()


@pytest.mark.asyncio
async def test_interval_transition_still_enqueues_event_notification(
    tmp_path, fixed_clock, id_generator
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'interval-notifications.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    repository.create(
        MonitorDefinition(
            monitor_id="monitor_00000000-0000-7000-8000-000000000010",
            version=1,
            name="Interval price alert",
            subject_id=None,
            primary_instrument_id="equity:US:TEST",
            cadence=MonitorCadence.INTERVAL,
            interval_minutes=60,
            status=MonitorStatus.ACTIVE,
            rules=(
                MonitorRuleInput(
                    rule_code="TEST_ABOVE_100",
                    description="Test price is above 100.",
                    rule_type=MonitorRuleType.PRICE_ABOVE,
                    severity=MonitorSeverity.HIGH,
                    instrument_id="equity:US:TEST",
                    price_threshold=Decimal("100"),
                    max_fact_age_seconds=3600,
                ).to_domain(),
                MonitorRuleInput(
                    rule_code="TEST_ABOVE_99",
                    description="Test price is above 99.",
                    rule_type=MonitorRuleType.PRICE_ABOVE,
                    severity=MonitorSeverity.MEDIUM,
                    instrument_id="equity:US:TEST",
                    price_threshold=Decimal("99"),
                    max_fact_age_seconds=3600,
                ).to_domain(),
            ),
            confirmed_by="user",
            idempotency_key="interval-notification-monitor",
            created_at=NOW,
        )
    )
    fixed_clock.set(NOW)
    quote = ToolEnvelope.success(
        request_id="req_interval_quote",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.FRESH,
        sources=(SourceReference(name="yfinance", role=SourceRole.PRIMARY),),
        data=USQuoteDTO(
            instrument_id="equity:US:TEST",
            quote_at=NOW,
            session=TradingSession.REGULAR,
            last=Decimal("101"),
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
        ),
    )
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(return_value=quote)
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )

    run = await evaluator.evaluate(
        MonitorEvaluateInput(cadence=MonitorCadence.INTERVAL, as_of=NOW)
    )
    pending = repository.list_due_notifications(
        MonitorNotificationChannel.TELEGRAM, NOW, 20
    )

    assert run.events_created == 2
    assert len(pending) == 1
    assert pending[0].source_event_id is not None
    assert pending[0].source_run_id is None
    assert "条件：> 100" in pending[0].body
    assert "条件：> 99" in pending[0].body
    assert "2项变化" in pending[0].title
    assert "含义：Test price is above 100" in pending[0].body
    engine.dispose()


@pytest.mark.asyncio
async def test_interval_data_recovery_enqueues_blue_run_notification(
    tmp_path, fixed_clock, id_generator
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'interval-data-recovery.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    repository.create(
        MonitorDefinition(
            monitor_id="monitor_00000000-0000-7000-8000-000000000020",
            version=1,
            name="XAUUSD data recovery",
            subject_id=None,
            primary_instrument_id="commodity_spot:OTC:XAUUSD",
            cadence=MonitorCadence.INTERVAL,
            interval_minutes=60,
            status=MonitorStatus.ACTIVE,
            rules=(
                MonitorRuleInput(
                    rule_code="XAU_ABOVE_2500",
                    description="黄金高于 2500 时提醒。",
                    rule_type=MonitorRuleType.PRICE_ABOVE,
                    severity=MonitorSeverity.HIGH,
                    instrument_id="commodity_spot:OTC:XAUUSD",
                    price_threshold=Decimal("2500"),
                    max_fact_age_seconds=3600,
                ).to_domain(),
            ),
            confirmed_by="user",
            idempotency_key="interval-data-recovery-monitor",
            created_at=NOW,
        )
    )
    failure = ToolEnvelope.failure(
        request_id="req_failed_quote",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        errors=(
            ErrorInfo(
                code="PROVIDER_TIMEOUT_ERROR",
                message="Provider timed out.",
                retryable=True,
                details={
                    "provider_diagnostics": [
                        {
                            "provider": "dukascopy",
                            "stage": "primary_quote",
                            "error_code": "PROVIDER_TIMEOUT_ERROR",
                            "retryable": True,
                            "attempt_count": 1,
                            "error_type": "timeout",
                            "status_class": "none",
                            "status_code": None,
                        }
                    ]
                },
            ),
        ),
    )
    recovered_at = NOW + timedelta(hours=1)
    success = ToolEnvelope.success(
        request_id="req_recovered_quote",
        market=None,
        as_of=recovered_at,
        fetched_at=recovered_at,
        freshness=Freshness.FRESH,
        sources=(SourceReference(name="dukascopy", role=SourceRole.PRIMARY),),
        data=USQuoteDTO(
            instrument_id="commodity_spot:OTC:XAUUSD",
            quote_at=recovered_at,
            session=TradingSession.REGULAR,
            last=Decimal("2400"),
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
        ),
    )
    market = MagicMock()
    market.get_market_snapshot = AsyncMock(side_effect=(failure, success))
    evaluator = MonitorEvaluationService(
        repository,
        MagicMock(),
        market,
        MagicMock(),
        fixed_clock,
        id_generator,
    )

    fixed_clock.set(NOW)
    first = await evaluator.evaluate(
        MonitorEvaluateInput(cadence=MonitorCadence.INTERVAL, as_of=NOW)
    )
    fixed_clock.set(recovered_at)
    second = await evaluator.evaluate(
        MonitorEvaluateInput(cadence=MonitorCadence.INTERVAL, as_of=recovered_at)
    )
    pending = repository.list_due_notifications(
        MonitorNotificationChannel.TELEGRAM, recovered_at, 20
    )

    assert first.events_created == 1
    assert second.events_created == 0
    assert {item.title for item in pending} == {
        "⛔ XAUUSD · 数据源中断",
        "🔵 XAUUSD · 数据恢复",
    }
    recovered_notice = next(item for item in pending if "数据恢复" in item.title)
    assert recovered_notice.source_run_id == second.run_id
    assert "不代表价格上涨或行情转好" in recovered_notice.body
    engine.dispose()
