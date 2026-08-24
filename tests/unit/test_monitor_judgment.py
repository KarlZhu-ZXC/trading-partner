from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from application.ports.monitor_judgment_provider import (
    MonitorJudgmentRequest,
    MonitorJudgmentResponse,
)
from application.services.monitor_judgment_service import (
    MonitorJudgmentService,
    _daily_returns_aligned,
)
from domain.common.enums import TradingSession
from domain.common.errors import DataContractError, ProviderTimeoutError
from domain.monitoring.enums import MonitorJudgmentConclusion
from domain.monitoring.models import MonitorJudgment
from domain.us_market.enums import USBarInterval
from infrastructure.providers.llm import (
    BailianChatMonitorJudgmentProvider,
    BailianMonitorJudgmentProvider,
    DeepSeekMonitorJudgmentProvider,
)
from infrastructure.providers.llm.bailian_monitor_judgment import (
    _StructuredResponse as _BailianStructuredResponse,
)
from infrastructure.providers.llm.deepseek_monitor_judgment import (
    _StructuredResponse as _DeepSeekStructuredResponse,
)


@pytest.mark.asyncio
async def test_bailian_adapter_requests_qwen_max_reasoning_search_and_chinese_json() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {
                            "sources": [
                                {"url": "https://example.com/macro"},
                                {"url": "javascript:alert(1)"},
                            ]
                        },
                    },
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "urgency": "WATCH",
                                        "phase": "A",
                                        "market_state": "交易时段已经对齐",
                                        "divergence": "NONE",
                                        "conclusion": "WAIT",
                                        "quantity_min": 0,
                                        "quantity_max": 0,
                                        "summary": "没有新的可执行变化。",
                                        "evidence_feature_ids": ["sessions_aligned"],
                                        "next_trigger": "等待进入下一价格区间",
                                        "invalidation": "确定性事实缺失时失效",
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    },
                ]
            },
        )

    client = httpx.AsyncClient(
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        transport=httpx.MockTransport(handler),
    )
    provider = BailianMonitorJudgmentProvider(
        api_key="test-secret",
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        model="qwen3.8-max",
        reasoning_effort="max",
        web_search_enabled=True,
        output_language="zh-CN",
        timeout_seconds=10,
        max_output_tokens=3000,
        client=client,
    )
    result = await provider.judge(
        MonitorJudgmentRequest(
            playbook="Wait without confirmation.",
            confirmed_state_json="{}",
            feature_snapshot_json='{"sessions_aligned":true}',
            allowed_feature_ids=("sessions_aligned",),
        )
    )

    assert result.conclusion == "WAIT"
    assert result.summary == "没有新的可执行变化。"
    assert result.web_search_used is True
    assert result.web_source_urls == ("https://example.com/macro",)
    assert captured["model"] == "qwen3.8-max"
    assert captured["reasoning"] == {"effort": "max"}
    assert captured["tools"] == [{"type": "web_search"}]
    assert "tool_choice" not in captured
    assert "temperature" not in captured
    assert "前一已完成常规交易时段收盘" in json.dumps(captured, ensure_ascii=False)
    assert "禁止称" in json.dumps(captured, ensure_ascii=False)
    await client.aclose()


@pytest.mark.asyncio
async def test_bailian_adapter_retries_empty_max_reasoning_once_at_high() -> None:
    efforts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        efforts.append(payload["reasoning"]["effort"])
        content = "" if len(efforts) == 1 else json.dumps(
            {
                "urgency": "WATCH",
                "phase": "A",
                "market_state": "状态没有变化",
                "divergence": "NONE",
                "conclusion": "HOLD",
                "quantity_min": 0,
                "quantity_max": 0,
                "summary": "暂时没有行动。",
                "evidence_feature_ids": ["sessions_aligned"],
                "next_trigger": "等待新的确定性事实",
                "invalidation": "关键事实缺失时失效",
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "output": (
                    []
                    if not content
                    else [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    ]
                )
            },
        )

    client = httpx.AsyncClient(
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        transport=httpx.MockTransport(handler),
    )
    provider = BailianMonitorJudgmentProvider(
        api_key="test-secret",
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        model="qwen3.8-max",
        reasoning_effort="max",
        web_search_enabled=True,
        output_language="zh-CN",
        timeout_seconds=10,
        max_output_tokens=8000,
        client=client,
    )
    result = await provider.judge(
        MonitorJudgmentRequest(
            playbook="Hold.",
            confirmed_state_json="{}",
            feature_snapshot_json='{"sessions_aligned":true}',
            allowed_feature_ids=("sessions_aligned",),
        )
    )

    assert efforts == ["max", "high"]
    assert result.reasoning_effort_used == "high"
    await client.aclose()


