"""Execute one provider-neutral Agent model request.

This collaborator owns only the provider response boundary: optional streaming,
complete-response fallback, bounded response assembly, and incremental event
delivery.  Durable conversation state, routing/retry policy, and turn lifecycle
remain responsibilities of :mod:`agent_runtime_service`.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import replace
from typing import Any, cast

from application.dto.agent import AgentTurnEvent
from application.ports.agent_model_provider import (
    AgentModelProvider,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelToolCall,
    ModelUsage,
)
from application.services.copilot_research_budget import ResearchBudget
from domain.common.errors import (
    DataContractError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

AgentModelEventEmitter = Callable[[AgentTurnEvent], Awaitable[None] | None]
AgentCancellationCheck = Callable[[], None]


class AgentModelExecution:
    """Assemble one model response without accessing runtime or persistence state."""

    async def execute(
        self,
        *,
        model_provider: AgentModelProvider,
        request: ModelRequest,
        event_emitter: AgentModelEventEmitter | None,
        cancellation_check: AgentCancellationCheck | None,
        research_budget: ResearchBudget | None,
        conversation_id: str,
        turn_id: str | None,
    ) -> ModelResponse:
        """Complete ``request`` using streaming when supported by the provider.

        ``agent_stream_emitted`` is attached to retryable provider failures so
        the runtime can preserve its existing rule: auto fallback is allowed
        only when no visible stream content has reached the caller.
        """

        if research_budget is not None:
            research_budget.model()
            return await research_budget.wait(lambda: model_provider.complete(request))

        stream_method = getattr(model_provider, "stream", None)
        if not callable(stream_method):
            return await self._complete_without_stream(
                model_provider=model_provider,
                request=request,
                event_emitter=event_emitter,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )

        text_parts: list[str] = []
        tool_calls: dict[str, ModelToolCall] = {}
        latest_usage: ModelUsage | None = None
        model: str | None = None
        finish_reason: str | None = None
        web_search_used = False
        web_extractor_used = False
        source_urls: list[str] = []
        request_id: str | None = None
        latest_latency_ms: int | None = None
        final_response: ModelResponse | None = None
        saw_event = False
        streamed_content = False
        try:
            stream_result = stream_method(request)
            if inspect.isawaitable(stream_result):
                stream_result = await stream_result
            async for chunk in self._as_async_iterable(stream_result):
                if cancellation_check is not None:
                    cancellation_check()
                if isinstance(chunk, ModelResponse):
                    final_response = chunk
                    saw_event = True
                    continue
                if not isinstance(chunk, ModelStreamChunk):
                    raise DataContractError("Agent model stream returned an invalid chunk")
                saw_event = saw_event or bool(
                    chunk.text_delta
                    or chunk.tool_calls
                    or chunk.final_response is not None
                    or chunk.web_search_used
                    or chunk.web_extractor_used
                    or chunk.web_source_urls
                    or chunk.done
                )
                if chunk.text_delta:
                    streamed_content = True
                    text_parts.append(chunk.text_delta)
                    await self._emit_event(
                        event_emitter,
                        AgentTurnEvent(
                            type="text_delta",
                            data={
                                "conversation_id": conversation_id,
                                "turn_id": turn_id,
                                "text": chunk.text_delta,
                                "delta": chunk.text_delta,
                            },
                        ),
                    )
                for call in chunk.tool_calls:
                    streamed_content = True
                    existing = tool_calls.get(call.id)
                    if existing is None and len(tool_calls) == 1 and call.id.startswith("call_"):
                        # Chat/Responses continuations may expose an array
                        # index instead of the stable provider call id.
                        existing = next(iter(tool_calls.values()))
                        call = replace(call, id=existing.id)
                    if existing is None:
                        tool_calls[call.id] = call
                    else:
                        tool_calls[call.id] = ModelToolCall(
                            id=call.id,
                            name=call.name or existing.name,
                            arguments=existing.arguments + call.arguments,
                        )
                latest_usage = chunk.usage or latest_usage
                model = chunk.model or model
                finish_reason = chunk.finish_reason or finish_reason
                if chunk.latency_ms is not None:
                    latest_latency_ms = chunk.latency_ms
                web_search_used = web_search_used or chunk.web_search_used
                web_extractor_used = web_extractor_used or chunk.web_extractor_used
                if chunk.web_search_used or chunk.web_extractor_used or chunk.web_source_urls:
                    streamed_content = True
                for url in chunk.web_source_urls:
                    if url not in source_urls and len(source_urls) < 20:
                        source_urls.append(url)
                request_id = chunk.request_id or request_id
                if chunk.final_response is not None:
                    streamed_content = True
                    final_response = chunk.final_response
        except (
            ProviderTimeoutError,
            ProviderRateLimitError,
            ProviderUnavailableError,
        ) as error:
            # The runtime may retry only before the first visible delta.
            error.__dict__["agent_stream_emitted"] = streamed_content
            raise
        except NotImplementedError:
            if saw_event:
                raise
            return await self._complete_without_stream(
                model_provider=model_provider,
                request=request,
                event_emitter=event_emitter,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )

        if final_response is not None:
            # A Responses ``completed`` event may repeat text already emitted
            # as deltas.  Keep the visible text exactly once.
            text = "".join(text_parts) if text_parts else final_response.text
            if not text_parts and final_response.text:
                await self._emit_event(
                    event_emitter,
                    AgentTurnEvent(
                        type="text_delta",
                        data={
                            "conversation_id": conversation_id,
                            "turn_id": turn_id,
                            "text": final_response.text,
                            "delta": final_response.text,
                        },
                    ),
                )
            merged_calls = tuple(tool_calls.values()) or final_response.tool_calls
            return replace(
                final_response,
                text=text,
                tool_calls=merged_calls,
                usage=final_response.usage or latest_usage,
                model=final_response.model or model,
                finish_reason=final_response.finish_reason or finish_reason,
                latency_ms=(
                    final_response.latency_ms
                    if final_response.latency_ms is not None
                    else latest_latency_ms
                ),
                web_search_used=final_response.web_search_used or web_search_used,
                web_extractor_used=final_response.web_extractor_used or web_extractor_used,
                web_source_urls=tuple(
                    dict.fromkeys((*final_response.web_source_urls, *source_urls))
                )[:20],
                request_id=final_response.request_id or request_id,
            )
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls.values()),
            usage=latest_usage,
            model=model,
            finish_reason=finish_reason,
            web_search_used=web_search_used,
            web_extractor_used=web_extractor_used,
            web_source_urls=tuple(source_urls),
            request_id=request_id,
            latency_ms=latest_latency_ms,
        )

    async def _complete_without_stream(
        self,
        *,
        model_provider: AgentModelProvider,
        request: ModelRequest,
        event_emitter: AgentModelEventEmitter | None,
        conversation_id: str,
        turn_id: str | None,
    ) -> ModelResponse:
        response = await model_provider.complete(request)
        if response.text:
            await self._emit_event(
                event_emitter,
                AgentTurnEvent(
                    type="text_delta",
                    data={
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                        "text": response.text,
                        "delta": response.text,
                    },
                ),
            )
        return response

    @staticmethod
    async def _emit_event(
        event_emitter: AgentModelEventEmitter | None,
        event: AgentTurnEvent,
    ) -> None:
        """Best-effort event delivery; observability must not break a turn."""

        if event_emitter is None:
            return
        try:
            result = event_emitter(event)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - event consumers are non-critical
            return

    @staticmethod
    def _as_async_iterable(value: Any) -> AsyncIterable[ModelResponse | ModelStreamChunk]:
        """Narrow a provider's optional stream result for typed iteration."""

        return cast(AsyncIterable[ModelResponse | ModelStreamChunk], value)


__all__ = [
    "AgentCancellationCheck",
    "AgentModelEventEmitter",
    "AgentModelExecution",
]
