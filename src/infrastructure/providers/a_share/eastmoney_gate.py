"""Process-wide Eastmoney request gate (Phase 1E E2 / §1.3).

Global concurrency exactly 1 plus minimum start-to-start interval with bounded
injected jitter. Production code must use ``get_production_eastmoney_request_gate``
so the process owns a single gate instance. Tests use
``create_isolated_eastmoney_request_gate_for_tests`` only.

Server ownership: the MCP / application server runs on a single asyncio event
loop. The gate binds to the first loop that acquires it and fails closed if a
different loop attempts concurrent use (asyncio.Lock is loop-bound and must not
be reused across loops).
"""

from __future__ import annotations

import asyncio
import random as random_mod
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Final, TypeVar

from domain.common.errors import ConfigurationError, ProviderUnavailableError

T = TypeVar("T")


class EastmoneyRequestGate:
    """Serialize all Eastmoney-family HTTP operations in-process.

    ``run`` acquires the gate lock, waits until the minimum interval since the
    previous operation *start* (plus jitter), then starts the operation.
    Cancellation while waiting releases the lock without updating
    ``last_start``. Cancellation during the operation still records the start
    time so the next caller observes the interval.
    """

    def __init__(
        self,
        *,
        min_interval_seconds: float = 1.0,
        jitter_seconds: float = 0.25,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        random_func: Callable[[], float] | None = None,
        _allow_direct_construction: bool = False,
    ) -> None:
        # Production construction path is get_production_eastmoney_request_gate.
        # Direct construction is reserved for the production factory and the
        # explicitly named test-only factory (both pass the flag).
        if not _allow_direct_construction:
            raise ConfigurationError(
                "EastmoneyRequestGate must be obtained via "
                "get_production_eastmoney_request_gate or "
                "create_isolated_eastmoney_request_gate_for_tests",
                details={
                    "field": "EastmoneyRequestGate",
                    "rule": "construction_path",
                },
            )
        if (
            not isinstance(min_interval_seconds, (int, float))
            or isinstance(min_interval_seconds, bool)
            or min_interval_seconds <= 0
        ):
            raise ConfigurationError(
                "min_interval_seconds must be > 0",
                details={"field": "min_interval_seconds", "rule": "positive"},
            )
        if (
            not isinstance(jitter_seconds, (int, float))
            or isinstance(jitter_seconds, bool)
            or jitter_seconds < 0
        ):
            raise ConfigurationError(
                "jitter_seconds must be >= 0",
                details={"field": "jitter_seconds", "rule": "nonnegative"},
            )
        self._min_interval = float(min_interval_seconds)
        self._jitter = float(jitter_seconds)
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._random = random_func if random_func is not None else random_mod.random
        self._lock = asyncio.Lock()
        self._last_start: float | None = None
        self._in_flight = 0
        self._max_observed_in_flight = 0
        self._bound_loop: asyncio.AbstractEventLoop | None = None

    @property
    def min_interval_seconds(self) -> float:
        return self._min_interval

    @property
    def jitter_seconds(self) -> float:
        return self._jitter

    @property
    def max_observed_in_flight(self) -> int:
        """Peak concurrent operations observed (must be <= 1)."""
        return self._max_observed_in_flight

    @property
    def last_start(self) -> float | None:
        """Monotonic timestamp of the last operation start (test/observe)."""
        return self._last_start

    def _bind_or_check_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise ProviderUnavailableError(
                "EastmoneyRequestGate requires a running event loop",
                details={
                    "error_type": "wrong_event_loop",
                    "status_class": "none",
                },
            ) from exc
        if self._bound_loop is None:
            self._bound_loop = loop
            return
        if self._bound_loop is not loop:
            raise ProviderUnavailableError(
                "EastmoneyRequestGate is bound to a different event loop; "
                "server must own a single event loop for Eastmoney requests",
                details={
                    "error_type": "wrong_event_loop",
                    "status_class": "none",
                },
            )

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        if not callable(operation):
            raise ConfigurationError(
                "operation must be callable",
                details={"field": "operation", "rule": "callable"},
            )
        self._bind_or_check_loop()
        async with self._lock:
            await self._wait_for_interval()
            start = self._clock()
            self._last_start = start
            self._in_flight += 1
            if self._in_flight > self._max_observed_in_flight:
                self._max_observed_in_flight = self._in_flight
            try:
                return await operation()
            finally:
                self._in_flight -= 1

    async def _wait_for_interval(self) -> None:
        if self._last_start is None:
            return
        elapsed = self._clock() - self._last_start
        jitter = 0.0
        if self._jitter > 0:
            sample = self._random()
            if not isinstance(sample, (int, float)) or isinstance(sample, bool):
                sample = 0.0
            sample_f = float(sample)
            if sample_f < 0.0:
                sample_f = 0.0
            if sample_f > 1.0:
                sample_f = 1.0
            jitter = sample_f * self._jitter
        required = self._min_interval + jitter
        remaining = required - elapsed
        if remaining > 0:
            await self._sleep(remaining)


