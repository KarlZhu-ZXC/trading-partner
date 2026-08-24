"""Shared admission, circuit, and route receipts for server-side LLM calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from application.dto.provider_resilience import CircuitCallPermit
from application.dto.provider_route_history import ProviderRouteReceipt
from application.dto.provider_routing import ProviderAttemptRecord
from application.ports.agent_model_provider import (
    AgentModelProvider,
    ModelCatalog,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.provider_route_history_store import ProviderRouteHistoryStore
from domain.common.enums import (
    DataCategory,
    DataCriticality,
    Market,
    ProviderAttemptOutcome,
    SourceRole,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    ProviderAdmissionTimeoutError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from domain.common.ids import EntityIdPrefix
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.common.rate_limiter import ProviderRateLimiter


@dataclass(frozen=True, slots=True)
class LLMCallLease:
    vendor: VendorId
    operation_name: str
    permit: CircuitCallPermit | None
    queued: bool
    started: float


class LLMResilienceController:
    def __init__(
        self,
        *,
        rate_limiter: ProviderRateLimiter,
        circuit_breaker: CircuitBreaker,
        route_history: ProviderRouteHistoryStore,
        clock: Clock,
        id_generator: IdGenerator,
        max_wait_seconds: float,
        circuit_breaker_enabled: bool = True,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._breaker = circuit_breaker
        self._history = route_history
        self._clock = clock
        self._ids = id_generator
        self._max_wait_seconds = max_wait_seconds
        self._circuit_breaker_enabled = circuit_breaker_enabled

    async def acquire(self, vendor: VendorId, operation_name: str) -> LLMCallLease:
        decision = await self._rate_limiter.acquire(
            vendor,
            DataCategory.INTERACTIVE_QA,
            self._max_wait_seconds,
        )
        if not decision.allowed:
            error = ProviderAdmissionTimeoutError(
                "LLM admission budget expired",
                details={"vendor": vendor.value, "category": DataCategory.INTERACTIVE_QA.value},
            )
            self._record(
                vendor=vendor,
                operation_name=operation_name,
                ok=False,
                outcome=ProviderAttemptOutcome.SKIPPED_ADMISSION_TIMEOUT,
                duration_ms=0,
                error_code=error.code,
                queued=False,
            )
            raise error
        permit = None
        if self._circuit_breaker_enabled:
            try:
                permit = self._breaker.before_call(vendor, DataCategory.INTERACTIVE_QA)
            except ProviderUnavailableError as error:
                self._record(
                    vendor=vendor,
                    operation_name=operation_name,
                    ok=False,
                    outcome=ProviderAttemptOutcome.SKIPPED_CIRCUIT_OPEN,
                    duration_ms=0,
                    error_code=error.code,
                    queued=decision.queued,
                )
                raise
        return LLMCallLease(
            vendor=vendor,
            operation_name=operation_name,
            permit=permit,
            queued=decision.queued,
            started=monotonic(),
        )

    def succeed(self, lease: LLMCallLease) -> None:
        if lease.permit is not None:
            self._breaker.record_success(lease.permit)
        self._record(
            vendor=lease.vendor,
            operation_name=lease.operation_name,
            ok=True,
            outcome=ProviderAttemptOutcome.SUCCESS,
            duration_ms=self._duration(lease),
            error_code=None,
            queued=lease.queued,
        )

    def fail(self, lease: LLMCallLease, error: BaseException) -> None:
        retryable_failure = isinstance(
            error,
            (ProviderTimeoutError, ProviderUnavailableError),
        )
        if lease.permit is not None:
            if retryable_failure:
                self._breaker.record_failure(lease.permit)
            else:
                # Authentication, contract, rate-limit, and caller cancellation do
                # not establish endpoint unavailability; release the permit only.
                self._breaker.record_success(lease.permit)
        code = error.code if isinstance(error, TradingPartnerError) else "LLM_UNEXPECTED_ERROR"
        self._record(
            vendor=lease.vendor,
            operation_name=lease.operation_name,
            ok=False,
            outcome=self._outcome(error),
            duration_ms=self._duration(lease),
            error_code=code,
            queued=lease.queued,
        )

    @staticmethod
    def _duration(lease: LLMCallLease) -> int:
        return max(0, round((monotonic() - lease.started) * 1_000))

    @staticmethod
    def _outcome(error: BaseException) -> ProviderAttemptOutcome:
        if isinstance(error, ProviderTimeoutError):
            return ProviderAttemptOutcome.TIMEOUT
        if isinstance(error, ProviderRateLimitError):
            return ProviderAttemptOutcome.RATE_LIMITED
        if isinstance(error, ProviderAuthenticationError):
            return ProviderAttemptOutcome.AUTH_ERROR
        if isinstance(error, DataContractError):
            return ProviderAttemptOutcome.CONTRACT_ERROR
        return ProviderAttemptOutcome.FAILURE

    def _record(
        self,
        *,
        vendor: VendorId,
        operation_name: str,
        ok: bool,
        outcome: ProviderAttemptOutcome,
        duration_ms: int,
        error_code: str | None,
        queued: bool,
    ) -> None:
        try:
            self._history.append(
                ProviderRouteReceipt(
                    route_id=self._ids.new(EntityIdPrefix.PROVIDER_ROUTE),
                    recorded_at=self._clock.now(),
                    market=Market.GLOBAL,
                    category=DataCategory.INTERACTIVE_QA,
                    operation_name=operation_name,
                    instrument_id=None,
                    criticality=DataCriticality.CORE,
                    requested_chain=(vendor,),
                    ok=ok,
                    selected_vendor=vendor if ok else None,
                    selected_role=SourceRole.PRIMARY if ok else None,
                    cache_disposition=None,
                    attempts=(
                        ProviderAttemptRecord(
                            vendor=vendor,
                            outcome=outcome,
                            error_code=error_code,
                            duration_ms=duration_ms,
                            message=None,
                        ),
                    ),
                    warning_codes=("PROVIDER_ADMISSION_QUEUED",) if queued else (),
                    final_error_code=error_code,
                )
            )
        except Exception:
            # Observability persistence must never change an LLM result.
            return


class RoutedAgentModelProvider(AgentModelProvider):
    def __init__(
        self,
        provider: AgentModelProvider,
        *,
        vendor: VendorId,
        resilience: LLMResilienceController,
    ) -> None:
        self._provider = provider
        self._vendor = vendor
        self._resilience = resilience
        for field in ("config", "model", "reasoning_mode", "reasoning_effort"):
            if hasattr(provider, field):
                setattr(self, field, getattr(provider, field))

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await self._execute("llm.complete", lambda: self._provider.complete(request))

    async def _execute[T](self, operation: str, call: Callable[[], Awaitable[T]]) -> T:
        lease = await self._resilience.acquire(self._vendor, operation)
        try:
            value = await call()
        except BaseException as error:
            self._resilience.fail(lease, error)
            raise
        self._resilience.succeed(lease)
        return value

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        lease = await self._resilience.acquire(self._vendor, "llm.stream")
        stream = getattr(self._provider, "stream", None)
        if not callable(stream):
            try:
                response = await self._provider.complete(request)
            except BaseException as error:
                self._resilience.fail(lease, error)
                raise
            self._resilience.succeed(lease)
            yield ModelStreamChunk(final_response=response, done=True)
            return
        try:
            async for chunk in stream(request):
                yield chunk
        except (GeneratorExit, asyncio.CancelledError) as error:
            self._resilience.fail(lease, error)
            raise
        except BaseException as error:
            self._resilience.fail(lease, error)
            raise
        self._resilience.succeed(lease)

    async def list_models(self, *, force_refresh: bool = False) -> ModelCatalog:
        method = getattr(self._provider, "list_models", None)
        if not callable(method):
            raise ProviderUnavailableError("LLM model catalog is unavailable")
        return await self._execute(
            "llm.models",
            lambda: method(force_refresh=force_refresh),
        )

    async def aclose(self) -> None:
        await self._provider.aclose()


__all__ = ["LLMResilienceController", "RoutedAgentModelProvider"]
