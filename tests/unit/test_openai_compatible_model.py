from __future__ import annotations

import json

import httpx
import pytest

from application.ports.agent_model_provider import ModelMessage, ModelRequest
from domain.common.errors import (
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
)
from infrastructure.config.llm import LLMEndpointConfig
from infrastructure.providers.llm.anthropic_messages_codec import AnthropicMessagesCodec
from infrastructure.providers.llm.chat_completions_codec import ChatCompletionsCodec
from infrastructure.providers.llm.openai_compatible import OpenAICompatibleModelProvider
from infrastructure.providers.llm.responses_codec import ResponsesCodec


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
async def test_provider_maps_http_400_to_nonretryable_request_rejected() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "must-not-export"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(_config(), client=client)

    with pytest.raises(ProviderRequestRejectedError) as caught:
        await provider.complete(ModelRequest())

    assert caught.value.code == "PROVIDER_REQUEST_REJECTED"
    assert caught.value.retryable is False
    assert caught.value.details == {"status_code": 400}
    assert "must-not-export" not in repr(caught.value)
    await provider.aclose()


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


def test_multimodal_content_is_encoded_for_chat_completions() -> None:
    payload = ChatCompletionsCodec.encode(
        ModelRequest(
            messages=(
                ModelMessage(
                    role="user",
                    content=(
                        {"type": "text", "text": "Inspect this"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,abc="},
                        },
                    ),
                ),
            )
        ),
        model="vision-model",
    )
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith(  # type: ignore[index]
        "data:image/png"
    )


def test_multimodal_content_is_translated_for_responses_and_messages() -> None:
    request = ModelRequest(
        messages=(
            ModelMessage(
                role="user",
                content=(
                    {"type": "text", "text": "Inspect this"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc="},
                    },
                ),
            ),
        )
    )
    responses = ResponsesCodec.encode(request, model="vision-model")
    messages = AnthropicMessagesCodec.encode(
        request,
        model="vision-model",
        max_output_tokens=1000,
    )
    assert responses["input"][0]["content"][1]["type"] == "input_image"  # type: ignore[index]
    assert messages["messages"][0]["content"][1]["type"] == "image"  # type: ignore[index]


@pytest.mark.asyncio
async def test_provider_uses_catalog_selected_model_in_wire_payload() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(_config(), client=client)
    response = await provider.complete(ModelRequest(model="another/model", messages=()))

    assert captured["payload"]["model"] == "another/model"  # type: ignore[index]
    assert response.model == "another/model"
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_streams_chat_deltas_without_buffering_full_answer() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        body = (
            'data: {"id":"req_stream","model":"unit-model",'
            '"choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}\n\n'
            'data: {"id":"req_stream","model":"unit-model",'
            '"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(_config(), client=client)
    chunks = [chunk async for chunk in provider.stream(ModelRequest())]
    assert [chunk.text_delta for chunk in chunks[:2]] == ["Hel", "lo"]
    assert chunks[-1].done is True
    assert chunks[0].request_id == "req_stream"
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_keeps_responses_item_and_call_ids_on_one_tool_call() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        body = "".join(
            (
                'data: {"type":"response.output_item.added","output_index":0,'
                '"item":{"type":"function_call","id":"msg_tool_1",'
                '"call_id":"call_tool_1","name":"tp_read","arguments":""}}\n\n',
                'data: {"type":"response.function_call_arguments.delta",'
                '"output_index":0,"item_id":"msg_tool_1",'
                '"delta":"{\\"capability\\":\\"market_data_get\\"}"}\n\n',
                'data: {"type":"response.completed","response":{"id":"resp_1",'
                '"model":"responses-model","status":"completed","output":['
                '{"type":"function_call","id":"msg_tool_1",'
                '"call_id":"call_tool_1","name":"tp_read",'
                '"arguments":"{\\"capability\\":\\"market_data_get\\"}"}]}}\n\n',
            )
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(
        _config(api_style="responses", model="responses-model"),
        client=client,
    )
    chunks = [chunk async for chunk in provider.stream(ModelRequest())]

    calls = [call for chunk in chunks for call in chunk.tool_calls]
    assert [(call.id, call.name) for call in calls] == [
        ("call_tool_1", "tp_read"),
        ("call_tool_1", ""),
    ]
    assert chunks[-1].final_response is not None
    assert chunks[-1].final_response.tool_calls[0].id == "call_tool_1"
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_lists_models_with_bounded_cache_and_safe_metadata() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer unit-secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "unit-model"},
                    {
                        "id": "reasoning/model-v2",
                        "supported_reasoning_efforts": ["low", "high", "invalid"],
                    },
                    {"id": "bad model id"},
                    {"id": "image-generation-model"},
                    {"id": "reasoning/model-v2"},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(_config(), client=client)
    first = await provider.list_models()
    second = await provider.list_models()

    assert calls == 1
    assert [item.id for item in first.models] == ["unit-model", "reasoning/model-v2"]
    assert first.models[1].reasoning_efforts == ("low", "high")
    assert first.cached is False
    assert second.cached is True
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
