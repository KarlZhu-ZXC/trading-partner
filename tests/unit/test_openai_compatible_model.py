from __future__ import annotations

import json

import httpx
import pytest

from application.ports.agent_model_provider import ModelMessage, ModelRequest
from domain.common.errors import ProviderRateLimitError, ProviderTimeoutError
from infrastructure.config.llm import LLMEndpointConfig
from infrastructure.providers.llm.openai_compatible import OpenAICompatibleModelProvider


def _config(**overrides: object) -> LLMEndpointConfig:
    values: dict[str, object] = {
        "api_style": "chat_completions",
        "base_url": "https://llm.example/v1",
        "api_key": "unit-secret",
        "model": "unit-model",
    }
    values.update(overrides)
    return LLMEndpointConfig(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_provider_posts_chat_payload_and_decodes_text() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "unit-model",
                "choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(_config(), client=client)
    response = await provider.complete(
        ModelRequest(messages=(ModelMessage(role="user", content="你好"),))
    )
    assert response.text == "你好"
    assert captured["authorization"] == "Bearer unit-secret"
    assert captured["payload"]["model"] == "unit-model"  # type: ignore[index]
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_switches_to_responses_without_code_changes() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "responses-model",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "tp_capability_search",
                        "arguments": '{"query":"positions"}',
                    }
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(
        _config(
            api_style="responses",
            model="responses-model",
            native_web_search="responses_web_search",
            native_web_extractor="responses_web_extractor",
        ),
        client=client,
    )
    response = await provider.complete(
        ModelRequest(messages=(ModelMessage(role="user", content="查持仓"),))
    )

    assert captured["path"] == "/v1/responses"
    assert captured["payload"]["model"] == "responses-model"  # type: ignore[index]
    assert captured["payload"]["store"] is False  # type: ignore[index]
    assert {"type": "web_search"} in captured["payload"]["tools"]  # type: ignore[operator,index]
    assert {"type": "web_extractor"} in captured["payload"]["tools"]  # type: ignore[operator,index]
    assert response.tool_calls[0].name == "tp_capability_search"
    assert response.usage is not None and response.usage.total_tokens == 6
    assert response.latency_ms is not None and response.latency_ms >= 0
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_retries_rate_limit_once_then_raises_without_secret() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "do not expose"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(_config(), client=client)
    with pytest.raises(ProviderRateLimitError) as exc_info:
        await provider.complete(ModelRequest(messages=()))
    assert calls == 2
    assert "unit-secret" not in str(exc_info.value)
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_retries_timeout_once_then_raises() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("provider timed out", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(_config(), client=client)
    with pytest.raises(ProviderTimeoutError):
        await provider.complete(ModelRequest(messages=()))
    assert calls == 2
    await provider.aclose()
