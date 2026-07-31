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
from application.dto.tool_envelope import ToolEnvelope
from application.dto.us_market import USQuoteDTO
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_notification_service import MonitorNotificationService
from domain.common.enums import Freshness, TradingSession
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorNotificationChannel,
    MonitorNotificationStatus,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition, MonitorNotificationOutboxEntry
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
CHANGES
• [MEDIUM] GC_PULLBACK_ALERT_4080 → TRIGGERED
RULES
RULE                      COND    VALUE   DIST   STATE      LEVEL
------------------------  ------  ------  -----  ---------  ------
GC_PULLBACK_ALERT_4080    < 4080  4078.3  -1.7   TRIGGERED  MEDIUM
GC_STRUCTURE_FAIL_3940    < 3940  4078.3  138.3  QUIET      HIGH
数据提示：DELAYED_US_DATA, FUTURES_CONTRACT_NOT_SPOT
"""

    rendered = _format_notification_html(
        title="🚨 GC=F · TRIGGERED",
        body=body,
    )

    assert rendered.startswith("<b>🚨 GC=F · 4078.3 · TRIGGERED</b>")
    assert "💰 <b>当前价格：4078.3</b>" in rendered
    assert "🕒 价格时间：2026-07-29 08:51 UTC-04:00" in rendered
    assert "<b>本轮结果</b>" in rendered
    assert "🔴 <code>GC_PULLBACK_ALERT_4080</code>" in rendered
    assert "<b>全部监控规则</b>" in rendered
    assert "🔴 <b>&lt; 4080</b> · <b>TRIGGERED</b> · MEDIUM" in rendered
    assert "已低于阈值 1.7" in rendered
    assert "⚪️ <b>&lt; 3940</b> · <b>QUIET</b> · HIGH" in rendered
    assert "距触发 138.3" in rendered
    assert "规则：<code>GC_STRUCTURE_FAIL_3940</code>" in rendered
    assert "<pre>" not in rendered
    assert "数据提示：DELAYED_US_DATA" in rendered
    assert rendered.index("当前价格") < rendered.index("<b>本轮结果</b>")


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
        sources=(),
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
    delivery = await service.flush_pending()
    second_run = await evaluator.evaluate(request)
    second_delivery = await service.flush_pending()
    counts_after = repository.notification_counts(
        MonitorNotificationChannel.TELEGRAM
    )

    assert first_run.events_created == 2
    assert counts_before == {MonitorNotificationStatus.PENDING: 3}
    assert delivery.delivered == 2
    assert second_run.events_created == 0
    assert second_delivery.pending_selected == 1
    assert second_delivery.delivered == 1
    assert counts_after == {MonitorNotificationStatus.DELIVERED: 4}
    messages = tuple(call.args[0] for call in sender.send.await_args_list)
    assert any("GC_PULLBACK_ALERT_4080" in item.body for item in messages)
    digests = tuple(
        item for item in messages if item.body.startswith("POST_MARKET_SUMMARY")
    )
    assert len(digests) == 2
    assert all(item.source_run_id is not None for item in digests)
    assert all("当前价格：4070" in item.body for item in digests)
    assert all("连续合约存在换月风险" in item.body for item in digests)
    assert sender.send.await_count == 3
    engine.dispose()
