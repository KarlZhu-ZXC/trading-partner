"""OpenCode Go subscription adapters for Agent and Monitor judgment."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from application.ports.agent_model_provider import (
    AgentModelProvider,
    ModelCatalog,
    ModelCatalogItem,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
)
from application.ports.monitor_judgment_provider import (
    MonitorJudgmentRequest,
    MonitorJudgmentResponse,
)
from application.ports.trade_retro_narrative_provider import (
    TradeRetroNarrativeRequest,
    TradeRetroNarrativeResponse,
)
from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from infrastructure.config.llm import LLMEndpointConfig
from infrastructure.providers.llm.anthropic_messages_codec import AnthropicMessagesCodec
from infrastructure.providers.llm.bailian_trade_retro import (
    _SYSTEM_PROMPT as _TRADE_RETRO_SYSTEM_PROMPT,
)
from infrastructure.providers.llm.bailian_trade_retro import (
    _Response as _TradeRetroResponse,
)
from infrastructure.providers.llm.deepseek_monitor_judgment import (
    _STRUCTURE_REPAIR_PROMPT,
    _SYSTEM_PROMPT,
    _StructuredResponse,
)
from infrastructure.providers.llm.openai_compatible import OpenAICompatibleModelProvider

_GO_RESPONSES_MODELS = frozenset({"grok-4.5", "gpt-5.6-luna", "muse-spark-1.2-contributor"})
_ZEN_RESPONSES_MODELS = frozenset(
    {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "muse-spark-1.2",
        "muse-spark-1.2-contributor-free",
    }
)
_ZEN_FREE_CHAT_MODELS = frozenset(
    {
        "big-pickle",
        "x-preview-f-free",
        "mimo-v2.5-free",
        "hy3-free",
        "nemotron-3-ultra-free",
        "nemotron-3.5-lightning-free",
    }
)
_ZEN_REASONING_FREE_CHAT_MODELS = frozenset({"x-preview-f-free"})
_ZEN_PLAIN_FREE_CHAT_MODELS = _ZEN_FREE_CHAT_MODELS - _ZEN_REASONING_FREE_CHAT_MODELS
_MESSAGES_MODELS = frozenset(
    {
        "minimax-m3",
        "minimax-m2.7",
        "minimax-m2.5",
        "qwen3.8-max",
        "qwen3.7-max",
        "qwen3.7-plus",
        "qwen3.6-plus",
        "qwen3.5-plus",
    }
)
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_TRADE_RETRO_STRUCTURE_REPAIR_PROMPT = (
    "上一响应未通过结构校验。仅重新输出一个 JSON object，且只能包含 "
    "summary_markdown 字段；不得输出代码围栏、解释或额外字段。事实与结论仍只能来自前一条用户 JSON。"
)
_MONITOR_TOOL_NAME = "submit_monitor_judgment"
_MONITOR_TOOL_INSTRUCTION = (
    "Call submit_monitor_judgment exactly once with the complete judgment. "
    "Do not return the judgment as ordinary text."
)


class OpenCodeGoModelProvider(AgentModelProvider):
    """Route each Go model to its documented wire protocol."""

    provider_name = "opencode_go"
    display_name = "OpenCode Go"
    responses_models = _GO_RESPONSES_MODELS

    def __init__(
        self,
        config: LLMEndpointConfig,
        *,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if _SAFE_MODEL_ID.fullmatch(config.model) is None:
            raise DataContractError(f"Configured {self.display_name} model identifier is invalid")
        self.config = config
        self.model = config.model
        self.reasoning_mode = config.reasoning_mode
        self.reasoning_effort = config.reasoning_effort
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            proxy=proxy_url,
            follow_redirects=False,
        )
        self._chat = OpenAICompatibleModelProvider(
            replace(config, api_style="chat_completions"),
            client=self._client,
        )
        self._plain_chat = OpenAICompatibleModelProvider(
            replace(
                config,
                api_style="chat_completions",
                reasoning_mode="none",
                reasoning_effort=None,
            ),
            client=self._client,
        )
        self._responses = OpenAICompatibleModelProvider(
            replace(
                config,
                api_style="responses",
                reasoning_mode="effort",
                native_web_search="disabled",
                native_web_extractor="disabled",
            ),
            client=self._client,
        )

    @staticmethod
    def _model(request: ModelRequest, fallback: str) -> str:
        model = request.model or fallback
        if not isinstance(model, str) or _SAFE_MODEL_ID.fullmatch(model) is None:
            raise DataContractError("OpenCode model selection is unavailable")
        return model

    async def complete(self, request: ModelRequest) -> ModelResponse:
        model = self._model(request, self.model)
        effective = replace(request, model=model, native_web_search=False)
        last_error: DataContractError | None = None
        for _attempt in range(2):
            try:
                response = await self._complete_once(effective, model=model)
            except ProviderRequestRejectedError:
                raise
            except DataContractError as error:
                last_error = error
                continue
            if response.text.strip() or response.tool_calls:
                return response
            last_error = DataContractError(
                f"{self.display_name} returned neither text nor tool calls"
            )
        assert last_error is not None
        raise last_error

    async def _complete_once(
        self,
        request: ModelRequest,
        *,
        model: str,
    ) -> ModelResponse:
        if model in _ZEN_PLAIN_FREE_CHAT_MODELS:
            request = replace(
                request,
                reasoning_mode="none",
                reasoning_effort=None,
            )
        if model in self.responses_models:
            return await self._responses.complete(request)
        if model in _ZEN_PLAIN_FREE_CHAT_MODELS:
            return await self._plain_chat.complete(request)
        if model in _ZEN_REASONING_FREE_CHAT_MODELS:
            return await self._chat.complete(request)
        if model in _MESSAGES_MODELS:
            started = perf_counter()
            payload = AnthropicMessagesCodec.encode(
                request,
                model=model,
                max_output_tokens=self.config.max_output_tokens,
            )
            raw = await self._request_messages(payload)
            result = AnthropicMessagesCodec.decode(raw)
            return replace(
                result,
                model=result.model or model,
                latency_ms=max(0, round((perf_counter() - started) * 1000)),
            )
        # OpenCode Go's directory is the source of truth for model identity,
        # but it currently returns only IDs, not protocol metadata.  Treat an
        # otherwise unknown model as Chat Completions, the documented default
        # route for newly-added Go models.  Known Messages/Responses models
        # remain explicitly routed above.
        return await self._chat.complete(request)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        model = self._model(request, self.model)
        effective = replace(request, model=model, native_web_search=False)
        if model in self.responses_models:
            async for chunk in self._responses.stream(effective):
                yield chunk
            return
        if model not in _MESSAGES_MODELS and model not in _ZEN_FREE_CHAT_MODELS:
            async for chunk in self._chat.stream(effective):
                yield chunk
            return
        # The Messages route is normalized as a final-only chunk. This avoids
        # replaying partial tool-use JSON while preserving the shared runtime.
        response = await self.complete(effective)
        yield ModelStreamChunk(
            final_response=response,
            model=response.model,
            request_id=response.request_id,
            latency_ms=response.latency_ms,
            done=True,
        )

    async def list_models(self, *, force_refresh: bool = False) -> ModelCatalog:
        raw = await self._chat.list_models(force_refresh=force_refresh)
        items = tuple(
            ModelCatalogItem(
                id=item.id,
                reasoning_efforts=(
                    ()
                    if item.id in _ZEN_PLAIN_FREE_CHAT_MODELS
                    else ("low", "high", "max")
                    if item.id in _ZEN_REASONING_FREE_CHAT_MODELS
                    else ("low", "medium", "high", "max")
                    if item.id in self.responses_models
                    else ("high", "max")
                ),
                reasoning_supported=item.id not in _ZEN_PLAIN_FREE_CHAT_MODELS,
            )
            for item in raw.models
        )
        if not items:
            raise DataContractError(f"{self.display_name} model directory has no usable model IDs")
        return replace(raw, models=items)

    async def _request_messages(self, payload: dict[str, object]) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}/messages"
        for attempt in range(2):
            try:
                response = await self._client.post(
                    url,
                    headers={
                        "x-api-key": self.config.api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.TimeoutException as error:
                if attempt == 0:
                    continue
                raise ProviderTimeoutError(
                    f"{self.display_name} Messages request timed out"
                ) from error
            except httpx.HTTPError as error:
                if attempt == 0:
                    continue
                raise ProviderUnavailableError(
                    f"{self.display_name} Messages transport failed"
                ) from error
            if response.status_code in {401, 403}:
                raise ProviderAuthenticationError(
                    f"{self.display_name} authentication or model entitlement failed",
                    details={"status_code": response.status_code},
                )
            if response.status_code == 429:
                raise ProviderRateLimitError(f"{self.display_name} subscription limit was reached")
            if response.status_code >= 400:
                raise ProviderUnavailableError(
                    f"{self.display_name} Messages request failed",
                    details={"status_code": response.status_code},
                )
            try:
                value = response.json()
            except ValueError as error:
                raise DataContractError(
                    f"{self.display_name} returned a non-JSON response"
                ) from error
            if not isinstance(value, dict):
                raise DataContractError(f"{self.display_name} response is not an object")
            return value
        raise ProviderUnavailableError(f"{self.display_name} Messages request failed")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class OpenCodeGoMonitorJudgmentProvider:
    provider_name = "opencode_go"
    display_name = "OpenCode Go"
    model_provider_type = OpenCodeGoModelProvider

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float,
        max_output_tokens: int,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._provider = self.model_provider_type(
            LLMEndpointConfig(
                api_style="chat_completions",
                base_url=base_url,
                api_key=api_key,
                model=model,
                reasoning_mode="thinking",
                reasoning_effort=reasoning_effort,
                native_web_search="disabled",
                native_web_extractor="disabled",
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            ),
            proxy_url=proxy_url,
            client=client,
        )
        self._max_output_tokens = max_output_tokens

    async def judge(self, request: MonitorJudgmentRequest) -> MonitorJudgmentResponse:
        user_payload = json.dumps(
            {
                "playbook": request.playbook,
                "confirmed_state": json.loads(request.confirmed_state_json),
                "features": json.loads(request.feature_snapshot_json),
                "allowed_feature_ids": request.allowed_feature_ids,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        base_messages: tuple[dict[str, str], ...] = (
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        )
        schema = _StructuredResponse.model_json_schema()
        use_tool_schema = (
            self.model not in self._provider.responses_models and self.model not in _MESSAGES_MODELS
        )
        use_response_schema = not use_tool_schema
        json_output = False
        validation_error: Exception | None = None
        result: _StructuredResponse | None = None
        effective_effort = self.reasoning_effort
        for attempt in range(2):
            messages = (
                (
                    {
                        "role": "system",
                        "content": f"{_SYSTEM_PROMPT}\n{_MONITOR_TOOL_INSTRUCTION}",
                    },
                    base_messages[1],
                )
                if use_tool_schema
                else base_messages
            )
            if attempt == 1:
                messages = (
                    *messages,
                    {"role": "user", "content": _STRUCTURE_REPAIR_PROMPT},
                )
                if effective_effort == "max":
                    effective_effort = "high"
            model_request = ModelRequest(
                messages=messages,
                tools=(
                    {
                        "type": "function",
                        "function": {
                            "name": _MONITOR_TOOL_NAME,
                            "description": "Submit one bounded Monitor judgment.",
                            "strict": True,
                            "parameters": schema,
                        },
                    },
                )
                if use_tool_schema
                else (),
                model=self.model,
                reasoning_mode="thinking",
                reasoning_effort=effective_effort,
                max_output_tokens=self._max_output_tokens,
                native_web_search=False,
                json_output=json_output,
                response_schema_name="monitor_judgment",
                response_schema=schema if use_response_schema else None,
            )
            try:
                response = await self._provider.complete(model_request)
            except ProviderRequestRejectedError:
                if json_output:
                    raise
                use_tool_schema = False
                use_response_schema = False
                json_output = True
                response = await self._provider.complete(
                    replace(
                        model_request,
                        tools=(),
                        json_output=True,
                        response_schema_name=None,
                        response_schema=None,
                    )
                )
            try:
                content = _monitor_structured_content(response)
                result = _validate_monitor_content(content)
                break
            except (DataContractError, TypeError, ValueError, ValidationError) as error:
                validation_error = error
        if result is None:
            raise DataContractError(
                f"{self.display_name} returned an invalid Monitor judgment"
            ) from validation_error
        if result.quantity_max < result.quantity_min:
            raise DataContractError(f"{self.display_name} returned inverted quantity bounds")
        return MonitorJudgmentResponse(
            **result.model_dump(),
            reasoning_effort_used=effective_effort,
        )

    async def aclose(self) -> None:
        await self._provider.aclose()


def _monitor_structured_content(response: ModelResponse) -> str:
    if response.tool_calls:
        if len(response.tool_calls) != 1 or response.tool_calls[0].name != _MONITOR_TOOL_NAME:
            raise DataContractError("OpenCode returned an unexpected Monitor judgment tool call")
        return response.tool_calls[0].arguments
    return response.text


def _validate_monitor_content(content: str) -> _StructuredResponse:
    try:
        value = json.loads(_strip_json_fence(content))
    except (TypeError, ValueError) as error:
        raise DataContractError("OpenCode Monitor judgment is not valid JSON") from error
    if not isinstance(value, dict):
        raise DataContractError("OpenCode Monitor judgment is not an object")
    # Some OpenAI-compatible gateways accept a function parameter schema but do
    # not enforce ``additionalProperties=false``. Project only declared fields
    # before the strict local validation; never synthesize a missing field or
    # reinterpret a value, enum, quantity, or evidence identifier.
    projected = {
        field: value[field] for field in _StructuredResponse.model_fields if field in value
    }
    return _StructuredResponse.model_validate(projected)


class OpenCodeGoTradeRetroNarrativeProvider:
    """Use the OpenCode Go Chat Completions route for Trade Retro narration."""

    provider_name = "opencode_go"

    def __init__(
        self,
        config: LLMEndpointConfig,
        *,
        max_output_tokens: int,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = config.model
        self._reasoning_effort = config.reasoning_effort or "max"
        self._max_output_tokens = max_output_tokens
        self._provider = OpenCodeGoModelProvider(
            config,
            proxy_url=proxy_url,
            client=client,
        )

    async def narrate(self, request: TradeRetroNarrativeRequest) -> TradeRetroNarrativeResponse:
        base_messages: tuple[dict[str, str], ...] = (
            {"role": "system", "content": _TRADE_RETRO_SYSTEM_PROMPT},
            {"role": "user", "content": request.deterministic_facts_json},
        )
        messages = base_messages
        validation_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._provider.complete(
                    ModelRequest(
                        messages=messages,
                        model=self.model,
                        reasoning_mode="thinking",
                        reasoning_effort=self._reasoning_effort,
                        max_output_tokens=self._max_output_tokens,
                        native_web_search=False,
                    )
                )
            except ProviderUnavailableError:
                if attempt == 0:
                    continue
                raise
            try:
                result = _TradeRetroResponse.model_validate_json(_strip_json_fence(response.text))
            except (TypeError, ValueError, ValidationError) as error:
                validation_error = error
                if attempt == 0:
                    messages = (
                        *base_messages,
                        {
                            "role": "user",
                            "content": _TRADE_RETRO_STRUCTURE_REPAIR_PROMPT,
                        },
                    )
                    continue
                break
            return TradeRetroNarrativeResponse(
                summary_markdown=result.summary_markdown,
                provider_name=self.provider_name,
                model=self.model,
            )
        raise DataContractError(
            "OpenCode Go returned an invalid Trade Retro narrative"
        ) from validation_error

    async def aclose(self) -> None:
        await self._provider.aclose()


class OpenCodeZenModelProvider(OpenCodeGoModelProvider):
    """OpenCode Zen endpoint with an independent key and model directory."""

    provider_name = "opencode_zen"
    display_name = "OpenCode Zen"
    responses_models = _ZEN_RESPONSES_MODELS

    @staticmethod
    def _model(request: ModelRequest, fallback: str) -> str:
        model = request.model or fallback
        if not isinstance(model, str) or _SAFE_MODEL_ID.fullmatch(model) is None:
            raise DataContractError("OpenCode Zen model selection is unavailable")
        return model


class OpenCodeZenMonitorJudgmentProvider(OpenCodeGoMonitorJudgmentProvider):
    provider_name = "opencode_zen"
    display_name = "OpenCode Zen"
    model_provider_type = OpenCodeZenModelProvider


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return stripped[7:-3].strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped[3:-3].strip()
    return stripped


__all__ = [
    "OpenCodeGoModelProvider",
    "OpenCodeGoMonitorJudgmentProvider",
    "OpenCodeGoTradeRetroNarrativeProvider",
    "OpenCodeZenModelProvider",
    "OpenCodeZenMonitorJudgmentProvider",
]
