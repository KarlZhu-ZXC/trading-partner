from __future__ import annotations

import json

import httpx
import pytest

from application.ports.agent_model_provider import ModelMessage, ModelRequest
from application.ports.monitor_judgment_provider import MonitorJudgmentRequest
from application.ports.trade_retro_narrative_provider import TradeRetroNarrativeRequest
from infrastructure.config.llm import LLMEndpointConfig
from infrastructure.providers.llm.opencode_go import (
    OpenCodeGoModelProvider,
    OpenCodeGoMonitorJudgmentProvider,
    OpenCodeGoTradeRetroNarrativeProvider,
    OpenCodeZenModelProvider,
)


def _config(model: str = "deepseek-v4-flash") -> LLMEndpointConfig:
    return LLMEndpointConfig(
        api_style="chat_completions",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="test-go-key",
        model=model,
        reasoning_mode="thinking",
        reasoning_effort="max",
    )


@pytest.mark.asyncio
async def test_opencode_go_routes_models_to_each_documented_protocol() -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        payload = json.loads(request.content)
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "model": payload["model"],
                    "status": "completed",
                    "output_text": "luna",
                },
            )
        if request.url.path.endswith("/messages"):
            assert request.headers["x-api-key"] == "test-go-key"
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "model": payload["model"],
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "qwen"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "deepseek"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoModelProvider(_config(), client=client)
    base = (ModelMessage(role="user", content="hello"),)

    assert (await provider.complete(ModelRequest(messages=base))).text == "deepseek"
    assert (
        await provider.complete(ModelRequest(messages=base, model="gpt-5.6-luna"))
    ).text == "luna"
    assert (
        await provider.complete(ModelRequest(messages=base, model="muse-spark-1.2-contributor"))
    ).text == "luna"
    assert (
        await provider.complete(ModelRequest(messages=base, model="qwen3.8-max"))
    ).text == "qwen"
    assert paths == [
        "/zen/go/v1/chat/completions",
        "/zen/go/v1/responses",
        "/zen/go/v1/responses",
        "/zen/go/v1/messages",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_go_catalog_exposes_directory_models_and_assigns_efforts() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-5.6-luna"},
                    {"id": "muse-spark-1.2-contributor"},
                    {"id": "qwen3.8-max"},
                    {"id": "deepseek-v4-flash-vision-exp"},
                    {"id": "future-chat-model"},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoModelProvider(_config("gpt-5.6-luna"), client=client)

    catalog = await provider.list_models()

    assert tuple(item.id for item in catalog.models) == (
        "gpt-5.6-luna",
        "muse-spark-1.2-contributor",
        "qwen3.8-max",
        "deepseek-v4-flash-vision-exp",
        "future-chat-model",
    )
    assert catalog.models[0].reasoning_efforts == ("low", "medium", "high", "max")
    assert catalog.models[1].reasoning_efforts == ("low", "medium", "high", "max")
    assert catalog.models[2].reasoning_efforts == ("high", "max")
    assert catalog.models[3].reasoning_efforts == ("high", "max")
    assert catalog.models[4].reasoning_efforts == ("high", "max")
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_go_routes_unknown_catalog_models_to_chat_completions() -> None:
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "deepseek-v4-flash-vision-exp"}]},
            )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash-vision-exp",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "vision-ready"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoModelProvider(
        _config("deepseek-v4-flash-vision-exp"),
        client=client,
    )

    response = await provider.complete(
        ModelRequest(messages=(ModelMessage(role="user", content="hello"),))
    )

    assert response.text == "vision-ready"
    assert paths == ["/zen/go/v1/chat/completions"]
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_go_trade_retro_uses_chat_completions_and_max_reasoning() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash-vision-exp",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"summary_markdown":"本周复盘完成。"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoTradeRetroNarrativeProvider(
        _config("deepseek-v4-flash-vision-exp"),
        max_output_tokens=8000,
        client=client,
    )

    response = await provider.narrate(
        TradeRetroNarrativeRequest(deterministic_facts_json='{"findings":[]}')
    )

    assert response.provider_name == "opencode_go"
    assert response.model == "deepseek-v4-flash-vision-exp"
    assert response.summary_markdown == "本周复盘完成。"
    assert captured["path"] == "/zen/go/v1/chat/completions"
    payload = captured["payload"]
    assert payload["model"] == "deepseek-v4-flash-vision-exp"  # type: ignore[index]
    assert payload["reasoning_effort"] == "max"  # type: ignore[index]
    assert payload["thinking"] == {"type": "enabled"}  # type: ignore[index]
    await provider.aclose()


