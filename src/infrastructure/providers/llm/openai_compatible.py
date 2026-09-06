"""Provider-neutral HTTP adapter for Chat Completions and Responses."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic, perf_counter
from typing import Any

import httpx

from application.ports.agent_model_provider import (
    AgentModelProvider,
    ModelCatalog,
    ModelCatalogItem,
    ModelReasoningEffort,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
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
from infrastructure.providers.llm.chat_completions_codec import ChatCompletionsCodec
from infrastructure.providers.llm.responses_codec import ResponsesCodec


def opaque_model_session_id(value: str) -> str:
    """Return a stable, header-safe correlation ID without exposing domain IDs."""

    normalized = value.strip()
    if not normalized:
        raise DataContractError("Model session identifier is empty")
    return f"tp-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


class OpenAICompatibleModelProvider(AgentModelProvider):
    """Small, bounded HTTP client shared by Console and Telegram Agent turns."""

    provider_name = "openai_compatible"
    _MODEL_CATALOG_TTL_SECONDS = 300.0
    _MAX_CATALOG_MODELS = 200
    _SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
    _NON_AGENT_MODEL_MARKERS = (
        "audio",
        "embedding",
        "image",
        "moderation",
        "realtime",
        "rerank",
        "speech",
        "transcribe",
        "tts",
    )
    _REASONING_EFFORTS: tuple[ModelReasoningEffort, ...] = (
        "low",
        "medium",
        "high",
        "max",
    )

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
        session_header_name: str | None = None,
        default_session_id: str | None = None,
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
        self._model_catalog: ModelCatalog | None = None
        self._model_catalog_expires_at = 0.0
        self._model_catalog_lock = asyncio.Lock()
        self._session_header_name = session_header_name
        self._default_session_id = default_session_id

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
                model=effective.model or self.config.model,
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
                model=effective.model or self.config.model,
                max_output_tokens=self.config.max_output_tokens,
            )
            path = "/chat/completions"
        started = perf_counter()
        raw = await self._post(path, payload, request=effective)
        if self.config.api_style == "responses":
            result = ResponsesCodec.decode(raw)
        else:
            result = ChatCompletionsCodec.decode(raw)
        if result.model is None:
            result = replace(result, model=effective.model or self.config.model)
        return replace(result, latency_ms=max(0, round((perf_counter() - started) * 1000)))

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        """Yield normalized SSE deltas with one pre-content retry budget.

        The retry is intentionally owned here rather than by the runtime: once
        any token/tool delta has been yielded, replaying the request could
        duplicate visible text or execute a tool twice.
        """

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
                model=effective.model or self.config.model,
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
                model=effective.model or self.config.model,
                max_output_tokens=self.config.max_output_tokens,
            )
            path = "/chat/completions"
        payload["stream"] = True
        url = f"{self.config.base_url.rstrip('/')}{path}"
        started = perf_counter()
        emitted_content = False
        for attempt in range(2):
            try:
                async with self._client.stream(
                    "POST",
                    url,
                    headers=self._request_headers(effective),
                    json=payload,
                ) as response:
                    status = response.status_code
                    if status in {401, 403}:
                        raise ProviderAuthenticationError(
                            "LLM endpoint authentication failed",
                            details={"status_code": status},
                        )
                    if status == 429:
                        raise ProviderRateLimitError(
                            "LLM endpoint was rate limited",
                            details={"status_code": status},
                        )
                    if 400 <= status < 500:
                        raise ProviderRequestRejectedError(
                            "LLM endpoint rejected the request",
                            details={"status_code": status},
                        )
                    if status >= 400:
                        raise ProviderUnavailableError(
                            "LLM endpoint returned an HTTP error",
                            details={"status_code": status},
                        )
                    request_id = response.headers.get("x-request-id") or response.headers.get(
                        "request-id"
                    )
                    event_name: str | None = None
                    data_lines: list[str] = []
                    stream_event_seen = False
                    # Responses argument-delta events identify a function item
                    # by ``item_id`` while the following
                    # ``function_call_output`` must use its distinct
                    # ``call_id``.  Keep that protocol-local mapping inside one
                    # stream so the runtime sees one stable tool-call identity.
                    response_item_call_ids: dict[str, str] = {}

                    def decode_buffer(
                        buffered: list[str],
                        current_event_name: str | None,
                        response_request_id: str | None,
                        item_call_ids: dict[str, str] = response_item_call_ids,
                    ) -> ModelStreamChunk | None:
                        if not buffered:
                            return None
                        raw = "\n".join(buffered).strip()
                        buffered.clear()
                        if raw == "[DONE]":
                            return ModelStreamChunk(done=True, request_id=response_request_id)
                        try:
                            value = json.loads(raw)
                        except (TypeError, ValueError) as exc:
                            raise DataContractError("LLM stream event is not valid JSON") from exc
                        if not isinstance(value, dict):
                            raise DataContractError("LLM stream event is not an object")
                        if self.config.api_style == "responses":
                            raw_item = value.get("item")
                            if isinstance(raw_item, dict):
                                item_id = raw_item.get("id")
                                call_id = raw_item.get("call_id")
                                if (
                                    isinstance(item_id, str)
                                    and item_id
                                    and isinstance(call_id, str)
                                    and call_id
                                ):
                                    item_call_ids[item_id] = call_id
                            chunk = ResponsesCodec.decode_stream_event(
                                value,
                                event_name=current_event_name,
                            )
                            item_id = value.get("item_id")
                            stable_call_id = (
                                item_call_ids.get(item_id)
                                if isinstance(item_id, str)
                                else None
                            )
                            if stable_call_id is not None and chunk.tool_calls:
                                chunk = replace(
                                    chunk,
                                    tool_calls=tuple(
                                        replace(call, id=stable_call_id)
                                        for call in chunk.tool_calls
                                    ),
                                )
                        else:
                            chunk = ChatCompletionsCodec.decode_stream_event(value)
                        if response_request_id is not None and chunk.request_id is None:
                            chunk = replace(chunk, request_id=response_request_id)
                        return chunk

                    async for line in response.aiter_lines():
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event_name = line[6:].strip() or None
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                            continue
                        if line == "":
                            chunk = decode_buffer(data_lines, event_name, request_id)
                            if chunk is None:
                                event_name = None
                                continue
                            stream_event_seen = True
                            if chunk.latency_ms is None:
                                chunk = replace(
                                    chunk,
                                    latency_ms=max(0, round((perf_counter() - started) * 1000)),
                                )
                            if (
                                chunk.text_delta
                                or chunk.tool_calls
                                or chunk.final_response is not None
                                or chunk.web_search_used
                                or chunk.web_extractor_used
                                or chunk.web_source_urls
                            ):
                                emitted_content = True
                            yield chunk
                            event_name = None
                    chunk = decode_buffer(data_lines, event_name, request_id)
                    if chunk is not None:
                        stream_event_seen = True
                        if chunk.latency_ms is None:
                            chunk = replace(
                                chunk,
                                latency_ms=max(0, round((perf_counter() - started) * 1000)),
                            )
                        if (
                            chunk.text_delta
                            or chunk.tool_calls
                            or chunk.final_response is not None
                            or chunk.web_search_used
                            or chunk.web_extractor_used
                            or chunk.web_source_urls
                        ):
                            emitted_content = True
                        yield chunk
                    if not stream_event_seen:
                        # A few OpenAI-compatible servers silently ignore the
                        # ``stream`` flag and return one ordinary JSON body.
                        # Reuse the canonical non-stream path rather than
                        # treating an empty SSE parse as a successful answer.
                        fallback_response = await self.complete(effective)
                        yield ModelStreamChunk(
                            final_response=fallback_response,
                            model=fallback_response.model,
                            request_id=fallback_response.request_id or request_id,
                            latency_ms=fallback_response.latency_ms,
                            done=True,
                        )
                return
            except ProviderUnavailableError as exc:
                raise exc
            except (ProviderTimeoutError, ProviderRateLimitError) as exc:
                if attempt == 0 and not emitted_content:
                    continue
                raise exc
            except httpx.TimeoutException as exc:
                if attempt == 0 and not emitted_content:
                    continue
                raise ProviderTimeoutError(
                    "LLM endpoint timed out",
                    details={"attempts": attempt + 1},
                ) from exc
            except httpx.HTTPError as exc:
                if attempt == 0 and not emitted_content:
                    continue
                raise ProviderUnavailableError(
                    "LLM endpoint transport failed",
                    details={"error_type": type(exc).__name__},
                ) from exc

    async def list_models(self, *, force_refresh: bool = False) -> ModelCatalog:
        """Fetch and cache the standard OpenAI-compatible ``/models`` directory."""

        now = monotonic()
        if (
            not force_refresh
            and self._model_catalog is not None
            and now < self._model_catalog_expires_at
        ):
            return replace(self._model_catalog, cached=True)
        async with self._model_catalog_lock:
            now = monotonic()
            if (
                not force_refresh
                and self._model_catalog is not None
                and now < self._model_catalog_expires_at
            ):
                return replace(self._model_catalog, cached=True)
            raw = await self._request_json("GET", "/models")
            candidates = raw.get("data", raw.get("models"))
            if not isinstance(candidates, list):
                raise DataContractError("LLM model directory returned no model list")

            items: list[ModelCatalogItem] = []
            seen: set[str] = set()

            def add(model_id: object, raw_item: object = None) -> None:
                if (
                    not isinstance(model_id, str)
                    or self._SAFE_MODEL_ID.fullmatch(model_id) is None
                    or any(
                        marker in model_id.casefold()
                        for marker in self._NON_AGENT_MODEL_MARKERS
                    )
                    or model_id in seen
                    or len(items) >= self._MAX_CATALOG_MODELS
                ):
                    return
                efforts: tuple[ModelReasoningEffort, ...] = ()
                if isinstance(raw_item, dict):
                    raw_efforts = raw_item.get(
                        "reasoning_efforts",
                        raw_item.get("supported_reasoning_efforts"),
                    )
                    if isinstance(raw_efforts, list):
                        efforts = tuple(
                            effort
                            for effort in self._REASONING_EFFORTS
                            if effort in raw_efforts
                        )
                seen.add(model_id)
                items.append(ModelCatalogItem(id=model_id, reasoning_efforts=efforts))

            # Keep the configured model available and first even when a Provider
            # omits aliases from its directory response.
            add(self.config.model)
            for candidate in candidates:
                if isinstance(candidate, str):
                    add(candidate)
                elif isinstance(candidate, dict):
                    add(candidate.get("id"), candidate)

            catalog = ModelCatalog(
                models=tuple(items),
                fetched_at=datetime.now(UTC),
            )
            self._model_catalog = catalog
            self._model_catalog_expires_at = monotonic() + self._MODEL_CATALOG_TTL_SECONDS
            return catalog

    async def _post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        request: ModelRequest | None = None,
    ) -> dict[str, Any]:
        return await self._request_json("POST", path, payload, request=request)

    def _request_headers(self, request: ModelRequest | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if self._session_header_name is not None:
            session_id = request.session_id if request is not None else self._default_session_id
            if session_id is None:
                raise DataContractError("Model request is missing a session identifier")
            headers[self._session_header_name] = opaque_model_session_id(session_id)
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        request: ModelRequest | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        for attempt in range(2):
            try:
                headers = self._request_headers(request)
                if payload is None:
                    response = await self._client.request(method, url, headers=headers)
                else:
                    response = await self._client.request(
                        method,
                        url,
                        headers=headers,
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
            if 400 <= status < 500:
                raise ProviderRequestRejectedError(
                    "LLM endpoint rejected the request",
                    details={"status_code": status},
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
