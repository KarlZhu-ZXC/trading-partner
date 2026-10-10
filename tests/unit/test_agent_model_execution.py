from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from application.dto.agent import AgentTurnEvent
from application.ports.agent_model_provider import ModelRequest, ModelResponse, ModelStreamChunk
from application.services.agent_model_execution import AgentModelExecution
from application.services.copilot_research_budget import ResearchBudget, ResearchBudgetExhausted
from domain.common.errors import ProviderTimeoutError


class StreamingProvider:
    def __init__(
        self,
        chunks: tuple[ModelStreamChunk, ...] = (),
        *,
        stream_error: BaseException | None = None,
        complete_response: ModelResponse | None = None,
    ) -> None:
        self.chunks = chunks
        self.stream_error = stream_error
        self.complete_response = complete_response or ModelResponse(text="complete")
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        _ = request
        self.complete_calls += 1
        return self.complete_response

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        _ = request
        self.stream_calls += 1
        for chunk in self.chunks:
            yield chunk
        if self.stream_error is not None:
            raise self.stream_error

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_stream_success_assembles_text_and_emits_each_delta_once() -> None:
    provider = StreamingProvider(
        (
            ModelStreamChunk(text_delta="Hel"),
            ModelStreamChunk(
                text_delta="lo",
                model="stream-model",
                final_response=ModelResponse(text="Hello", request_id="request-1"),
            ),
        )
    )
    events: list[AgentTurnEvent] = []

    response = await AgentModelExecution().execute(
        model_provider=provider,
        request=ModelRequest(),
        event_emitter=events.append,
        cancellation_check=None,
        research_budget=None,
        conversation_id="conversation-1",
        turn_id="turn-1",
    )

    assert response.text == "Hello"
    assert response.model == "stream-model"
    assert response.request_id == "request-1"
    assert [event.data["text"] for event in events] == ["Hel", "lo"]
    assert provider.complete_calls == 0


@pytest.mark.asyncio
async def test_partial_stream_timeout_marks_visible_output_and_does_not_replay() -> None:
    provider = StreamingProvider(
        (ModelStreamChunk(text_delta="partial"),),
        stream_error=ProviderTimeoutError("upstream timed out"),
    )
    events: list[AgentTurnEvent] = []

    with pytest.raises(ProviderTimeoutError) as raised:
        await AgentModelExecution().execute(
            model_provider=provider,
            request=ModelRequest(),
            event_emitter=events.append,
            cancellation_check=None,
            research_budget=None,
            conversation_id="conversation-1",
            turn_id="turn-1",
        )

    assert raised.value.__dict__["agent_stream_emitted"] is True
    assert [event.data["text"] for event in events] == ["partial"]
    assert provider.complete_calls == 0


@pytest.mark.asyncio
async def test_stream_not_implemented_before_first_event_falls_back_to_complete() -> None:
    provider = StreamingProvider(
        stream_error=NotImplementedError(),
        complete_response=ModelResponse(text="fallback"),
    )
    events: list[AgentTurnEvent] = []

    response = await AgentModelExecution().execute(
        model_provider=provider,
        request=ModelRequest(),
        event_emitter=events.append,
        cancellation_check=None,
        research_budget=None,
        conversation_id="conversation-1",
        turn_id="turn-1",
    )

    assert response.text == "fallback"
    assert [event.data["text"] for event in events] == ["fallback"]
    assert provider.complete_calls == 1


@pytest.mark.asyncio
async def test_cancellation_check_stops_stream_before_replaying_next_chunk() -> None:
    provider = StreamingProvider(
        (ModelStreamChunk(text_delta="first"), ModelStreamChunk(text_delta="second"))
    )
    events: list[AgentTurnEvent] = []
    checks = 0

    def cancellation_check() -> None:
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await AgentModelExecution().execute(
            model_provider=provider,
            request=ModelRequest(),
            event_emitter=events.append,
            cancellation_check=cancellation_check,
            research_budget=None,
            conversation_id="conversation-1",
            turn_id="turn-1",
        )

    assert [event.data["text"] for event in events] == ["first"]


@pytest.mark.asyncio
async def test_research_budget_counts_model_attempt_before_complete() -> None:
    provider = StreamingProvider(complete_response=ModelResponse(text="budgeted"))
    budget = ResearchBudget(
        mode="research",
        max_seconds=30,
        max_model_calls=1,
        max_tool_calls=1,
    )
    execution = AgentModelExecution()

    response = await execution.execute(
        model_provider=provider,
        request=ModelRequest(),
        event_emitter=None,
        cancellation_check=None,
        research_budget=budget,
        conversation_id="conversation-1",
        turn_id="turn-1",
    )

    assert response.text == "budgeted"
    assert provider.complete_calls == 1
    with pytest.raises(ResearchBudgetExhausted, match="MODEL_BUDGET"):
        await execution.execute(
            model_provider=provider,
            request=ModelRequest(),
            event_emitter=None,
            cancellation_check=None,
            research_budget=budget,
            conversation_id="conversation-1",
            turn_id="turn-1",
        )
    assert provider.complete_calls == 1