@pytest.mark.asyncio
async def test_bailian_deepseek_chat_enforces_json_and_repairs_structure_once() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        content = "not json" if len(payloads) == 1 else json.dumps(
            {
                "urgency": "WATCH",
                "phase": "A",
                "market_state": "确定性事实没有变化",
                "divergence": "NONE",
                "conclusion": "WAIT",
                "quantity_min": 0,
                "quantity_max": 0,
                "summary": "继续等待新的确认。",
                "evidence_feature_ids": ["sessions_aligned"],
                "next_trigger": "等待新的确定性触发",
                "invalidation": "关键事实不可用时失效",
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash-0731",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            },
        )

    client = httpx.AsyncClient(
        base_url="https://example.cn/compatible-mode/v1",
        transport=httpx.MockTransport(handler),
    )
    provider = BailianChatMonitorJudgmentProvider(
        api_key="test-bailian-key",
        base_url="https://example.cn/compatible-mode/v1",
        model="deepseek-v4-flash-0731",
        reasoning_effort="max",
        timeout_seconds=10,
        max_output_tokens=3000,
        client=client,
    )

    result = await provider.judge(
        MonitorJudgmentRequest(
            playbook="Wait.",
            confirmed_state_json="{}",
            feature_snapshot_json='{"sessions_aligned":true}',
            allowed_feature_ids=("sessions_aligned",),
        )
    )

    assert result.summary == "继续等待新的确认。"
    assert result.reasoning_effort_used == "high"
    assert provider.provider_name == "bailian"
    assert len(payloads) == 2
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert payloads[1]["response_format"] == {"type": "json_object"}
    assert "previous answer did not satisfy" in payloads[1]["messages"][-1]["content"]  # type: ignore[index]
    await client.aclose()


@pytest.mark.asyncio
async def test_deepseek_adapter_remains_selectable_with_chinese_json() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "urgency": "WATCH",
                                    "phase": "A",
                                    "market_state": "市场状态没有变化",
                                    "divergence": "NONE",
                                    "conclusion": "WAIT",
                                    "quantity_min": 0,
                                    "quantity_max": 0,
                                    "summary": "继续等待确认。",
                                    "evidence_feature_ids": ["sessions_aligned"],
                                    "next_trigger": "等待新的确定性事实",
                                    "invalidation": "关键事实缺失时失效",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(
        base_url="https://api.deepseek.com",
        transport=httpx.MockTransport(handler),
    )
    provider = DeepSeekMonitorJudgmentProvider(
        api_key="test-secret",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        reasoning_effort="max",
        timeout_seconds=10,
        max_output_tokens=3000,
        client=client,
    )

    result = await provider.judge(
        MonitorJudgmentRequest(
            playbook="Wait.",
            confirmed_state_json="{}",
            feature_snapshot_json='{"sessions_aligned":true}',
            allowed_feature_ids=("sessions_aligned",),
        )
    )

    assert result.summary == "继续等待确认。"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["reasoning_effort"] == "max"
    assert captured["response_format"] == {"type": "json_object"}
    assert "previous_regular_session_close" in json.dumps(captured, ensure_ascii=False)
    await client.aclose()


@pytest.mark.parametrize(
    "response_type", (_BailianStructuredResponse, _DeepSeekStructuredResponse)
)
@pytest.mark.parametrize("ambiguous", ("相对昨收上涨", "相对上一根 K 线收盘上涨"))
def test_monitor_llm_rejects_ambiguous_previous_close_language(
    response_type: type,
    ambiguous: str,
) -> None:
    with pytest.raises(ValueError, match="ambiguous previous-close"):
        response_type(
            urgency="WATCH",
            phase="观察阶段",
            market_state="市场状态稳定",
            divergence="NONE",
            conclusion="WAIT",
            quantity_min=0,
            quantity_max=0,
            summary=ambiguous,
            evidence_feature_ids=(),
            next_trigger="等待新的确定性事实",
            invalidation="关键事实缺失时失效",
        )


def test_judgment_guard_downgrades_unaligned_action_and_clamps_quantity() -> None:
    service = MonitorJudgmentService(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    raw = MonitorJudgmentResponse(
        urgency="ACTION",
        phase="A_TOP_RUN",
        market_state="gold and miners diverged",
        divergence="BEARISH",
        conclusion="REDUCE",
        quantity_min=10,
        quantity_max=40,
        summary="Reduce on divergence.",
        evidence_feature_ids=("sessions_aligned",),
        next_trigger="GDX confirmation",
        invalidation="sessions unaligned",
        web_search_used=True,
        web_source_urls=("https://example.com/macro",),
    )

    result = service._validate(  # noqa: SLF001 - compact invariant test
        raw,
        '{"confirmed_position":50,"runner_target_min":30}',
        {"sessions_aligned": False},
        ("sessions_aligned",),
    )

    assert result.conclusion == "WAIT"
    assert result.divergence == "NONE"
    assert (result.quantity_min, result.quantity_max) == (0, 0)
    assert result.web_search_used is True
    assert result.web_source_urls == ("https://example.com/macro",)


def test_judgment_guard_allows_quote_aligned_action_with_stale_return_bars() -> None:
    service = MonitorJudgmentService(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    raw = MonitorJudgmentResponse(
        urgency="ACTION",
        phase="A_TOP_RUN",
        market_state="盘前报价已同步",
        divergence="BULLISH",
        conclusion="BUY_SMALL",
        quantity_min=1,
        quantity_max=3,
        summary="fresh quote spread supports a small add",
        evidence_feature_ids=("relative_strength.gdxu_xau.quote_return_spread_pct",),
        next_trigger="quote spread confirmation",
        invalidation="quote alignment lost",
    )

    result = service._validate(  # noqa: SLF001 - compact invariant test
        raw,
        '{"phase_B_remaining":5}',
        {
            "sessions_aligned": True,
            "quote_sessions_aligned": True,
            "hourly_returns_aligned": False,
            "daily_returns_aligned": False,
        },
        ("relative_strength.gdxu_xau.quote_return_spread_pct",),
    )

    assert result.conclusion == "BUY_SMALL"
    assert result.divergence == "BULLISH"
    assert (result.quantity_min, result.quantity_max) == (1, 3)


def test_judgment_guard_rejects_stale_daily_evidence_despite_fresh_quotes() -> None:
    service = MonitorJudgmentService(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    raw = MonitorJudgmentResponse(
        urgency="ACTION",
        phase="A_TOP_RUN",
        market_state="最新报价已对齐，日线窗口仍过旧。",
        divergence="BEARISH",
        conclusion="REDUCE",
        quantity_min=10,
        quantity_max=15,
        summary="不得用过旧日线价差做减仓判断。",
        evidence_feature_ids=("relative_strength.GDX_GLD.return_1d_spread_pct",),
        next_trigger="等待日线窗口对齐。",
        invalidation="日线事实缺失。",
    )

    result = service._validate(  # noqa: SLF001 - compact invariant test
        raw,
        '{"confirmed_position":50,"runner_target_min":15}',
        {
            "sessions_aligned": True,
            "quote_sessions_aligned": True,
            "hourly_returns_aligned": False,
            "daily_returns_aligned": False,
        },
        ("relative_strength.GDX_GLD.return_1d_spread_pct",),
    )

    assert result.conclusion == "WAIT"
    assert result.divergence == "NONE"
    assert (result.quantity_min, result.quantity_max) == (0, 0)


@pytest.mark.asyncio
async def test_features_use_live_quotes_without_relabeling_old_bars_as_latest() -> None:
    now = datetime(2026, 8, 10, 8, 6, tzinfo=UTC)
    stale_daily = datetime(2026, 8, 5, 20, tzinfo=UTC)
    quote_times = {
        "commodity_spot:OTC:XAUUSD": datetime(2026, 8, 10, 8, 5, tzinfo=UTC),
        "etf:US:GDXU": datetime(2026, 8, 10, 8, 4, tzinfo=UTC),
    }

    async def snapshot(request):
        instrument_id = request.instrument_id
        data = SimpleNamespace(
            quote_at=quote_times[instrument_id],
            session=(
                None
                if instrument_id.endswith("XAUUSD")
                else TradingSession.PRE_MARKET
            ),
            display_price=Decimal("4354.8") if instrument_id.endswith("XAUUSD") else None,
            last=Decimal("132.07") if instrument_id.endswith("GDXU") else None,
            previous_close=(
                Decimal("4340") if instrument_id.endswith("XAUUSD") else Decimal("130")
            ),
            price_basis="mid" if instrument_id.endswith("XAUUSD") else None,
        )
        return SimpleNamespace(
            ok=True,
            data=data,
            sources=(SimpleNamespace(name="live_quote"),),
            warnings=(),
            errors=(),
        )

    async def bars(request):
        count = 5 if request.interval is USBarInterval.SIXTY_MINUTES else 4
        values = tuple(
            SimpleNamespace(timestamp=stale_daily, close=Decimal(100 + index))
            for index in range(count)
        )
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(bars=values),
            sources=(SimpleNamespace(name="historical_bars"),),
            warnings=(),
            errors=(),
        )

    market = MagicMock()
    market.get_market_snapshot = AsyncMock(side_effect=snapshot)
    market.get_market_bars = AsyncMock(side_effect=bars)
    clock = MagicMock()
    clock.now.return_value = now
    service = MonitorJudgmentService(MagicMock(), market, MagicMock(), clock, MagicMock())
    monitor = SimpleNamespace(
        judgment_policy=SimpleNamespace(
            reference_instrument_ids=("commodity_spot:OTC:XAUUSD", "etf:US:GDXU"),
            relative_strength_pairs=(
                ("gdxu_xau", "etf:US:GDXU", "commodity_spot:OTC:XAUUSD"),
            ),
        )
    )

    features, allowed_ids, _signature = await service._features(monitor, ())  # noqa: SLF001

    gdxu = features["instruments"]["etf:US:GDXU"]
    assert gdxu["latest_price"] == "132.07"
    assert gdxu["price_session"] == "pre_market"
    assert gdxu["price_source"] == "quote"
    assert gdxu["price_time"] == quote_times["etf:US:GDXU"].isoformat()
    assert gdxu["hourly_return_as_of"] == stale_daily.isoformat()
    assert gdxu["previous_regular_session_close"] == "130"
    assert gdxu["return_from_previous_regular_session_close_pct"] is not None
    assert features["quote_sessions_aligned"] is True
    assert features["hourly_returns_aligned"] is False
    assert features["daily_returns_aligned"] is False
    assert features["sessions_aligned"] is True
    assert "etf:US:GDXU.hourly_return_as_of" in allowed_ids
    assert "etf:US:GDXU.return_from_previous_regular_session_close_pct" in allowed_ids
    assert (
        "relative_strength.gdxu_xau."
        "return_from_previous_regular_session_close_spread_pct"
    ) in allowed_ids
    assert "quote_sessions_aligned" in allowed_ids
    assert "hourly_returns_aligned" in allowed_ids
    assert "daily_returns_aligned" in allowed_ids


def test_daily_return_alignment_tolerates_weekend_close_hours() -> None:
    now = datetime(2026, 8, 10, 8, 6, tzinfo=UTC)
    features = {
        "commodity_spot:OTC:XAUUSD": {
            "return_1d_pct": "1.0",
            "daily_return_as_of": datetime(2026, 8, 7, 23, 55, tzinfo=UTC).isoformat(),
        },
        "etf:US:GDXU": {
            "return_1d_pct": "2.0",
            # Same Friday trading date in New York, despite a different UTC close hour.
            "daily_return_as_of": datetime(2026, 8, 8, 0, 5, tzinfo=UTC).isoformat(),
        },
    }

    assert _daily_returns_aligned(features, now) is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_quote_alignment_rejects_stale_or_missing_etf_quote() -> None:
    now = datetime(2026, 8, 10, 8, 6, tzinfo=UTC)
    fresh = now - datetime.resolution
    quote_mode = {"missing": False}

    async def snapshot(request):
        if request.instrument_id.endswith("GDXU") and quote_mode["missing"]:
            return SimpleNamespace(ok=False, data=None, sources=(), warnings=(), errors=())
        quote_at = now - timedelta(hours=9) if request.instrument_id.endswith("GDXU") else fresh
        data = SimpleNamespace(
            quote_at=quote_at,
            session=TradingSession.PRE_MARKET,
            display_price=None,
            last=(
                Decimal("132.07")
                if request.instrument_id.endswith("GDXU")
                else Decimal("4354.8")
            ),
            previous_close=(
                Decimal("130")
                if request.instrument_id.endswith("GDXU")
                else Decimal("4340")
            ),
        )
        return SimpleNamespace(ok=True, data=data, sources=(), warnings=(), errors=())

    async def bars(request):
        bar = SimpleNamespace(timestamp=now, close=Decimal("100"))
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(bars=(bar, bar, bar, bar, bar)),
            sources=(),
            warnings=(),
            errors=(),
        )

    market = MagicMock()
    market.get_market_snapshot = AsyncMock(side_effect=snapshot)
    market.get_market_bars = AsyncMock(side_effect=bars)
    clock = MagicMock()
    clock.now.return_value = now
    service = MonitorJudgmentService(MagicMock(), market, MagicMock(), clock, MagicMock())
    monitor = SimpleNamespace(
        judgment_policy=SimpleNamespace(
            reference_instrument_ids=("commodity_spot:OTC:XAUUSD", "etf:US:GDXU"),
            relative_strength_pairs=(),
        )
    )

    stale_features, _, _ = await service._features(monitor, ())  # noqa: SLF001
    assert stale_features["quote_sessions_aligned"] is False
    assert stale_features["sessions_aligned"] is False

    quote_mode["missing"] = True
    missing_features, _, _ = await service._features(monitor, ())  # noqa: SLF001
    assert missing_features["quote_sessions_aligned"] is False
    assert missing_features["sessions_aligned"] is False


@pytest.mark.asyncio
async def test_deepseek_rejects_english_only_monitor_explanations() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "urgency": "WATCH",
                                    "phase": "A",
                                    "market_state": "old close",
                                    "divergence": "NONE",
                                    "conclusion": "WAIT",
                                    "quantity_min": 0,
                                    "quantity_max": 0,
                                    "summary": "wait",
                                    "evidence_feature_ids": ["sessions_aligned"],
                                    "next_trigger": "next quote",
                                    "invalidation": "missing facts",
                                }
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(
        base_url="https://api.deepseek.com", transport=httpx.MockTransport(handler)
    )
    provider = DeepSeekMonitorJudgmentProvider(
        api_key="test-secret",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        timeout_seconds=10,
        max_output_tokens=3000,
        client=client,
    )

    with pytest.raises(DataContractError):
        await provider.judge(
            MonitorJudgmentRequest(
                playbook="Wait.",
                confirmed_state_json="{}",
                feature_snapshot_json='{"sessions_aligned":false}',
                allowed_feature_ids=("sessions_aligned",),
            )
        )
    await client.aclose()


def test_unavailable_judgment_notification_uses_chinese_operational_status() -> None:
    ids = MagicMock()
    ids.new.return_value = "monitor_notification_unavailable"
    service = MonitorJudgmentService(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), ids
    )
    created_at = datetime.now(UTC)
    judgment = SimpleNamespace(
        quantity_max=None,
        quantity_min=None,
        urgency=None,
        phase=None,
        conclusion=None,
        market_state=None,
        summary="复合判断暂时不可用；确定性规则结果仍然有效。",
        divergence=None,
        evidence_feature_ids=(),
        next_trigger=None,
        invalidation=None,
        created_at=created_at,
    )
    event = SimpleNamespace(event_id="monitor_event_unavailable", created_at=created_at)

    notification = service._notification(  # noqa: SLF001
        SimpleNamespace(
            name="黄金监控", primary_instrument_id="commodity_spot:OTC:XAUUSD"
        ),
        judgment,
        event,
    )

    assert notification.title == "🧭 XAUUSD · 判断不可用"
    assert "市场：未生成新的模型判断" in notification.body
    assert "错误码：MONITOR_JUDGMENT_UNAVAILABLE" in notification.body
    assert "未定义" not in notification.body
    assert "UNKNOWN" not in notification.body
    assert "建议数量0" not in notification.body