@pytest.mark.asyncio
async def test_opencode_go_trade_retro_repairs_one_invalid_structure() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        content = "not-json" if len(payloads) == 1 else '{"summary_markdown":"结构修复成功。"}'
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash-vision-exp",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoTradeRetroNarrativeProvider(
        _config("deepseek-v4-flash-vision-exp"),
        max_output_tokens=8000,
        client=client,
    )

    response = await provider.narrate(
        TradeRetroNarrativeRequest(deterministic_facts_json='{"findings":[]}')
    )

    assert response.summary_markdown == "结构修复成功。"
    assert len(payloads) == 2
    assert len(payloads[0]["messages"]) == 2  # type: ignore[arg-type,index]
    assert len(payloads[1]["messages"]) == 3  # type: ignore[arg-type,index]
    await provider.aclose()


@pytest.mark.asyncio
async def test_opencode_go_trade_retro_retries_one_transient_unavailable() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash-vision-exp",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"summary_markdown":"瞬时故障后恢复。"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoTradeRetroNarrativeProvider(
        _config("deepseek-v4-flash-vision-exp"),
        max_output_tokens=8000,
        client=client,
    )

    response = await provider.narrate(
        TradeRetroNarrativeRequest(deterministic_facts_json='{"findings":[]}')
    )

    assert response.summary_markdown == "瞬时故障后恢复。"
    assert attempts == 2
    await provider.aclose()


