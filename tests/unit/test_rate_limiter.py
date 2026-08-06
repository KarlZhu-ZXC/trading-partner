"""Phase 1D D5b: DefaultRateLimitPolicy + ProviderRateLimiter fixed-window."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from application.dto.provider_resilience import RateLimitDecision, RateLimitPolicy
from application.dto.provider_state import ProviderRateLimitSnapshot
from conftest import FixedClock
from domain.common.enums import DataCategory, VendorId
from domain.common.errors import DataContractError, PersistenceError
from infrastructure.providers.common.rate_limiter import (
    DefaultRateLimitPolicy,
    ProviderRateLimiter,
    floor_window_start,
)

NOW = datetime(2026, 7, 16, 12, 0, 0, 500_000, tzinfo=UTC)  # mid-second


class InMemoryRateLimitStore:
    """Thread-safe in-memory fixed-window store for unit tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str, datetime], dict[str, Any]] = {}
        self.fail = fail
        self.reserve_calls = 0

    def try_reserve(
        self,
        *,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
        window_seconds: int,
        limit_count: int,
        at: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        if self.fail:
            raise PersistenceError(
                "Failed to reserve provider rate limit",
                details={"error_type": "SimulatedStoreError"},
            )
        with self._lock:
            self.reserve_calls += 1
            key = (vendor.value, category.value, window_start)
            row = self._rows.get(key)
            if row is not None and int(row["request_count"]) >= limit_count:
                return None
            count = 1 if row is None else int(row["request_count"]) + 1
            self._rows[key] = {
                "request_count": count,
                "window_seconds": window_seconds,
                "limit_count": limit_count,
                "updated_at": at,
            }
            return ProviderRateLimitSnapshot(
                vendor=vendor,
                category=category,
                window_start=window_start,
                window_seconds=window_seconds,
                request_count=count,
                limit_count=limit_count,
                updated_at=at,
            )

    def get(
        self,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        with self._lock:
            row = self._rows.get((vendor.value, category.value, window_start))
            if row is None:
                return None
            return ProviderRateLimitSnapshot(
                vendor=vendor,
                category=category,
                window_start=window_start,
                window_seconds=int(row["window_seconds"]),
                request_count=int(row["request_count"]),
                limit_count=int(row["limit_count"]),
                updated_at=row["updated_at"],
            )


# --- Policy ---


def test_default_policy_for_every_vendor_id() -> None:
    policy = DefaultRateLimitPolicy()
    high = {
        VendorId.MOCK_A_SHARE,
        VendorId.MOCK_US,
        VendorId.NULL,
        VendorId.LOCAL_MASTER,
        VendorId.SEED_FIXTURE,
    }
    for vendor in VendorId:
        p = policy.for_vendor(vendor, DataCategory.MARKET_QUOTE)
        assert p.window_seconds == 1
        if vendor in high:
            assert p.limit_count == 1000, vendor
        elif vendor in {VendorId.TENCENT, VendorId.YFINANCE}:
            expected = 4 if vendor is VendorId.TENCENT else 8
            assert p.limit_count == expected, vendor
        else:
            assert p.limit_count == 1, vendor


def test_rate_limit_policy_validation() -> None:
    with pytest.raises(DataContractError, match="positive"):
        RateLimitPolicy(window_seconds=0, limit_count=1)
    with pytest.raises(DataContractError, match="positive"):
        RateLimitPolicy(window_seconds=1, limit_count=0)


def test_rate_limit_decision_validation() -> None:
    reset = datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC)
    d = RateLimitDecision(
        allowed=True,
        remaining=0,
        reset_at=reset,
        limit_per_window=1,
    )
    assert d.allowed is True
    with pytest.raises(DataContractError, match="timezone-aware"):
        RateLimitDecision(
            allowed=False,
            remaining=0,
            reset_at=datetime(2026, 7, 16, 12, 0, 1),
            limit_per_window=1,
        )


def test_dto_exports() -> None:
    from application.dto import RateLimitDecision as D
    from application.dto import RateLimitPolicy as P

    assert D is RateLimitDecision
    assert P is RateLimitPolicy


# --- Window math ---


def test_floor_window_start_utc() -> None:
    # 12:00:00.500 → floor second window = 12:00:00
    start = floor_window_start(NOW, 1)
    assert start == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    assert start.tzinfo is not None

    # 60s window: floor to minute
    mid = datetime(2026, 7, 16, 12, 0, 45, tzinfo=UTC)
    assert floor_window_start(mid, 60) == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)

    # boundary exactly on window start stays
    exact = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    assert floor_window_start(exact, 1) == exact