def test_fallback_contract_failure_notification_explains_call_path() -> None:
    ids = MagicMock()
    ids.new.return_value = "monitor_notification_fallback_failed"
    service = MonitorJudgmentService(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), ids
    )
    created_at = datetime.now(UTC)
    judgment = SimpleNamespace(
        status="FAILED",
        conclusion=None,
        provider="bailian",
        model="deepseek-v4-flash-0731",
        warning_codes=(
            "MONITOR_JUDGMENT_FALLBACK_USED",
            "PRIMARY_PROVIDER_TIMEOUT_ERROR",
        ),
        error_codes=("DATA_CONTRACT_ERROR",),
        created_at=created_at,
    )

    notification = service._failure_notification(  # noqa: SLF001
        SimpleNamespace(
            name="黄金监控", primary_instrument_id="commodity_spot:OTC:XAUUSD"
        ),
        judgment,
        SimpleNamespace(event_id="monitor_event_fallback_failed"),
    )

    assert "失败模型：bailian / deepseek-v4-flash-0731" in notification.body
    assert "主模型失败（PROVIDER_TIMEOUT_ERROR），已尝试 fallback" in notification.body
    assert "模型输出结构校验；未采用不合规结果" in notification.body


def test_weekend_proxy_overrides_model_otc_wording_and_labels_notification_source() -> None:
    ids = MagicMock()
    ids.new.return_value = "monitor_notification_weekend"
    service = MonitorJudgmentService(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), ids
    )
    raw = MonitorJudgmentResponse(
        urgency="WATCH",
        phase="PYRAMID_WAIT",
        market_state="周六OTC黄金报价新鲜。",
        divergence="NONE",
        conclusion="HOLD",
        quantity_min=0,
        quantity_max=0,
        summary="维持观察。",
        evidence_feature_ids=(),
        next_trigger="等待美股常规时段。",
        invalidation="关键结构失效时复核。",
    )

    normalized = service._validate(  # noqa: SLF001
        raw,
        "{}",
        {
            "warning_codes": (
                "PAXG_USDC_WEEKEND_PROXY",
                "WEEKEND_PROXY_NOT_XAUUSD_SPOT",
            ),
            "quote_sessions_aligned": False,
        },
        (),
    )
    judgment = SimpleNamespace(
        status="SUCCEEDED",
        quantity_max=0,
        quantity_min=0,
        urgency=normalized.urgency,
        phase=normalized.phase,
        conclusion=MonitorJudgmentConclusion.HOLD,
        market_state=normalized.market_state,
        summary=normalized.summary,
        divergence=normalized.divergence,
        evidence_feature_ids=(),
        next_trigger=normalized.next_trigger,
        invalidation=normalized.invalidation,
        warning_codes=("PAXG_USDC_WEEKEND_PROXY",),
        created_at=datetime.now(UTC),
    )
    notification = service._notification(  # noqa: SLF001
        SimpleNamespace(
            name="黄金监控", primary_instrument_id="commodity_spot:OTC:XAUUSD"
        ),
        judgment,
        SimpleNamespace(event_id="monitor_event_weekend"),
    )

    assert normalized.market_state.startswith("周末参考：Binance PAXG/USDC")
    assert "非XAUUSD OTC、非LBMA" in normalized.market_state
    assert "周六OTC黄金报价新鲜" not in normalized.market_state
    assert notification.title == "🧭 XAUUSD（PAXG周末参考） · HOLD"


