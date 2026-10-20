"""Offline protocol/privacy contracts; no live or private model calls."""

from __future__ import annotations

import json

import httpx
import pytest

from application.ports.agent_model_provider import ModelRequest
from domain.common.errors import ProviderAuthenticationError, ProviderRateLimitError
from infrastructure.providers.llm.note_evaluation_protocol import (
    EvaluationProtocolProvider,
    profile_for,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "suffix", "budget"),
    [
        ("opencode_go", "deepseek-v4.1-flash", "/go/openai/v1/chat/completions", None),
        ("opencode_go", "gpt-6-luna", "/go/openai/v1/responses", None),
        ("opencode_go", "qwen3.8-flash", "/go/anthropic/v1/messages", 32000),
        ("opencode_zen", "deepseek-v4.1-flash-free", "/openai/v1/chat/completions", 32000),
    ],
)
async def test_selected_model_uses_native_protocol_and_declared_budget(
    provider, model, suffix, budget
):
    calls = []

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.url.path.endswith(suffix)
        assert request.headers["User-Agent"] == "trading-partner-note-evaluation/1.0"
        assert ("x-opencode-session" in request.headers) == (provider == "opencode_go")
        if suffix.endswith("/responses"):
            assert "max_output_tokens" not in payload
            return httpx.Response(200, json={"status": "completed", "output_text": "ok"})
        if suffix.endswith("/messages"):
            assert payload["max_tokens"] == budget
            return httpx.Response(
                200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
            )
        assert payload.get("max_tokens") == budget
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = EvaluationProtocolProvider(
            provider=provider, model=model, api_key="secret", client=client
        )
        response = await adapter.complete(
            ModelRequest(
                messages=({"role": "user", "content": "offline fixture"},),
                session_id="private-session",
                max_output_tokens=5000,
            )
        )
        assert response.text == "ok"
        assert len(calls) == 1
        saved = json.dumps(adapter.wire_request_snapshots)
        assert "secret" not in saved
        assert "private-session" not in saved


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"), [(401, ProviderAuthenticationError), (429, ProviderRateLimitError)]
)
async def test_authentication_and_quota_fail_once_without_retry(status, error):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "raw private data must not escape"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = EvaluationProtocolProvider(
            provider="opencode_go", model="gpt-6-luna", api_key="secret", client=client
        )
        with pytest.raises(error) as captured:
            await adapter.complete(ModelRequest(session_id="session"))
        assert calls == 1
        assert captured.value.details["status_code"] == status
        assert "raw private" not in str(captured.value)


@pytest.mark.asyncio
async def test_google_tool_round_preserves_schema_and_thought_signature():
    payloads = []
    schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

    async def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        assert request.url.path.endswith(
            "/go/google/v1beta/models/gemini-4-argon-contributor:generateContent"
        )
        assert payload["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == schema
        assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"
        return httpx.Response(
            200,
            json={
                "modelVersion": "reported-build",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "id": "call-1",
                                        "name": "extract",
                                        "args": {"value": "ok"},
                                    },
                                    "thoughtSignature": "opaque-signature",
                                }
                            ]
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 5,
                    "thoughtsTokenCount": 20,
                    "totalTokenCount": 35,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = EvaluationProtocolProvider(
            provider="opencode_go",
            model="gemini-4-argon-contributor",
            api_key="secret",
            client=client,
        )
        request = ModelRequest(
            messages=(
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "fixture"},
            ),
            tools=({"type": "function", "function": {"name": "extract", "parameters": schema}},),
            session_id="session",
        )
        result = await adapter.complete(request)
        assert result.model == "reported-build"
        assert result.usage.output_tokens == 25
        from dataclasses import replace

        from application.ports.agent_model_provider import ModelMessage

        await adapter.complete(
            replace(
                request,
                messages=(
                    *request.messages,
                    ModelMessage(role="assistant", tool_calls=result.tool_calls),
                    ModelMessage(
                        role="tool", name="extract", tool_call_id="call-1", content="repair"
                    ),
                ),
            )
        )
        assert payloads[1]["contents"][1]["parts"][0]["thoughtSignature"] == "opaque-signature"


def test_profiles_do_not_claim_effort_for_models_using_provider_defaults():
    assert profile_for("opencode_go", "mimo-v2.6-flash")["effort"] == "provider_default"
    assert profile_for("opencode_zen", "glm-5.3-flash-free")["reasoning_mode"] == "effort"
    with pytest.raises(ValueError):
        profile_for("opencode_zen", "big-pickle")
