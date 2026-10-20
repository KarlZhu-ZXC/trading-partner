"""Documented OpenCode wire protocols for explicit, private evaluation only.

No business writes, fallback providers, HTTP retries, or automatic paid overage.
The application's interpretation service still owns schema and attribution checks.
"""

from __future__ import annotations

import json
from dataclasses import replace
from time import perf_counter

import httpx

from application.ports.agent_model_provider import (
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
)
from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from infrastructure.providers.llm.anthropic_messages_codec import AnthropicMessagesCodec
from infrastructure.providers.llm.chat_completions_codec import ChatCompletionsCodec
from infrastructure.providers.llm.openai_compatible import opaque_model_session_id
from infrastructure.providers.llm.responses_codec import ResponsesCodec

GO_MODELS = frozenset(
    {
        "deepseek-v4.1-flash",
        "glm-5.3-flash",
        "gpt-6-luna",
        "grok-4.7",
        "hy4-preview",
        "mimo-v2.6-flash",
        "mimo-v2.6-pro",
        "qwen3.8-flash",
        "kimi-k3",
        "beam-free",
        "longcat-2.5-preview-free",
        "minimax-m3.1-flash-free",
        "gemini-4-argon-contributor",
        "muse-spark-1.3-contributor",
    }
)
ZEN_MODELS = frozenset(
    {
        "deepseek-v4.1-flash-free",
        "glm-5.3-flash-free",
        "longcat-2.5-preview-free",
        "minimax-m3.1-flash-free",
    }
)


def profile_for(provider: str, model: str) -> dict:
    allowed = GO_MODELS if provider == "opencode_go" else ZEN_MODELS
    if provider not in {"opencode_go", "opencode_zen"} or model not in allowed:
        raise ValueError("Model is not in the selected evaluation allowlist")
    family, effort, mode = "chat", "provider_default", "none"
    if model in {"gpt-6-luna", "grok-4.7", "muse-spark-1.3-contributor"}:
        family, effort, mode = "responses", "high", "effort"
    elif model == "gemini-4-argon-contributor":
        family, effort, mode = "google", "high", "thinking"
    elif model == "qwen3.8-flash":
        family = "messages"
    elif model.startswith("deepseek-"):
        effort, mode = "high", "thinking"
    elif model.startswith("glm-5.3-flash"):
        effort, mode = "high", "effort"
    return {
        "family": family,
        "effort": effort,
        "reasoning_mode": mode,
        "output_budget": 32000
        if provider == "opencode_zen" or family in {"messages", "google"}
        else None,
        "http_attempts_per_completion": 1,
    }


