"""Telegram adapter and durable Monitor notification outbox coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine

from application.dto.monitor_notifications import NotificationSendReceipt
from application.dto.monitoring import MonitorEvaluateInput, MonitorRuleInput
from application.dto.tool_envelope import SourceReference, ToolEnvelope
from application.dto.us_market import USQuoteDTO
from application.services.monitor_evaluation_service import (
    MonitorEvaluationService,
    _notification_event_label,
    _notification_price_change_lines,
    _NotificationPriceContext,
    _rule_meaning,
)
from application.services.monitor_notification_service import MonitorNotificationService
from domain.common.enums import Freshness, SourceRole, TradingSession
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventType,
    MonitorNotificationChannel,
    MonitorNotificationStatus,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorNotificationOutboxEntry,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from infrastructure.providers.notifications.telegram import (
    TelegramMonitorNotificationAdapter,
    _format_notification_html,
)

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def _outbox_entry() -> MonitorNotificationOutboxEntry:
    return MonitorNotificationOutboxEntry(
        notification_id="monitor_notification_00000000-0000-7000-8000-000000000001",
        source_event_id="monitor_event_00000000-0000-7000-8000-000000000001",
        source_run_id=None,
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
        assert request.url.path.endswith("/sendMessage")
        assert b'"chat_id":"-100123"' in request.content
        assert b'"parse_mode":"HTML"' in request.content
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
    assert "💰 <b>当前价格：4078.3</b>" in rendered
    assert "🕒 价格时间：2026-07-29 08:51 UTC-04:00" in rendered
    assert "↩️ 上次价格：4081.2" in rendered
    assert "📈 较上次：<b>-2.9 (-0.07%)</b>" in rendered
    assert "🟥🟥🟥 <b>新触发点位</b> 🟥🟥🟥" in rendered
    assert "<b>状态较上次发生变化</b>" in rendered
    assert "🟥🟥🟥🟥🟥🟥🟥🟥🟥" in rendered
    assert "📡 数据来源：<b>IG Weekend Gold（Apify）</b>" in rendered
    assert "🔴 <code>GC_PULLBACK_ALERT_4080</code>" in rendered
    assert "<b>全部监控规则</b>" in rendered
    assert "🔴 <b>&lt; 4080</b> · <b>TRIGGERED</b> · MEDIUM" in rendered
    assert "已低于阈值 1.7" in rendered
    assert "⚪️ <b>&lt; 3940</b> · <b>QUIET</b> · HIGH" in rendered
    assert "距触发 138.3" in rendered
    assert "规则：<code>GC_STRUCTURE_FAIL_3940</code>" in rendered
    assert "<pre>" not in rendered
    assert "数据提示：DELAYED_US_DATA" in rendered
    assert "周末口径：IG Weekend Gold CFD 仅作为 XAUUSD 周末波动代理" in rendered
    assert rendered.index("新触发点位") < rendered.index("当前价格")


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
    assert "TTWO_FIRST_CONFIRM_233_9" in rendered
    assert "已高于阈值 12.53" in rendered
    assert "TTWO_NO_CHASE_249_4" in rendered
    assert "距触发 2.97" in rendered
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
    meaning = _rule_meaning(
        "黄金价格回落至关键支撑区域并触发风控提醒，后续观察确认且风险敞口仍然升高。"
    )
    assert len(meaning) <= 32
    assert meaning.endswith("…")

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

    assert "🟥🟥🟥 <b>新触发点位</b> 🟥🟥🟥" in rendered
    assert "🔴 <b>&gt; 4080</b> · <b>已触发</b> · H" in rendered
    assert "🟢 <b>&lt; 4080</b> · <b>已恢复</b> · M" in rendered
    assert "含义：突破 &lt;关键位&gt; &amp; 观察" in rendered
    assert "含义：回落 &lt;关键位&gt; &amp; 已恢复" in rendered
    assert rendered.index("&gt; 4080") < rendered.index("&lt; 4080")
    assert "状态变化" not in rendered.split("<b>全部监控规则</b>", 1)[0]


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
    assert "↩️ 上次价格：99" in rendered
    assert "📈 较上次：<b>+1 (+1.01%)</b>" in rendered
    assert "🔴 <b>&gt; 100</b> · <b>已触发</b> · H" in rendered
    assert "🟢 <b>&lt; 99</b> · <b>已恢复</b> · M" in rendered
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
            case_id=None,
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
            case_id=None,
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

    assert run.events_created == 1
    assert len(pending) == 1
    assert pending[0].source_event_id is not None
    assert pending[0].source_run_id is None
    assert "条件：> 100" in pending[0].body
    assert "含义：Test price is above 100" in pending[0].body
    engine.dispose()
