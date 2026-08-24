from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from application.dto.provider_route_history import ProviderRouteReceipt
from application.ports.agent_model_provider import ModelRequest, ModelResponse
from domain.common.enums import DataCategory, Market, ProviderAttemptOutcome, VendorId
from domain.common.errors import ProviderTimeoutError, ProviderUnavailableError
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.llm.routed import (
    LLMResilienceController,
    RoutedAgentModelProvider,
)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 20, 4, 0, tzinfo=UTC)


class IDs:
    def __init__(self) -> None:
        self.value = 0

    def new(self, _prefix: object) -> str:
        self.value += 1
        return f"provider_route_{self.value}"


class RateLimiter:
    async def acquire(self, *args: object, **kwargs: object) -> Any:
        return SimpleNamespace(allowed=True, queued=False)


class History:
    is_durable = True

    def __init__(self) -> None:
        self.values: list[ProviderRouteReceipt] = []

    def append(self, receipt: ProviderRouteReceipt) -> None:
        self.values.append(receipt)


class Provider:
    def __init__(self, responses: list[ModelResponse | BaseException]) -> None:
        self.responses = responses
        self.calls = 0
        self.config = SimpleNamespace(model="test-model")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        _ = request
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def aclose(self) -> None:
        return None


def _wrapper(
    provider: Provider,
    history: History,
    *,
    threshold: int = 5,
    circuit_breaker_enabled: bool = True,
) -> RoutedAgentModelProvider:
    clock = Clock()
    controller = LLMResilienceController(
        rate_limiter=RateLimiter(),  # type: ignore[arg-type]
        circuit_breaker=CircuitBreaker(
            clock,
            failure_threshold=threshold,
            recovery_timeout_seconds=60,
            half_open_max_calls=1,
        ),
        route_history=history,
        clock=clock,
        id_generator=IDs(),
        max_wait_seconds=1,
        circuit_breaker_enabled=circuit_breaker_enabled,
    )
    return RoutedAgentModelProvider(
        provider,
        vendor=VendorId.BAILIAN,
        resilience=controller,
    )


@pytest.mark.asyncio
async def test_llm_wrapper_records_shared_route_receipt() -> None:
    history = History()
    provider = Provider([ModelResponse(text="ok", model="test-model")])
    wrapped = _wrapper(provider, history)

    result = await wrapped.complete(ModelRequest())

    assert result.text == "ok"
    assert len(history.values) == 1
    receipt = history.values[0]
    assert receipt.market is Market.GLOBAL
    assert receipt.category is DataCategory.INTERACTIVE_QA
    assert receipt.selected_vendor is VendorId.BAILIAN
    assert receipt.attempts[0].outcome is ProviderAttemptOutcome.SUCCESS


@pytest.mark.asyncio
async def test_llm_wrapper_opens_shared_circuit_without_persisting_error_text() -> None:
    history = History()
    provider = Provider(
        [
            ProviderTimeoutError("api_key=must-not-persist"),
            ProviderTimeoutError("authorization=must-not-persist"),
        ]
    )
    wrapped = _wrapper(provider, history, threshold=2)

    with pytest.raises(ProviderTimeoutError):
        await wrapped.complete(ModelRequest())
    with pytest.raises(ProviderTimeoutError):
        await wrapped.complete(ModelRequest())
    with pytest.raises(ProviderUnavailableError):
        await wrapped.complete(ModelRequest())

    assert provider.calls == 2
    assert history.values[-1].attempts[0].outcome is ProviderAttemptOutcome.SKIPPED_CIRCUIT_OPEN
    assert "must-not-persist" not in repr(history.values)


@pytest.mark.asyncio
async def test_llm_wrapper_respects_disabled_shared_circuit() -> None:
    history = History()
    provider = Provider(
        [
            ProviderTimeoutError("first"),
            ProviderTimeoutError("second"),
            ModelResponse(text="ok", model="test-model"),
        ]
    )
    wrapped = _wrapper(
        provider,
        history,
        threshold=1,
        circuit_breaker_enabled=False,
    )

    with pytest.raises(ProviderTimeoutError):
        await wrapped.complete(ModelRequest())
    with pytest.raises(ProviderTimeoutError):
        await wrapped.complete(ModelRequest())
    assert (await wrapped.complete(ModelRequest())).text == "ok"
    assert provider.calls == 3