class EvaluationProtocolProvider:
    def __init__(self, *, provider: str, model: str, api_key: str, client: httpx.AsyncClient):
        self.provider, self.model, self.api_key, self.client = provider, model, api_key, client
        self.profile = profile_for(provider, model)
        self.google_parts: dict[str, dict] = {}
        self.wire_request_snapshots: list[dict] = []

    async def aclose(self):
        """The evaluation runner owns the shared, credential-free HTTP client."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        profile = self.profile
        effort = None if profile["effort"] == "provider_default" else profile["effort"]
        effective = replace(
            request,
            model=self.model,
            reasoning_mode=profile["reasoning_mode"],
            reasoning_effort=effort,
            max_output_tokens=profile["output_budget"],
            native_web_search=False,
        )
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "trading-partner-note-evaluation/1.0",
        }
        go = self.provider == "opencode_go"
        root = "https://opencode.ai/inference" + ("/go" if go else "")
        if go:
            if not request.session_id:
                raise DataContractError("Evaluation request lacks session identity")
            headers["x-opencode-session"] = opaque_model_session_id(request.session_id)
        family = profile["family"]
        if family == "chat":
            path = "/openai/v1/chat/completions"
            payload = ChatCompletionsCodec.encode(effective, max_output_tokens=32000)
            if go:
                payload.pop("max_tokens", None)
        elif family == "responses":
            path = "/openai/v1/responses"
            payload = ResponsesCodec.encode(effective, max_output_tokens=32000)
            if go:
                payload.pop("max_output_tokens", None)
        elif family == "messages":
            path = "/anthropic/v1/messages"
            payload = AnthropicMessagesCodec.encode(
                effective, model=self.model, max_output_tokens=32000
            )
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            path = "/google/v1beta/models/" + self.model + ":generateContent"
            payload = self._google_encode(effective)
        # Private experiment material only; never retain headers or credentials.
        self.wire_request_snapshots.append({"path": path, "payload": payload})
        started = perf_counter()
        try:
            response = await self.client.post(root + path, headers=headers, json=payload)
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError(
                "Evaluation request timed out", details={"attempts": 1}
            ) from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError(
                "Evaluation transport failed",
                details={"error_type": type(error).__name__, "attempts": 1},
            ) from error
        status = response.status_code
        details = {"status_code": status, "attempts": 1}
        if status in {401, 403}:
            raise ProviderAuthenticationError(
                "Evaluation authentication/access failed", details=details
            )
        if status == 429:
            raise ProviderRateLimitError("Evaluation quota/rate limit reached", details=details)
        if 400 <= status < 500:
            raise ProviderRequestRejectedError("Evaluation request rejected", details=details)
        if status >= 400:
            raise ProviderUnavailableError("Evaluation upstream unavailable", details=details)
        try:
            raw = response.json()
        except ValueError as error:
            raise DataContractError("Evaluation returned non-JSON data") from error
        if not isinstance(raw, dict):
            raise DataContractError("Evaluation response is not an object")
        if family == "chat":
            result = ChatCompletionsCodec.decode(raw)
        elif family == "responses":
            result = ResponsesCodec.decode(raw)
        elif family == "messages":
            result = AnthropicMessagesCodec.decode(raw)
        else:
            result = self._google_decode(raw)
        if result.finish_reason in {"length", "max_tokens", "MAX_TOKENS"}:
            raise ProviderUnavailableError(
                "Evaluation output truncated",
                details={"failure_kind": "token_limit", "attempts": 1},
                retryable=False,
            )
        return replace(
            result,
            model=result.model or self.model,
            latency_ms=round((perf_counter() - started) * 1000),
        )

    def _google_encode(self, request: ModelRequest) -> dict:
        system, contents = [], []
        for item in request.messages:
            message = item.as_dict() if hasattr(item, "as_dict") else dict(item)
            role, content = message.get("role"), message.get("content")
            if role == "system":
                system.append({"text": content or ""})
                continue
            if role == "tool":
                parts = [
                    {
                        "functionResponse": {
                            "name": message.get("name"),
                            "response": {"result": content or ""},
                        }
                    }
                ]
            else:
                parts = [{"text": content}] if content else []
                for call in message.get("tool_calls", ()):
                    function = call["function"]
                    parts.append(
                        self.google_parts.get(
                            call["id"],
                            {
                                "functionCall": {
                                    "name": function["name"],
                                    "args": json.loads(function["arguments"]),
                                },
                            },
                        )
                    )
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        declarations = []
        for item in request.tools:
            tool = item.as_dict() if hasattr(item, "as_dict") else dict(item)
            function = tool["function"]
            declarations.append(
                {
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "parametersJsonSchema": function["parameters"],
                }
            )
        return {
            "systemInstruction": {"parts": system},
            "contents": contents,
            "tools": [{"functionDeclarations": declarations}],
            "toolConfig": {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [x["name"] for x in declarations],
                }
            },
            "generationConfig": {
                "maxOutputTokens": 32000,
                "thinkingConfig": {"thinkingLevel": "high"},
            },
        }

    def _google_decode(self, raw: dict) -> ModelResponse:
        candidates = raw.get("candidates", [])
        if len(candidates) != 1:
            raise DataContractError("Evaluation Gemini response requires one candidate")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text, calls = [], []
        for index, part in enumerate(parts):
            function = part.get("functionCall")
            if isinstance(function, dict):
                call_id = function.get("id") or f"google_call_{index}"
                self.google_parts[call_id] = part
                calls.append(
                    ModelToolCall(
                        id=call_id,
                        name=function["name"],
                        arguments=json.dumps(function.get("args", {}), ensure_ascii=False),
                    )
                )
            elif isinstance(part.get("text"), str) and not part.get("thought"):
                text.append(part["text"])
        metadata = raw.get("usageMetadata", {})
        output = metadata.get("candidatesTokenCount")
        thought = metadata.get("thoughtsTokenCount", 0)
        if isinstance(output, int) and isinstance(thought, int):
            output += thought
        return ModelResponse(
            text="".join(text),
            tool_calls=tuple(calls),
            model=raw.get("modelVersion"),
            finish_reason=candidate.get("finishReason"),
            usage=ModelUsage(
                input_tokens=metadata.get("promptTokenCount"),
                output_tokens=output,
                total_tokens=metadata.get("totalTokenCount"),
            ),
        )