_PRODUCTION_INIT_LOCK: Final[threading.Lock] = threading.Lock()
_PRODUCTION_GATE: EastmoneyRequestGate | None = None


def get_production_eastmoney_request_gate(
    *,
    min_interval_seconds: float = 1.0,
    jitter_seconds: float = 0.25,
) -> EastmoneyRequestGate:
    """Return the process-wide production Eastmoney gate (singleton).

    First call constructs the gate with the given interval/jitter. Subsequent
    calls must pass the same interval/jitter or raise ``ConfigurationError`` —
    silent configuration drift is not allowed.
    """
    global _PRODUCTION_GATE
    if _PRODUCTION_GATE is not None:
        if (
            _PRODUCTION_GATE.min_interval_seconds != float(min_interval_seconds)
            or _PRODUCTION_GATE.jitter_seconds != float(jitter_seconds)
        ):
            raise ConfigurationError(
                "production EastmoneyRequestGate already initialized with "
                "different min_interval_seconds/jitter_seconds",
                details={
                    "field": "EastmoneyRequestGate",
                    "rule": "production_config_drift",
                    "existing_min_interval_seconds": (
                        _PRODUCTION_GATE.min_interval_seconds
                    ),
                    "existing_jitter_seconds": _PRODUCTION_GATE.jitter_seconds,
                    "requested_min_interval_seconds": float(min_interval_seconds),
                    "requested_jitter_seconds": float(jitter_seconds),
                },
            )
        return _PRODUCTION_GATE
    with _PRODUCTION_INIT_LOCK:
        if _PRODUCTION_GATE is None:
            _PRODUCTION_GATE = EastmoneyRequestGate(
                min_interval_seconds=min_interval_seconds,
                jitter_seconds=jitter_seconds,
                _allow_direct_construction=True,
            )
        elif (
            _PRODUCTION_GATE.min_interval_seconds != float(min_interval_seconds)
            or _PRODUCTION_GATE.jitter_seconds != float(jitter_seconds)
        ):
            raise ConfigurationError(
                "production EastmoneyRequestGate already initialized with "
                "different min_interval_seconds/jitter_seconds",
                details={
                    "field": "EastmoneyRequestGate",
                    "rule": "production_config_drift",
                    "existing_min_interval_seconds": (
                        _PRODUCTION_GATE.min_interval_seconds
                    ),
                    "existing_jitter_seconds": _PRODUCTION_GATE.jitter_seconds,
                    "requested_min_interval_seconds": float(min_interval_seconds),
                    "requested_jitter_seconds": float(jitter_seconds),
                },
            )
        return _PRODUCTION_GATE


def create_isolated_eastmoney_request_gate_for_tests(
    *,
    min_interval_seconds: float = 1.0,
    jitter_seconds: float = 0.0,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    random_func: Callable[[], float] | None = None,
) -> EastmoneyRequestGate:
    """Test-only factory: always returns a fresh, isolated gate instance.

    Do not call from production / bootstrap wiring. Inject deterministic clock,
    sleep, and random for unit tests.
    """
    return EastmoneyRequestGate(
        min_interval_seconds=min_interval_seconds,
        jitter_seconds=jitter_seconds,
        clock=clock,
        sleep=sleep,
        random_func=random_func,
        _allow_direct_construction=True,
    )


def _reset_production_eastmoney_request_gate_for_tests() -> None:
    """Test-only: clear the production singleton (not for production use)."""
    global _PRODUCTION_GATE
    with _PRODUCTION_INIT_LOCK:
        _PRODUCTION_GATE = None
