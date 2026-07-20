"""In-process thread-safe circuit breaker (Phase 1D D5b).

State is keyed by ``(vendor, category)``. Observability projection tables are
not consulted; only this process-local breaker drives skip decisions.

Every admitted call receives a unique :class:`CircuitCallPermit`. Results are
recorded against that permit so concurrent half-open probes and later normal
CLOSED traffic cannot be confused.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from application.dto.provider_resilience import CircuitCallPermit
from application.ports.clock import Clock
from domain.common.enums import CircuitState, DataCategory, VendorId
from domain.common.errors import ProviderUnavailableError
from domain.common.time import require_aware_datetime


class _BreakerBucket:
    """Mutable per-key breaker state (protected by CircuitBreaker lock)."""

    __slots__ = (
        "state",
        "consecutive_failures",
        "opened_at",
        "half_open_generation",
        "active_half_open_call_ids",
        "outstanding_call_ids",
        "next_call_id",
    )

    def __init__(self) -> None:
        self.state: CircuitState = CircuitState.CLOSED
        self.consecutive_failures: int = 0
        self.opened_at: datetime | None = None
        # 0 means never entered HALF_OPEN; OPEN→HALF_OPEN increments to 1, 2, …
        self.half_open_generation: int = 0
        self.active_half_open_call_ids: set[int] = set()
        self.outstanding_call_ids: set[int] = set()
        self.next_call_id: int = 1


class CircuitBreaker:
    """Thread-safe consecutive-failure circuit breaker."""

    def __init__(
        self,
        clock: Clock,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
        half_open_max_calls: int = 1,
    ) -> None:
        if not isinstance(failure_threshold, int) or isinstance(failure_threshold, bool):
            raise ValueError("failure_threshold must be an int")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if not isinstance(recovery_timeout_seconds, (int, float)) or isinstance(
            recovery_timeout_seconds, bool
        ):
            raise ValueError("recovery_timeout_seconds must be a number")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be positive")
        if not isinstance(half_open_max_calls, int) or isinstance(
            half_open_max_calls, bool
        ):
            raise ValueError("half_open_max_calls must be an int")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")
        if clock is None:
            raise ValueError("clock is required")

        self._clock = clock
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = float(recovery_timeout_seconds)
        self.half_open_max_calls = half_open_max_calls
        self._lock = threading.RLock()
        self._buckets: dict[tuple[VendorId, DataCategory], _BreakerBucket] = {}

    def _bucket(self, vendor: VendorId, category: DataCategory) -> _BreakerBucket:
        key = (vendor, category)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _BreakerBucket()
            self._buckets[key] = bucket
        return bucket

    def _now(self) -> datetime:
        now = self._clock.now()
        return require_aware_datetime(now, field_name="clock.now")

    def _recovery_elapsed(self, bucket: _BreakerBucket, now: datetime) -> bool:
        if bucket.opened_at is None:
            return False
        deadline = bucket.opened_at + timedelta(
            seconds=self.recovery_timeout_seconds
        )
        return now >= deadline

    def _maybe_open_to_half_open(
        self, bucket: _BreakerBucket, now: datetime
    ) -> None:
        """Time-based OPEN → HALF_OPEN without reserving a probe slot."""
        if bucket.state is CircuitState.OPEN and self._recovery_elapsed(bucket, now):
            bucket.state = CircuitState.HALF_OPEN
            bucket.half_open_generation += 1
            bucket.active_half_open_call_ids.clear()

    def _reject(
        self,
        vendor: VendorId,
        category: DataCategory,
        circuit_state: CircuitState,
    ) -> None:
        raise ProviderUnavailableError(
            "Provider circuit is open",
            details={
                "vendor": vendor.value,
                "category": category.value,
                "circuit_state": circuit_state.value,
            },
        )

    def _mint_call_id(self, bucket: _BreakerBucket) -> int:
        call_id = bucket.next_call_id
        bucket.next_call_id += 1
        bucket.outstanding_call_ids.add(call_id)
        return call_id

    def _is_current_active_half_open(
        self, bucket: _BreakerBucket, permit: CircuitCallPermit
    ) -> bool:
        return (
            permit.half_open_generation is not None
            and permit.half_open_generation == bucket.half_open_generation
            and permit.call_id in bucket.active_half_open_call_ids
        )

    def _trip_open(self, bucket: _BreakerBucket, now: datetime) -> None:
        """OPEN the circuit and invalidate remaining half-open permits."""
        bucket.state = CircuitState.OPEN
        bucket.opened_at = now
        bucket.consecutive_failures = self.failure_threshold
        for call_id in bucket.active_half_open_call_ids:
            bucket.outstanding_call_ids.discard(call_id)
        bucket.active_half_open_call_ids.clear()

    def state(self, vendor: VendorId, category: DataCategory) -> CircuitState:
        """Return current state; may transition OPEN→HALF_OPEN without probe reserve."""
        with self._lock:
            bucket = self._bucket(vendor, category)
            self._maybe_open_to_half_open(bucket, self._now())
            return bucket.state

    def before_call(
        self, vendor: VendorId, category: DataCategory
    ) -> CircuitCallPermit:
        """Admit a call and return a unique permit, or raise ProviderUnavailableError.

        OPEN before recovery rejects. Recovery elapsed → HALF_OPEN then probe
        reservation. HALF_OPEN admits at most ``half_open_max_calls`` concurrent
        probes (atomic under the lock). CLOSED always admits with
        ``half_open_generation=None``.
        """
        with self._lock:
            bucket = self._bucket(vendor, category)
            now = self._now()
            self._maybe_open_to_half_open(bucket, now)

            if bucket.state is CircuitState.OPEN:
                self._reject(vendor, category, CircuitState.OPEN)

            if bucket.state is CircuitState.HALF_OPEN:
                if len(bucket.active_half_open_call_ids) >= self.half_open_max_calls:
                    self._reject(vendor, category, CircuitState.HALF_OPEN)
                call_id = self._mint_call_id(bucket)
                bucket.active_half_open_call_ids.add(call_id)
                return CircuitCallPermit(
                    vendor=vendor,
                    category=category,
                    call_id=call_id,
                    half_open_generation=bucket.half_open_generation,
                )

            # CLOSED: admit without half-open accounting.
            call_id = self._mint_call_id(bucket)
            return CircuitCallPermit(
                vendor=vendor,
                category=category,
                call_id=call_id,
                half_open_generation=None,
            )

    def record_success(self, permit: CircuitCallPermit) -> None:
        """Record a successful call for ``permit``.

        An active half-open success resets/transitions only while state is
        HALF_OPEN (close + clear counters). If the breaker is already CLOSED
        (a sibling closed earlier), only release that active permit — do not
        wipe newer CLOSED failure counters. OPEN stays a no-op. Stale /
        duplicate / invalidated permits are no-ops.
        """
        with self._lock:
            bucket = self._bucket(permit.vendor, permit.category)
            if permit.call_id not in bucket.outstanding_call_ids:
                return
            bucket.outstanding_call_ids.discard(permit.call_id)

            if self._is_current_active_half_open(bucket, permit):
                bucket.active_half_open_call_ids.discard(permit.call_id)
                if bucket.state is CircuitState.HALF_OPEN:
                    bucket.state = CircuitState.CLOSED
                    bucket.consecutive_failures = 0
                    bucket.opened_at = None
                # CLOSED: late sibling half-open success — release only.
                # OPEN: no-op on counters (permit already released above).
                return

            if bucket.state is CircuitState.CLOSED:
                bucket.consecutive_failures = 0
                return

            # OPEN or non-active half-open origin: no-op on counters.

    def record_failure(self, permit: CircuitCallPermit) -> None:
        """Record a failed call for ``permit``.

        Any failure from a currently active half-open permit immediately OPENs,
        resets ``opened_at``, and invalidates remaining half-open permits — even
        if a sibling already transitioned the breaker to CLOSED. Normal CLOSED
        permits only increment the consecutive-failure threshold. Stale /
        duplicate / invalidated permits are no-ops.
        """
        with self._lock:
            bucket = self._bucket(permit.vendor, permit.category)
            if permit.call_id not in bucket.outstanding_call_ids:
                return
            bucket.outstanding_call_ids.discard(permit.call_id)
            now = self._now()

            if self._is_current_active_half_open(bucket, permit):
                bucket.active_half_open_call_ids.discard(permit.call_id)
                self._trip_open(bucket, now)
                return

            if bucket.state is CircuitState.CLOSED:
                bucket.consecutive_failures += 1
                if bucket.consecutive_failures >= self.failure_threshold:
                    self._trip_open(bucket, now)
                return

            # OPEN or non-active half-open origin: no-op.