@pytest.mark.asyncio
async def test_model_failure_emits_typed_operational_alert_once() -> None:
    now = datetime.now(UTC)
    repository = MagicMock()
    repository.latest_judgment.return_value = None
    repository.list_judgments.return_value = ()
    provider = MagicMock(
        provider_name="bailian", model="qwen3.8-max", reasoning_effort="high"
    )
    provider.judge = AsyncMock(side_effect=ProviderTimeoutError("timed out"))
    clock = MagicMock()
    clock.now.return_value = now
    ids = MagicMock()
    ids.new.side_effect = (
        "monitor_judgment_failed",
        "monitor_event_unavailable",
        "monitor_notification_unavailable",
    )
    service = MonitorJudgmentService(repository, MagicMock(), provider, clock, ids)
    service._features = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=(
            {"warning_codes": (), "sessions_aligned": True},
            ("sessions_aligned",),
            "feature-signature",
        )
    )
    monitor = SimpleNamespace(
        monitor_id="monitor_gold",
        version=3,
        name="黄金监控",
        primary_instrument_id="commodity_spot:OTC:XAUUSD",
        judgment_policy=SimpleNamespace(
            playbook="等待确定性确认",
            confirmed_state_json="{}",
            prompt_version="monitor-judgment-v1",
        ),
    )

    result = await service.evaluate(
        run_id="monitor_run_gold",
        monitor=monitor,
        observations=(),
        hard_transition=False,
    )

    assert result is not None
    assert result.judgment.status == "FAILED"
    assert result.judgment.error_codes == ("PROVIDER_TIMEOUT_ERROR",)
    assert result.event is not None
    assert result.notification is not None
    assert "错误码：PROVIDER_TIMEOUT_ERROR" in result.notification.body
    assert "未定义" not in result.notification.body
    assert "UNKNOWN" not in result.notification.body
    assert "建议数量0" not in result.notification.body

    repository.latest_judgment.return_value = result.judgment
    ids.new.side_effect = ("monitor_judgment_failed_repeat",)
    repeated = await service.evaluate(
        run_id="monitor_run_gold_repeat",
        monitor=monitor,
        observations=(),
        hard_transition=False,
    )
    assert repeated is not None
    assert repeated.event is None
    assert repeated.notification is None


