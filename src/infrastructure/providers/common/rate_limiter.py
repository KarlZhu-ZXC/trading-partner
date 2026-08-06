"""Fixed-window provider rate limiter (Phase 1D D5b)."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from application.dto.provider_resilience import RateLimitDecision, RateLimitPolicy
from application.ports.clock import Clock
from application.ports.provider_rate_limit_store import ProviderRateLimitStore
from domain.common.enums import DataCategory, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# High-throughput vendors used for mocks, null adapters, and local master/seed.
_HIGH_THROUGHPUT_VENDORS: frozenset[VendorId] = frozenset(
    {
        VendorId.MOCK_A_SHARE,
        VendorId.MOCK_US,
        VendorId.NULL,
        VendorId.LOCAL_MASTER,
        VendorId.SEED_FIXTURE,
    }
)

# Internal admission control, not a statement of upstream Yahoo quota. The US
# composite intentionally fans out one symbol plus SPY/QQQ/IWM in parallel, and
# company updates may overlap a separate news request. A 1/s cap caused our own
# Router to reject valid calls before they reached Yahoo.
_BOUNDED_COMPOSITE_LIMITS: dict[VendorId, int] = {
    VendorId.TENCENT: 4,
    VendorId.YFINANCE: 8,
}


class DefaultRateLimitPolicy:
    """Frozen default fixed-window limits per VendorId (design §12.4)."""

    def for_vendor(
        self, vendor: VendorId, category: DataCategory
    ) -> RateLimitPolicy:
        """Return policy for ``vendor``.

        ``mock_*`` / ``null`` / ``local_master`` / ``seed_fixture`` → 1s / 1000.
        ``tencent`` → 1s / 4 and ``yfinance`` → 1s / 8 to admit bounded
        workflow fan-out without manufacturing internal upstream failures.
        All other frozen :class:`~domain.common.enums.VendorId` → 1s / 1.
        ``category`` is accepted for per-category overrides.
        """
        if vendor in _HIGH_THROUGHPUT_VENDORS or vendor.value.startswith("mock_"):
            return RateLimitPolicy(window_seconds=1, limit_count=1000)
        if vendor in _BOUNDED_COMPOSITE_LIMITS:
            return RateLimitPolicy(
                window_seconds=1,
                limit_count=_BOUNDED_COMPOSITE_LIMITS[vendor],
            )
        return RateLimitPolicy(window_seconds=1, limit_count=1)


def floor_window_start(now: datetime, window_seconds: int) -> datetime:
    """UTC-aware fixed-window start: floor(ts / window) * window."""
    aware = require_aware_datetime(now, field_name="clock.now")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    ts = aware.timestamp()
    start_ts = math.floor(ts / window_seconds) * window_seconds
    return datetime.fromtimestamp(start_ts, tz=UTC)


class ProviderRateLimiter:
    """Bounded fixed-window admission scheduler backed by a shared store.

    Reservations are anonymous and short-lived: a future slot is persisted at
    reservation time and becomes usable at the start of its fixed window.  A
    cancelled waiter does not release the slot; it naturally expires with the
    window.
    """

    def __init__(
        self,
        store: ProviderRateLimitStore,
        clock: Clock,
        policy: DefaultRateLimitPolicy | None = None,
        max_wait_seconds: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._clock = clock
        self._policy = policy if policy is not None else DefaultRateLimitPolicy()
        self._max_wait_seconds = self._validate_max_wait_seconds(max_wait_seconds)
        self._sleep = sleep

    @staticmethod
    def _validate_max_wait_seconds(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DataContractError(
                "max_wait_seconds must be a finite nonnegative number",
                details={"field": "max_wait_seconds", "type": type(value).__name__},
            )
        normalized = float(value)
        if not math.isfinite(normalized) or normalized < 0:
            raise DataContractError(
                "max_wait_seconds must be a finite nonnegative number",
                details={"field": "max_wait_seconds"},
            )
        return normalized

    def reserve(
        self,
        vendor: VendorId,
        category: DataCategory,
        max_wait_seconds: float | None = None,
    ) -> RateLimitDecision:
        """Synchronously reserve the earliest slot within a bounded budget.

        This method does not sleep.  It atomically records a current or future
        reservation and returns its scheduled timestamp so an async caller can
        await it without holding a thread.
        """
        limit_policy = self._policy.for_vendor(vendor, category)
        budget = (
            self._max_wait_seconds
            if max_wait_seconds is None
            else self._validate_max_wait_seconds(max_wait_seconds)
        )
        now = require_aware_datetime(self._clock.now(), field_name="clock.now")
        current_start = floor_window_start(now, limit_policy.window_seconds)
        deadline = now + timedelta(seconds=budget)

        max_offset = math.floor(
            (deadline - current_start).total_seconds() / limit_policy.window_seconds
        )
        for offset in range(max_offset + 1):
            window_start = current_start + timedelta(
                seconds=offset * limit_policy.window_seconds
            )
            snapshot = self._store.try_reserve(
                vendor=vendor,
                category=category,
                window_start=window_start,
                window_seconds=limit_policy.window_seconds,
                limit_count=limit_policy.limit_count,
                at=now,
            )
            if snapshot is None:
                continue

            wait_seconds = max(0.0, (window_start - now).total_seconds())
            scheduled_at = now if wait_seconds == 0 else window_start
            return RateLimitDecision(
                allowed=True,
                remaining=max(snapshot.limit_count - snapshot.request_count, 0),
                reset_at=window_start
                + timedelta(seconds=limit_policy.window_seconds),
                limit_per_window=snapshot.limit_count,
                scheduled_at=scheduled_at,
                wait_seconds=wait_seconds,
                queued=wait_seconds > 0,
            )

        # No slot was available in the current or bounded future windows.  A
        # denied probe has no reservation side effect, so its count remains
        # unchanged in the store.
        return RateLimitDecision(
            allowed=False,
            remaining=0,
            reset_at=current_start + timedelta(seconds=limit_policy.window_seconds),
            limit_per_window=limit_policy.limit_count,
            scheduled_at=None,
            wait_seconds=0.0,
            queued=False,
        )

    async def acquire(
        self,
        vendor: VendorId,
        category: DataCategory,
        max_wait_seconds: float | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> RateLimitDecision:
        """Reserve a slot and asynchronously await its scheduled window.

        ``asyncio.CancelledError`` from the injected sleeper intentionally
        propagates.  The anonymous reservation remains in the store until its
        fixed window expires, so cancellation cannot release another caller's
        slot accidentally.
        """
        decision = self.reserve(vendor, category, max_wait_seconds)
        if not decision.allowed or decision.wait_seconds <= 0:
            return decision
        sleeper = self._sleep if sleep is None else sleep
        await sleeper(decision.wait_seconds)
        return decision
