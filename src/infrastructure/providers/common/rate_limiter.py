"""Fixed-window provider rate limiter (Phase 1D D5b)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from application.dto.provider_resilience import RateLimitDecision, RateLimitPolicy
from application.ports.clock import Clock
from application.ports.provider_rate_limit_store import ProviderRateLimitStore
from domain.common.enums import DataCategory, VendorId
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
    """Consume-on-check fixed-window rate limiter backed by a store."""

    def __init__(
        self,
        store: ProviderRateLimitStore,
        clock: Clock,
        policy: DefaultRateLimitPolicy | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._policy = policy if policy is not None else DefaultRateLimitPolicy()

    def check_and_consume(
        self, vendor: VendorId, category: DataCategory
    ) -> RateLimitDecision:
        """Atomically consume one request and return allow/deny decision.

        Every call consumes (including denied). Store typed errors propagate.
        """
        limit_policy = self._policy.for_vendor(vendor, category)
        now = require_aware_datetime(self._clock.now(), field_name="clock.now")
        window_start = floor_window_start(now, limit_policy.window_seconds)
        snapshot = self._store.consume(
            vendor=vendor,
            category=category,
            window_start=window_start,
            window_seconds=limit_policy.window_seconds,
            limit_count=limit_policy.limit_count,
            at=now,
        )
        allowed = snapshot.request_count <= snapshot.limit_count
        remaining = max(snapshot.limit_count - snapshot.request_count, 0)
        reset_at = window_start + timedelta(seconds=limit_policy.window_seconds)
        return RateLimitDecision(
            allowed=allowed,
            remaining=remaining,
            reset_at=reset_at,
            limit_per_window=limit_policy.limit_count,
        )