@pytest.mark.asyncio
async def test_neutral_hold_watch_wait_oscillation_does_not_notify_every_interval() -> None:
    now = datetime.now(UTC)
    previous = MonitorJudgment(
        judgment_id="monitor_judgment_previous",
        run_id="monitor_run_previous",
        monitor_id="monitor_gold",
        monitor_version=1,
        status="SUCCEEDED",
        urgency="WATCH",
        phase="PYRAMID_WAIT",
        market_state="等待同窗报价。",
        divergence="NONE",
        conclusion=MonitorJudgmentConclusion.HOLD,
        quantity_min=0,
        quantity_max=0,
        summary="维持观察。",
        evidence_feature_ids=(),
        next_trigger="等待常规时段。",
        invalidation="结构失效时复核。",
        feature_signature="old-signature",
        result_fingerprint="old-fingerprint",
        provider="bailian",
        model="qwen3.8-max",
        reasoning_effort="high",
        prompt_version="v1",
        warning_codes=(),
        error_codes=(),
        created_at=now - timedelta(hours=4),
    )
    repository = MagicMock()
    repository.latest_judgment.return_value = previous
    repository.list_judgments.return_value = (previous,)
    provider = MagicMock(
        provider_name="bailian", model="qwen3.8-max", reasoning_effort="high"
    )
    provider.judge = AsyncMock(
        return_value=MonitorJudgmentResponse(
            urgency="WATCH",
            phase="PYRAMID_WAIT",
            market_state="仍等待同窗报价。",
            divergence="NONE",
            conclusion="WAIT",
            quantity_min=0,
            quantity_max=0,
            summary="仍无操作。",
            evidence_feature_ids=(),
            next_trigger="等待常规时段。",
            invalidation="结构失效时复核。",
            reasoning_effort_used="high",
        )
    )
    clock = MagicMock()
    clock.now.return_value = now
    ids = MagicMock()
    ids.new.return_value = "monitor_judgment_current"
    service = MonitorJudgmentService(repository, MagicMock(), provider, clock, ids)
    service._features = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=(
            {"warning_codes": (), "quote_sessions_aligned": True},
            (),
            "new-signature",
        )
    )
    monitor = SimpleNamespace(
        monitor_id="monitor_gold",
        version=1,
        name="黄金监控",
        primary_instrument_id="commodity_spot:OTC:XAUUSD",
        judgment_policy=SimpleNamespace(
            playbook="等待确认。",
            confirmed_state_json="{}",
            prompt_version="v1",
        ),
    )

    result = await service.evaluate(
        run_id="monitor_run_current",
        monitor=monitor,
        observations=(),
        hard_transition=False,
    )

    assert result is not None and result.judgment.conclusion is MonitorJudgmentConclusion.WAIT
    assert result.event is None
    assert result.notification is None


