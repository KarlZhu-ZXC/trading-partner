"""Same-vendor retry helper (Phase 1D D5b).

Retry is limited to a single vendor; chain fallback is not retry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from domain.common.errors import (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    TradingPartnerError,
)

# Default same-vendor short retries (design §12.2).
DEFAULT_RETRYABLE_ERROR_TYPES: tuple[type[TradingPartnerError], ...] = (
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderRateLimitError,
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Per-vendor retry policy. ``max_attempts`` includes the first try."""

    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    retryable_error_types: tuple[type[TradingPartnerError], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise ValueError("max_attempts must be an int")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if not isinstance(self.base_delay_seconds, (int, float)) or isinstance(
            self.base_delay_seconds, bool
        ):
            raise ValueError("base_delay_seconds must be a number")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be nonnegative")
        if not isinstance(self.max_delay_seconds, (int, float)) or isinstance(
            self.max_delay_seconds, bool
        ):
            raise ValueError("max_delay_seconds must be a number")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be nonnegative")
        if float(self.max_delay_seconds) < float(self.base_delay_seconds):
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if not isinstance(self.retryable_error_types, tuple):
            raise ValueError("retryable_error_types must be a tuple of exception types")
        for item in self.retryable_error_types:
            if not isinstance(item, type) or not issubclass(item, BaseException):
                raise ValueError(
                    "retryable_error_types must contain exception types"
                )


def delay_after_failure(attempt: int, policy: RetryPolicy) -> float:
    """Delay after attempt N fails (N from 1), before attempt N+1.

    ``min(base * 2 ** (N - 1), max_delay)``. Callers must not sleep after the
    final attempt.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    raw = float(policy.base_delay_seconds) * float(2 ** (attempt - 1))
    capped = float(policy.max_delay_seconds)
    return raw if raw < capped else capped


async def run_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``operation`` up to ``policy.max_attempts`` times.

    Recreates the awaitable each attempt. Retries only configured error types.
    Final error identity/type/details are preserved. Cancellation propagates.
    """
    if not isinstance(policy, RetryPolicy):
        raise TypeError("policy must be a RetryPolicy")

    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except BaseException as exc:
            # Cancellation must not be swallowed as a retryable provider error.
            if isinstance(exc, asyncio.CancelledError):
                raise
            if not isinstance(exc, policy.retryable_error_types):
                raise
            last_error = exc
            if attempt >= policy.max_attempts:
                raise
            await sleep(delay_after_failure(attempt, policy))

    # Unreachable when max_attempts >= 1; keeps type checkers happy.
    assert last_error is not None
    raise last_error