@pytest.mark.asyncio
async def test_opencode_go_retries_one_empty_provider_contract_response() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "" if attempts == 1 else "recovered",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoModelProvider(_config(), client=client)

    response = await provider.complete(
        ModelRequest(messages=(ModelMessage(role="user", content="hello"),))
    )

    assert response.text == "recovered"
    assert attempts == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_zen_uses_its_own_provider_identity_and_base_path() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/zen/v1/responses"
        return httpx.Response(
            200,
            json={
                "id": "resp_zen",
                "model": "gpt-5.6-luna",
                "status": "completed",
                "output_text": "zen",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeZenModelProvider(
        LLMEndpointConfig(
            api_style="responses",
            base_url="https://opencode.ai/zen/v1",
            api_key="shared-opencode-key",
            model="gpt-5.6-luna",
            reasoning_mode="effort",
            reasoning_effort="high",
        ),
        client=client,
    )

    response = await provider.complete(
        ModelRequest(messages=(ModelMessage(role="user", content="hello"),))
    )

    assert provider.provider_name == "opencode_zen"
    assert response.text == "zen"
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_zen_free_chat_strips_unsupported_reasoning() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "hy3-free",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeZenModelProvider(
        LLMEndpointConfig(
            api_style="responses",
            base_url="https://opencode.ai/zen/v1",
            api_key="shared-opencode-key",
            model="hy3-free",
            reasoning_mode="effort",
            reasoning_effort="max",
        ),
        client=client,
    )

    response = await provider.complete(
        ModelRequest(
            messages=(ModelMessage(role="user", content="hello"),),
            model="hy3-free",
        )
    )

    assert response.text == "ok"
    assert "reasoning_effort" not in captured
    assert "thinking" not in captured
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_zen_catalog_marks_free_chat_reasoning_unsupported() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/zen/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "hy3-free"}, {"id": "x-preview-f-free"}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeZenModelProvider(
        LLMEndpointConfig(
            api_style="responses",
            base_url="https://opencode.ai/zen/v1",
            api_key="shared-opencode-key",
            model="hy3-free",
            reasoning_mode="effort",
            reasoning_effort="max",
        ),
        client=client,
    )

    catalog = await provider.list_models()

    assert catalog.models[0].id == "hy3-free"
    assert catalog.models[0].reasoning_efforts == ()
    assert catalog.models[0].reasoning_supported is False
    assert catalog.models[1].id == "x-preview-f-free"
    assert catalog.models[1].reasoning_efforts == ("low", "high", "max")
    assert catalog.models[1].reasoning_supported is True
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_zen_ox_alpha_forwards_selected_reasoning_effort() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "x-preview-f-free",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeZenModelProvider(
        LLMEndpointConfig(
            api_style="responses",
            base_url="https://opencode.ai/zen/v1",
            api_key="shared-opencode-key",
            model="x-preview-f-free",
            reasoning_mode="effort",
            reasoning_effort="max",
        ),
        client=client,
    )

    response = await provider.complete(
        ModelRequest(
            messages=(ModelMessage(role="user", content="hello"),),
            model="x-preview-f-free",
            reasoning_mode="effort",
            reasoning_effort="high",
        )
    )

    assert response.text == "ok"
    assert captured["reasoning_effort"] == "high"
    assert "thinking" not in captured
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_go_monitor_returns_bounded_structured_judgment_without_web() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        content = json.dumps(
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
                "reasoning": "这个未声明字段必须被边界投影移除",
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_monitor",
                                    "type": "function",
                                    "function": {
                                        "name": "submit_monitor_judgment",
                                        "arguments": content,
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoMonitorJudgmentProvider(
        api_key="test-go-key",
        base_url="https://opencode.ai/zen/go/v1",
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

    assert result.summary == "继续等待新的确认。"
    assert result.web_search_used is False
    assert captured["model"] == "deepseek-v4-flash"
    assert "response_format" not in captured
    tool = captured["tools"][0]["function"]  # type: ignore[index]
    assert tool["name"] == "submit_monitor_judgment"
    assert tool["strict"] is True
    schema = tool["parameters"]
    assert schema["additionalProperties"] is False  # type: ignore[index]
    assert schema["properties"]["urgency"]["enum"] == [  # type: ignore[index]
        "WATCH",
        "ACTION",
        "URGENT",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_go_monitor_falls_back_to_json_object_when_schema_is_rejected() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload.get("tools"):
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        content = json.dumps(
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
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoMonitorJudgmentProvider(
        api_key="test-go-key",
        base_url="https://opencode.ai/zen/go/v1",
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

    assert result.conclusion == "WAIT"
    assert "tools" in payloads[0]
    assert payloads[1]["response_format"] == {"type": "json_object"}
    assert "tools" not in payloads[1]
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_go_monitor_repairs_invalid_structured_output_once() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        content = "not-json"
        if len(payloads) == 2:
            content = json.dumps(
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
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoMonitorJudgmentProvider(
        api_key="test-go-key",
        base_url="https://opencode.ai/zen/go/v1",
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

    assert result.reasoning_effort_used == "high"
    assert len(payloads) == 2
    assert payloads[1]["tools"] == payloads[0]["tools"]
    assert "response_format" not in payloads[1]
    assert "previous answer did not satisfy" in payloads[1]["messages"][-1]["content"]  # type: ignore[index]
    await client.aclose()


@pytest.mark.asyncio
async def test_opencode_go_monitor_supports_messages_models() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/messages")
        content = json.dumps(
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
                "id": "msg_monitor",
                "model": "qwen3.8-max",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": content}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoMonitorJudgmentProvider(
        api_key="test-go-key",
        base_url="https://opencode.ai/zen/go/v1",
        model="qwen3.8-max",
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

    assert result.conclusion == "WAIT"
    assert result.reasoning_effort_used == "max"
    await client.aclose()