@pytest.mark.asyncio
async def test_consecutive_model_failures_with_different_codes_emit_one_interruption() -> None:
    now = datetime.now(UTC)
    previous = MonitorJudgment(
        judgment_id="monitor_judgment_failed_previous",
        run_id="monitor_run_failed_previous",
        monitor_id="monitor_gold",
        monitor_version=1,
        status="FAILED",
        urgency=None,
        phase=None,
        market_state=None,
        divergence=None,
        conclusion=None,
        quantity_min=None,
        quantity_max=None,
        summary="复合判断暂时不可用；确定性规则结果仍然有效。",
        evidence_feature_ids=(),
        next_trigger=None,
        invalidation=None,
        feature_signature="failed-signature",
        result_fingerprint=None,
        provider="bailian",
        model="qwen3.8-max",
        reasoning_effort="high",
        prompt_version="v1",
        warning_codes=(),
        error_codes=("DATA_CONTRACT_ERROR",),
        created_at=now - timedelta(hours=4),
    )
    repository = MagicMock()
    repository.latest_judgment.return_value = previous
    repository.list_judgments.return_value = (previous,)
    provider = MagicMock(
        provider_name="bailian", model="qwen3.8-max", reasoning_effort="high"
    )
    provider.judge = AsyncMock(side_effect=ProviderTimeoutError("timed out"))
    clock = MagicMock()
    clock.now.return_value = now
    ids = MagicMock()
    ids.new.return_value = "monitor_judgment_failed_current"
    service = MonitorJudgmentService(repository, MagicMock(), provider, clock, ids)
    service._features = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=({"warning_codes": ()}, (), "new-failed-signature")
    )
    monitor = SimpleNamespace(
        monitor_id="monitor_gold",
        version=1,
        judgment_policy=SimpleNamespace(
            playbook="等待确认。",
            confirmed_state_json="{}",
            prompt_version="v1",
        ),
    )

    result = await service.evaluate(
        run_id="monitor_run_failed_current",
        monitor=monitor,
        observations=(),
        hard_transition=False,
    )

    assert result is not None and result.judgment.error_codes == (
        "PROVIDER_TIMEOUT_ERROR",
    )
    assert result.event is None
    assert result.notification is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_error", "primary_code"),
    (
        (ProviderTimeoutError("primary timed out"), "PROVIDER_TIMEOUT_ERROR"),
        (DataContractError("ambiguous model output"), "DATA_CONTRACT_ERROR"),
    ),
)
async def test_bailian_failure_falls_back_to_bailian_deepseek_flash(
    primary_error: Exception,
    primary_code: str,
) -> None:
    primary = MagicMock(
        provider_name="bailian", model="qwen3.8-max", reasoning_effort="max"
    )
    primary.judge = AsyncMock(side_effect=primary_error)
    fallback = MagicMock(
        provider_name="bailian",
        model="deepseek-v4-flash-0731",
        reasoning_effort="max",
    )
    fallback.judge = AsyncMock(
        return_value=MonitorJudgmentResponse(
            urgency="WATCH",
            phase="A",
            market_state="盘前报价可用，日线收益仍截止前一已完成常规交易时段收盘。",
            divergence="NONE",
            conclusion="WAIT",
            quantity_min=0,
            quantity_max=0,
            summary="等待交易时段进一步确认。",
            evidence_feature_ids=("sessions_aligned",),
            next_trigger="等待美股常规交易时段。",
            invalidation="确定性行情不可用时失效。",
            reasoning_effort_used="max",
        )
    )
    repository = MagicMock()
    repository.latest_judgment.return_value = None
    repository.list_judgments.return_value = ()
    ids = MagicMock()
    ids.new.return_value = "monitor_judgment_fallback"
    clock = MagicMock()
    clock.now.return_value = datetime.now(UTC)
    service = MonitorJudgmentService(
        repository, MagicMock(), primary, clock, ids, fallback
    )
    service._features = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=(
            {"sessions_aligned": True, "warning_codes": ()},
            ("sessions_aligned",),
            "feature-signature",
        )
    )
    monitor = SimpleNamespace(
        monitor_id="monitor_gold",
        version=1,
        judgment_policy=SimpleNamespace(
            playbook="等待确认。",
            confirmed_state_json="{}",
            prompt_version="v1",
        ),
    )

    result = await service.evaluate(
        run_id="monitor_run_fallback",
        monitor=monitor,
        observations=(),
        hard_transition=False,
    )

    assert result is not None
    assert result.judgment.status == "SUCCEEDED"
    assert result.judgment.provider == "bailian"
    assert result.judgment.model == "deepseek-v4-flash-0731"
    assert "MONITOR_JUDGMENT_FALLBACK_USED" in result.judgment.warning_codes
    assert f"PRIMARY_{primary_code}" in result.judgment.warning_codes
    fallback.judge.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_contract_failure_gets_one_bounded_retry() -> None:
    primary = MagicMock(
        provider_name="bailian", model="qwen3.8-max", reasoning_effort="high"
    )
    primary.judge = AsyncMock(side_effect=ProviderTimeoutError("primary timed out"))
    valid = MonitorJudgmentResponse(
        urgency="WATCH",
        phase="A",
        market_state="盘前报价可用。",
        divergence="NONE",
        conclusion="WAIT",
        quantity_min=0,
        quantity_max=0,
        summary="等待确认。",
        evidence_feature_ids=("sessions_aligned",),
        next_trigger="等待下一轮。",
        invalidation="确定性行情不可用时失效。",
        reasoning_effort_used="high",
    )
    fallback = MagicMock(
        provider_name="bailian",
        model="deepseek-v4-flash-0731",
        reasoning_effort="high",
    )
    fallback.judge = AsyncMock(
        side_effect=(DataContractError("invalid structured output"), valid)
    )
    repository = MagicMock()
    repository.latest_judgment.return_value = None
    repository.list_judgments.return_value = ()
    clock = MagicMock()
    clock.now.return_value = datetime.now(UTC)
    ids = MagicMock()
    ids.new.return_value = "monitor_judgment_fallback_retry"
    service = MonitorJudgmentService(
        repository, MagicMock(), primary, clock, ids, fallback
    )
    service._features = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=(
            {"sessions_aligned": True, "warning_codes": ()},
            ("sessions_aligned",),
            "feature-signature",
        )
    )
    monitor = SimpleNamespace(
        monitor_id="monitor_gold_retry",
        version=1,
        judgment_policy=SimpleNamespace(
            playbook="等待确认。",
            confirmed_state_json="{}",
            prompt_version="v1",
        ),
    )

    result = await service.evaluate(
        run_id="monitor_run_fallback_retry",
        monitor=monitor,
        observations=(),
        hard_transition=False,
    )

    assert result is not None and result.judgment.status == "SUCCEEDED"
    assert "MONITOR_JUDGMENT_FALLBACK_CONTRACT_RETRIED" in result.judgment.warning_codes
    assert fallback.judge.await_count == 2