def test_floor_window_requires_aware() -> None:
    with pytest.raises(DataContractError, match="timezone-aware"):
        floor_window_start(datetime(2026, 7, 16, 12, 0, 0), 1)


# --- ProviderRateLimiter ---


def test_reserve_allows_until_limit() -> None:
    store = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock)
    # EASTMONEY default limit=1
    d1 = limiter.reserve(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE)
    assert d1.allowed is True
    assert d1.remaining == 0
    assert d1.limit_per_window == 1
    assert d1.reset_at == datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC)

    d2 = limiter.reserve(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE)
    assert d2.allowed is False
    assert d2.remaining == 0
    assert d2.limit_per_window == 1
    assert store.reserve_calls == 2


def test_mock_vendor_high_limit() -> None:
    store = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock)
    for i in range(5):
        d = limiter.reserve(VendorId.MOCK_US, DataCategory.MARKET_QUOTE)
        assert d.allowed is True
        assert d.limit_per_window == 1000
        assert d.remaining == 1000 - (i + 1)


def test_window_boundary_resets_counter() -> None:
    store = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock)
    d1 = limiter.reserve(VendorId.EASTMONEY, DataCategory.NEWS)
    assert d1.allowed is True
    d2 = limiter.reserve(VendorId.EASTMONEY, DataCategory.NEWS)
    assert d2.allowed is False

    # Advance into next 1s window.
    clock.set(datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC))
    d3 = limiter.reserve(VendorId.EASTMONEY, DataCategory.NEWS)
    assert d3.allowed is True
    assert d3.reset_at == datetime(2026, 7, 16, 12, 0, 2, tzinfo=UTC)


def test_denied_does_not_increment_store() -> None:
    store = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock)
    limiter.reserve(VendorId.EASTMONEY, DataCategory.MARKET_OHLCV)
    limiter.reserve(VendorId.EASTMONEY, DataCategory.MARKET_OHLCV)
    limiter.reserve(VendorId.EASTMONEY, DataCategory.MARKET_OHLCV)
    window = floor_window_start(NOW, 1)
    snap = store.get(VendorId.EASTMONEY, DataCategory.MARKET_OHLCV, window)
    assert snap is not None
    assert snap.request_count == 1


def test_naive_clock_rejected() -> None:
    store = InMemoryRateLimitStore()

    class NaiveClock:
        def now(self) -> datetime:
            return datetime(2026, 7, 16, 12, 0, 0)

    limiter = ProviderRateLimiter(store, NaiveClock())  # type: ignore[arg-type]
    with pytest.raises(DataContractError, match="timezone-aware"):
        limiter.reserve(VendorId.NULL, DataCategory.INSTRUMENT_MASTER)


def test_store_persistence_error_propagates_typed() -> None:
    store = InMemoryRateLimitStore(fail=True)
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock)
    with pytest.raises(PersistenceError) as exc_info:
        limiter.reserve(VendorId.MOCK_A_SHARE, DataCategory.MARKET_QUOTE)
    assert exc_info.value.details == {"error_type": "SimulatedStoreError"}
    assert "password" not in str(exc_info.value).lower()


