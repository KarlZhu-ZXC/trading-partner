from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from application.ports.monitor_judgment_provider import (
    MonitorJudgmentRequest,
    MonitorJudgmentResponse,
)
from application.services.monitor_judgment_service import MonitorJudgmentService
from infrastructure.providers.llm import (
    BailianMonitorJudgmentProvider,
    DeepSeekMonitorJudgmentProvider,
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
    await client.aclose()


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
