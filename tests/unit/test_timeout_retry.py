"""Phase 1D D5b: run_with_timeout + RetryPolicy / run_with_retry."""

from __future__ import annotations

import asyncio

import pytest

from domain.common.errors import (
    DataContractError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from infrastructure.providers.common.retry import (
    DEFAULT_RETRYABLE_ERROR_TYPES,
    RetryPolicy,
    delay_after_failure,
    run_with_retry,
)
from infrastructure.providers.common.timeout import run_with_timeout

# --- timeout ---


@pytest.mark.asyncio
async def test_run_with_timeout_success() -> None:
    async def _ok() -> str:
        return "done"

    assert await run_with_timeout(_ok(), 1.0) == "done"


@pytest.mark.asyncio
async def test_run_with_timeout_rejects_nonpositive() -> None:
    loop = asyncio.get_running_loop()
    for bad in (0, -1.0, 0.0):
        fut: asyncio.Future[int] = loop.create_future()
        with pytest.raises(ValueError, match="positive"):
            await run_with_timeout(fut, bad)


@pytest.mark.asyncio
async def test_run_with_timeout_raises_provider_timeout_without_chain() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hang() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await run_with_timeout(_hang(), 0.05)

    err = exc_info.value
    assert err.code == "PROVIDER_TIMEOUT_ERROR"
    assert err.retryable is True
    assert err.details == {"timeout_seconds": 0.05}
    # from None: no cause chain and context suppressed in display.
    assert err.__cause__ is None
    assert err.__suppress_context__ is True
    assert err.details == {"timeout_seconds": 0.05}
    assert await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert started.is_set()


@pytest.mark.asyncio
async def test_run_with_timeout_preserves_trading_partner_error() -> None:
    async def _boom() -> None:
        raise ProviderAuthenticationError(
            "auth failed",
            details={"vendor": "eastmoney"},
        )

    with pytest.raises(ProviderAuthenticationError) as exc_info:
        await run_with_timeout(_boom(), 1.0)
    assert exc_info.value.details == {"vendor": "eastmoney"}
    assert exc_info.value.code == "PROVIDER_AUTHENTICATION_ERROR"


@pytest.mark.asyncio
async def test_run_with_timeout_preserves_unknown_exceptions() -> None:
    async def _boom() -> None:
        raise RuntimeError("raw provider bug")

    with pytest.raises(RuntimeError, match="raw provider bug"):
        await run_with_timeout(_boom(), 1.0)


@pytest.mark.asyncio
async def test_run_with_timeout_propagates_cancellation() -> None:
    async def _work() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(run_with_timeout(_work(), 30.0))
    await asyncio.sleep(0)  # let task start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- RetryPolicy validation ---


def test_retry_policy_defaults_and_validation() -> None:
    policy = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=0.05,
        max_delay_seconds=1.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    assert policy.max_attempts == 2
    assert ProviderTimeoutError in policy.retryable_error_types

    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(
            max_attempts=0,
            base_delay_seconds=0.0,
            max_delay_seconds=0.0,
            retryable_error_types=(),
        )
    with pytest.raises(ValueError, match="nonnegative"):
        RetryPolicy(
            max_attempts=1,
            base_delay_seconds=-0.01,
            max_delay_seconds=1.0,
            retryable_error_types=(),
        )
    with pytest.raises(ValueError, match="max_delay"):
        RetryPolicy(
            max_attempts=1,
            base_delay_seconds=1.0,
            max_delay_seconds=0.5,
            retryable_error_types=(),
        )


def test_delay_after_failure_capped_exponential() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=0.05,
        max_delay_seconds=1.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    assert delay_after_failure(1, policy) == 0.05
    assert delay_after_failure(2, policy) == 0.1
    assert delay_after_failure(3, policy) == 0.2
    assert delay_after_failure(4, policy) == 0.4
    assert delay_after_failure(5, policy) == 0.8
    assert delay_after_failure(6, policy) == 1.0  # capped
    assert delay_after_failure(10, policy) == 1.0


# --- run_with_retry ---


@pytest.mark.asyncio
async def test_run_with_retry_succeeds_first_attempt() -> None:
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    assert await run_with_retry(op, policy) == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_run_with_retry_recreates_operation_and_delays() -> None:
    calls = 0
    sleeps: list[float] = []

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ProviderTimeoutError("t", details={"n": calls})
        return "ok"

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.05,
        max_delay_seconds=1.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    assert await run_with_retry(op, policy, sleep=fake_sleep) == "ok"
    assert calls == 3
    assert sleeps == [0.05, 0.1]  # no sleep after final success


@pytest.mark.asyncio
async def test_run_with_retry_preserves_final_error_identity() -> None:
    original = ProviderUnavailableError(
        "down",
        details={"vendor": "yfinance", "token": "not_a_secret_field"},
    )
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise original

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    policy = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=0.05,
        max_delay_seconds=1.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    with pytest.raises(ProviderUnavailableError) as exc_info:
        await run_with_retry(op, policy, sleep=fake_sleep)
    assert exc_info.value is original
    assert exc_info.value.details == original.details
    assert calls == 2
    assert sleeps == [0.05]  # no sleep after final failure


@pytest.mark.asyncio
async def test_run_with_retry_does_not_retry_non_configured_types() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise DataContractError("bad", details={"field": "x"})

    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    with pytest.raises(DataContractError):
        await run_with_retry(op, policy)
    assert calls == 1


@pytest.mark.asyncio
async def test_run_with_retry_rate_limit_is_retryable() -> None:
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderRateLimitError("rl")
        return "ok"

    policy = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    assert await run_with_retry(op, policy) == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_run_with_retry_propagates_cancellation() -> None:
    started = asyncio.Event()

    async def op() -> None:
        started.set()
        await asyncio.sleep(30)

    policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    task = asyncio.create_task(run_with_retry(op, policy))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_run_with_retry_unknown_exception_not_retried() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("unexpected")

    policy = RetryPolicy(
        max_attempts=4,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        retryable_error_types=DEFAULT_RETRYABLE_ERROR_TYPES,
    )
    with pytest.raises(RuntimeError, match="unexpected"):
        await run_with_retry(op, policy)
    assert calls == 1


def test_common_exports() -> None:
    from infrastructure.providers.common import (
        RetryPolicy as RP,
    )
    from infrastructure.providers.common import (
        run_with_retry as rwr,
    )
    from infrastructure.providers.common import (
        run_with_timeout as rwt,
    )

    assert RP is RetryPolicy
    assert rwr is run_with_retry
    assert rwt is run_with_timeout


def test_trading_partner_error_subclass_identity() -> None:
    err: TradingPartnerError = ProviderTimeoutError("t")
    assert isinstance(err, TimeoutError) is False
    assert err.code == "PROVIDER_TIMEOUT_ERROR"