def test_concurrency_exact_reservation_counts() -> None:
    store = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock)
    n = 25
    barrier = threading.Barrier(n)
    decisions: list[RateLimitDecision] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def _once() -> None:
        try:
            barrier.wait(timeout=10)
            d = limiter.reserve(VendorId.MOCK_US, DataCategory.MARKET_QUOTE)
            with lock:
                decisions.append(d)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(_once) for _ in range(n)]
        for f in as_completed(futs):
            f.result()

    assert errors == []
    assert len(decisions) == n
    assert all(d.allowed for d in decisions)
    remainings = sorted(d.remaining for d in decisions if d.remaining is not None)
    assert remainings == list(range(1000 - n, 1000))
    window = floor_window_start(NOW, 1)
    snap = store.get(VendorId.MOCK_US, DataCategory.MARKET_QUOTE, window)
    assert snap is not None
    assert snap.request_count == n


def test_limiter_accepts_store_protocol() -> None:
    from application.ports.provider_rate_limit_store import ProviderRateLimitStore

    store: ProviderRateLimitStore = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock, DefaultRateLimitPolicy())
    d = limiter.reserve(VendorId.SEED_FIXTURE, DataCategory.INSTRUMENT_MASTER)
    assert d.allowed is True
    assert d.limit_per_window == 1000


def test_common_exports() -> None:
    from infrastructure.providers.common import (
        DefaultRateLimitPolicy as P,
    )
    from infrastructure.providers.common import (
        ProviderRateLimiter as L,
    )

    assert P is DefaultRateLimitPolicy
    assert L is ProviderRateLimiter


def test_future_reservations_distribute_across_windows_without_overbooking() -> None:
    store = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(store, clock, max_wait_seconds=2.0)

    decisions = [
        limiter.reserve(
            VendorId.YFINANCE,
            DataCategory.MARKET_OHLCV,
            max_wait_seconds=2.0,
        )
        for _ in range(12)
    ]
    assert sum(decision.allowed for decision in decisions) == 12
    assert sum(decision.queued for decision in decisions) == 4

    current = floor_window_start(NOW, 1)
    next_window = current + timedelta(seconds=1)
    current_snapshot = store.get(
        VendorId.YFINANCE, DataCategory.MARKET_OHLCV, current
    )
    next_snapshot = store.get(
        VendorId.YFINANCE, DataCategory.MARKET_OHLCV, next_window
    )
    assert current_snapshot is not None and current_snapshot.request_count == 8
    assert next_snapshot is not None and next_snapshot.request_count == 4


@pytest.mark.asyncio
async def test_async_acquire_waits_and_cancellation_keeps_reservation() -> None:
    store = InMemoryRateLimitStore()
    clock = FixedClock(NOW)
    waits: list[float] = []

    async def _sleep(delay: float) -> None:
        waits.append(delay)

    limiter = ProviderRateLimiter(store, clock, max_wait_seconds=1.0, sleep=_sleep)
    for _ in range(8):
        assert limiter.reserve(
            VendorId.YFINANCE, DataCategory.MARKET_QUOTE
        ).allowed

    decision = await limiter.acquire(
        VendorId.YFINANCE, DataCategory.MARKET_QUOTE, max_wait_seconds=1.0
    )
    assert decision.allowed is True
    assert decision.queued is True
    assert waits == [pytest.approx(0.5)]

    async def _cancel(_delay: float) -> None:
        raise asyncio.CancelledError

    cancelled = ProviderRateLimiter(
        store,
        clock,
        max_wait_seconds=1.0,
        sleep=_cancel,
    )
    with pytest.raises(asyncio.CancelledError):
        await cancelled.acquire(
            VendorId.YFINANCE, DataCategory.MARKET_QUOTE, max_wait_seconds=1.0
        )
    next_window = floor_window_start(NOW, 1) + timedelta(seconds=1)
    snapshot = store.get(VendorId.YFINANCE, DataCategory.MARKET_QUOTE, next_window)
    assert snapshot is not None and snapshot.request_count == 2
