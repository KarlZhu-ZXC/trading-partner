"""Provider-neutral HTTP adapter for Chat Completions and Responses."""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import Any

import httpx

from application.ports.agent_model_provider import AgentModelProvider, ModelRequest, ModelResponse
from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from infrastructure.config.llm import LLMEndpointConfig
from infrastructure.providers.llm.chat_completions_codec import ChatCompletionsCodec
from infrastructure.providers.llm.responses_codec import ResponsesCodec


class OpenAICompatibleModelProvider(AgentModelProvider):
    """Small, bounded HTTP client shared by Console and Telegram Agent turns."""

    provider_name = "openai_compatible"

    def __init__(
        self,
        config: LLMEndpointConfig | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_style: str = "chat_completions",
        reasoning_mode: str = "none",
        reasoning_effort: str | None = None,
        native_web_search: str = "disabled",
        native_web_extractor: str = "disabled",
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 8000,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if config is None:
            config = LLMEndpointConfig(
                api_style=api_style,  # type: ignore[arg-type]
                base_url=base_url or "",
                api_key=api_key or "",
                model=model or "",
                reasoning_mode=reasoning_mode,  # type: ignore[arg-type]
                reasoning_effort=reasoning_effort,
                native_web_search=native_web_search,  # type: ignore[arg-type]
                native_web_extractor=native_web_extractor,  # type: ignore[arg-type]
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
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

    async def complete(self, request: ModelRequest) -> ModelResponse:
        effective = replace(
            request,
            model=request.model or self.config.model,
            reasoning_mode=(
                request.reasoning_mode
                if request.reasoning_mode != "none"
                else self.config.reasoning_mode
            ),
            reasoning_effort=request.reasoning_effort or self.config.reasoning_effort,
            max_output_tokens=request.max_output_tokens or self.config.max_output_tokens,
            native_web_search=(
                request.native_web_search
                and self.config.native_web_search == "responses_web_search"
            ),
        )
        if self.config.api_style == "responses":
            payload = ResponsesCodec.encode(
                effective,
                model=self.config.model,
                max_output_tokens=self.config.max_output_tokens,
                native_web_search=self.config.native_web_search == "responses_web_search",
                native_web_extractor=(
                    self.config.native_web_extractor == "responses_web_extractor"
                ),
            )
            path = "/responses"
        else:
            payload = ChatCompletionsCodec.encode(
                effective,
                model=self.config.model,
                max_output_tokens=self.config.max_output_tokens,
            )
            path = "/chat/completions"
        started = perf_counter()
        raw = await self._post(path, payload)
        if self.config.api_style == "responses":
            result = ResponsesCodec.decode(raw)
        else:
            result = ChatCompletionsCodec.decode(raw)
        if result.model is None:
            result = replace(result, model=self.config.model)
        return replace(result, latency_ms=max(0, round((perf_counter() - started) * 1000)))

    async def _post(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        for attempt in range(2):
            try:
                response = await self._client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    continue
                raise ProviderTimeoutError(
                    "LLM endpoint timed out",
                    details={"attempts": 2},
                ) from exc
            except httpx.HTTPError as exc:
                # Never include the exception text: httpx may include a URL,
                # request headers, or provider response snippets.
                raise ProviderUnavailableError(
                    "LLM endpoint transport failed",
                    details={"error_type": type(exc).__name__},
                ) from exc

            status = response.status_code
            if status in {401, 403}:
                raise ProviderAuthenticationError(
                    "LLM endpoint authentication failed",
                    details={"status_code": status},
                )
            if status == 429:
                if attempt == 0:
                    continue
                raise ProviderRateLimitError(
                    "LLM endpoint was rate limited",
                    details={"status_code": status, "attempts": 2},
                )
            if status >= 400:
                raise ProviderUnavailableError(
                    "LLM endpoint returned an HTTP error",
                    details={"status_code": status},
                )
            try:
                value = response.json()
            except (TypeError, ValueError) as exc:
                raise DataContractError("LLM endpoint returned non-JSON data") from exc
            if not isinstance(value, dict):
                raise DataContractError("LLM endpoint returned a non-object JSON response")
            return value
        # The loop always returns or raises.  Keep a typed fallback for static
        # analyzers if a future edit changes the retry bounds.
        raise ProviderUnavailableError("LLM endpoint request did not complete")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# Naming aliases for callers which spell out the Agent boundary.
OpenAICompatibleAgentModelProvider = OpenAICompatibleModelProvider


__all__ = ["OpenAICompatibleAgentModelProvider", "OpenAICompatibleModelProvider"]
