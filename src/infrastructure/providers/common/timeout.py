"""Async timeout helper for provider calls (Phase 1D D5b)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from domain.common.errors import ProviderTimeoutError


async def run_with_timeout[T](awaitable: Awaitable[T], timeout_seconds: float) -> T:
    """Await ``awaitable`` with a positive timeout.

    On timeout the underlying task is cancelled and a clean
    :class:`~domain.common.errors.ProviderTimeoutError` is raised (no raw
    exception chain). Provider :class:`~domain.common.errors.TradingPartnerError`
    and non-timeout exceptions propagate unchanged. Cancellation propagates.
    """
    if not isinstance(timeout_seconds, (int, float)) or isinstance(
        timeout_seconds, bool
    ):
        raise ValueError("timeout_seconds must be a positive number")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    try:
        return await asyncio.wait_for(awaitable, timeout=float(timeout_seconds))
    except TimeoutError:
        # asyncio.TimeoutError is an alias of TimeoutError on 3.11+.
        # from None: no raw cancel/timeout chain or awaitable params.
        raise ProviderTimeoutError(
            "Provider call timed out",
            details={"timeout_seconds": float(timeout_seconds)},
        ) from None
