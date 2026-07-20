"""Phase 1D D5b: thread-safe CircuitBreaker state machine (permit-based)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import pytest

from application.dto.provider_resilience import CircuitCallPermit
from conftest import FixedClock
from domain.common.enums import CircuitState, DataCategory, VendorId
from domain.common.errors import DataContractError, ProviderUnavailableError
from infrastructure.providers.common.circuit_breaker import CircuitBreaker

V = VendorId.EASTMONEY
C = DataCategory.MARKET_QUOTE
V2 = VendorId.YFINANCE
C2 = DataCategory.NEWS


def _breaker(
    clock: FixedClock,
    *,
    threshold: int = 5,
    recovery: float = 60.0,
    half_open: int = 1,
) -> CircuitBreaker:
    return CircuitBreaker(
        clock,
        failure_threshold=threshold,
        recovery_timeout_seconds=recovery,
        half_open_max_calls=half_open,
    )


def test_starts_closed() -> None:
    clock = FixedClock()
    cb = _breaker(clock)
    assert cb.state(V, C) is CircuitState.CLOSED
    permit = cb.before_call(V, C)
    assert permit.half_open_generation is None
    cb.record_success(permit)
    assert cb.state(V, C) is CircuitState.CLOSED


def test_consecutive_failures_open_circuit() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=3)
    for _ in range(2):
        permit = cb.before_call(V, C)
        cb.record_failure(permit)
        assert cb.state(V, C) is CircuitState.CLOSED
    permit = cb.before_call(V, C)
    cb.record_failure(permit)
    assert cb.state(V, C) is CircuitState.OPEN


def test_success_resets_consecutive_failures() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=3)
    cb.record_failure(cb.before_call(V, C))
    cb.record_failure(cb.before_call(V, C))
    cb.record_success(cb.before_call(V, C))
    cb.record_failure(cb.before_call(V, C))
    assert cb.state(V, C) is CircuitState.CLOSED


def test_open_rejects_before_recovery() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=60.0)
    cb.record_failure(cb.before_call(V, C))
    assert cb.state(V, C) is CircuitState.OPEN
    with pytest.raises(ProviderUnavailableError) as exc_info:
        cb.before_call(V, C)
    err = exc_info.value
    assert err.code == "PROVIDER_UNAVAILABLE_ERROR"
    assert err.details == {
        "vendor": V.value,
        "category": C.value,
        "circuit_state": CircuitState.OPEN.value,
    }
    assert err.__cause__ is None
    assert "api_key" not in str(err.details).lower()


def test_state_transitions_open_to_half_open_without_probe() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=10.0, half_open=1)
    cb.record_failure(cb.before_call(V, C))
    assert cb.state(V, C) is CircuitState.OPEN

    clock.advance(10)
    # state() must transition but not reserve a probe.
    assert cb.state(V, C) is CircuitState.HALF_OPEN
    # Still one probe available.
    cb.before_call(V, C)
    with pytest.raises(ProviderUnavailableError) as exc_info:
        cb.before_call(V, C)
    assert exc_info.value.details["circuit_state"] == CircuitState.HALF_OPEN.value


def test_half_open_success_closes() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=5.0)
    cb.record_failure(cb.before_call(V, C))
    clock.advance(5)
    probe = cb.before_call(V, C)
    assert probe.half_open_generation == 1
    cb.record_success(probe)
    assert cb.state(V, C) is CircuitState.CLOSED
    closed = cb.before_call(V, C)
    assert closed.half_open_generation is None
    cb.record_success(closed)


def test_half_open_failure_reopens() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=5.0)
    cb.record_failure(cb.before_call(V, C))
    clock.advance(5)
    probe = cb.before_call(V, C)
    reopen_at = clock.now()
    cb.record_failure(probe)
    assert cb.state(V, C) is CircuitState.OPEN
    # Recovery clock resets: need another full recovery from reopen_at.
    clock.set(reopen_at + timedelta(seconds=4))
    with pytest.raises(ProviderUnavailableError):
        cb.before_call(V, C)
    clock.set(reopen_at + timedelta(seconds=5))
    assert cb.state(V, C) is CircuitState.HALF_OPEN


def test_half_open_probe_release_on_success_allows_new_cycle() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=1.0, half_open=1)
    cb.record_failure(cb.before_call(V, C))
    clock.advance(1)
    first_probe = cb.before_call(V, C)
    assert first_probe.half_open_generation == 1
    cb.record_success(first_probe)
    # Fail again → open → recover → probe again with next generation.
    cb.record_failure(cb.before_call(V, C))
    clock.advance(1)
    second_probe = cb.before_call(V, C)
    assert second_probe.half_open_generation == 2
    cb.record_success(second_probe)
    assert cb.state(V, C) is CircuitState.CLOSED


def test_half_open_probe_release_on_failure() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=1.0, half_open=2)
    cb.record_failure(cb.before_call(V, C))
    clock.advance(1)
    p1 = cb.before_call(V, C)
    p2 = cb.before_call(V, C)
    with pytest.raises(ProviderUnavailableError):
        cb.before_call(V, C)
    cb.record_failure(p1)
    assert cb.state(V, C) is CircuitState.OPEN
    # Invalidated sibling is a no-op.
    cb.record_success(p2)
    assert cb.state(V, C) is CircuitState.OPEN


def test_half_open_success_then_sibling_failure_reopens() -> None:
    """half_open_max_calls=2: success then sibling failure must end OPEN."""
    clock = FixedClock()
    cb = _breaker(clock, threshold=5, recovery=1.0, half_open=2)
    for _ in range(5):
        cb.record_failure(cb.before_call(V, C))
    assert cb.state(V, C) is CircuitState.OPEN

    clock.advance(1)
    p1 = cb.before_call(V, C)
    p2 = cb.before_call(V, C)
    assert p1.half_open_generation == p2.half_open_generation == 1
    assert cb.state(V, C) is CircuitState.HALF_OPEN

    reopen_at = clock.now()
    cb.record_success(p1)
    assert cb.state(V, C) is CircuitState.CLOSED

    cb.record_failure(p2)
    assert cb.state(V, C) is CircuitState.OPEN

    clock.set(reopen_at + timedelta(seconds=0.5))
    with pytest.raises(ProviderUnavailableError) as exc_info:
        cb.before_call(V, C)
    assert exc_info.value.details["circuit_state"] == CircuitState.OPEN.value
    clock.set(reopen_at + timedelta(seconds=1.0))
    assert cb.state(V, C) is CircuitState.HALF_OPEN


def test_half_open_failure_then_sibling_success_stays_open() -> None:
    """half_open_max_calls=2: failure then late success must remain OPEN."""
    clock = FixedClock()
    cb = _breaker(clock, threshold=5, recovery=1.0, half_open=2)
    for _ in range(5):
        cb.record_failure(cb.before_call(V, C))
    assert cb.state(V, C) is CircuitState.OPEN

    clock.advance(1)
    p1 = cb.before_call(V, C)
    p2 = cb.before_call(V, C)
    assert cb.state(V, C) is CircuitState.HALF_OPEN

    reopen_at = clock.now()
    cb.record_failure(p1)
    assert cb.state(V, C) is CircuitState.OPEN

    # Late success from the other admitted probe — must not close.
    cb.record_success(p2)
    assert cb.state(V, C) is CircuitState.OPEN

    clock.set(reopen_at + timedelta(seconds=0.5))
    with pytest.raises(ProviderUnavailableError) as exc_info:
        cb.before_call(V, C)
    assert exc_info.value.details["circuit_state"] == CircuitState.OPEN.value
    clock.set(reopen_at + timedelta(seconds=1.0))
    assert cb.state(V, C) is CircuitState.HALF_OPEN


def test_success_then_normal_failure_then_old_sibling_success() -> None:
    """Half-open success → new CLOSED failure preserved; late sibling success no reset.

    Regression: after p1 closes, a normal CLOSED failure (count=1) must not be
    wiped by a late p2 half-open success. p2 only releases its permit. With
    threshold=5, four more normal failures must open on the fourth (proving
    the first failure was preserved). Duplicate/stale p2 recordings stay no-ops.
    """
    clock = FixedClock()
    cb = _breaker(clock, threshold=5, recovery=1.0, half_open=2)
    for _ in range(5):
        cb.record_failure(cb.before_call(V, C))
    clock.advance(1)

    p1 = cb.before_call(V, C)
    p2 = cb.before_call(V, C)
    assert p1.half_open_generation == 1
    assert p2.half_open_generation == 1

    cb.record_success(p1)
    assert cb.state(V, C) is CircuitState.CLOSED

    normal = cb.before_call(V, C)
    assert normal.half_open_generation is None
    cb.record_failure(normal)
    # threshold=5 → one normal failure stays CLOSED (not probe reopen).
    assert cb.state(V, C) is CircuitState.CLOSED

    cb.record_success(p2)
    assert cb.state(V, C) is CircuitState.CLOSED
    # Duplicate / already-released p2 must not reset the preserved count.
    cb.record_success(p2)
    cb.record_failure(p2)
    assert cb.state(V, C) is CircuitState.CLOSED

    # consecutive_failures still 1 → need 4 more normal failures to open.
    for i in range(4):
        permit = cb.before_call(V, C)
        assert permit.half_open_generation is None
        cb.record_failure(permit)
        if i < 3:
            assert cb.state(V, C) is CircuitState.CLOSED
        else:
            assert cb.state(V, C) is CircuitState.OPEN


def test_success_then_normal_failure_then_old_sibling_failure_reopens() -> None:
    """Half-open success → normal CLOSED failure → old sibling failure reopens."""
    clock = FixedClock()
    cb = _breaker(clock, threshold=5, recovery=1.0, half_open=2)
    for _ in range(5):
        cb.record_failure(cb.before_call(V, C))
    clock.advance(1)

    p1 = cb.before_call(V, C)
    p2 = cb.before_call(V, C)
    cb.record_success(p1)
    assert cb.state(V, C) is CircuitState.CLOSED

    normal = cb.before_call(V, C)
    assert normal.half_open_generation is None
    reopen_at = clock.now()
    cb.record_failure(normal)
    assert cb.state(V, C) is CircuitState.CLOSED

    cb.record_failure(p2)
    assert cb.state(V, C) is CircuitState.OPEN

    clock.set(reopen_at + timedelta(seconds=0.5))
    with pytest.raises(ProviderUnavailableError) as exc_info:
        cb.before_call(V, C)
    assert exc_info.value.details["circuit_state"] == CircuitState.OPEN.value
    clock.set(reopen_at + timedelta(seconds=1.0))
    assert cb.state(V, C) is CircuitState.HALF_OPEN


def test_failure_then_late_sibling_success_remains_open() -> None:
    """Explicit: failure-first OPEN; late sibling success is a no-op."""
    clock = FixedClock()
    cb = _breaker(clock, threshold=5, recovery=1.0, half_open=2)
    for _ in range(5):
        cb.record_failure(cb.before_call(V, C))
    clock.advance(1)
    p1 = cb.before_call(V, C)
    p2 = cb.before_call(V, C)
    cb.record_failure(p1)
    assert cb.state(V, C) is CircuitState.OPEN
    cb.record_success(p2)
    assert cb.state(V, C) is CircuitState.OPEN
    # Late sibling failure also no-op (already invalidated).
    cb.record_failure(p2)
    assert cb.state(V, C) is CircuitState.OPEN


def test_duplicate_recordings_are_noop() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=3, recovery=1.0, half_open=2)

    closed = cb.before_call(V, C)
    cb.record_success(closed)
    cb.record_success(closed)  # duplicate
    cb.record_failure(closed)  # duplicate
    assert cb.state(V, C) is CircuitState.CLOSED

    # Two failures then open on third — duplicates must not inflate counters.
    cb.record_failure(cb.before_call(V, C))
    p = cb.before_call(V, C)
    cb.record_failure(p)
    cb.record_failure(p)  # duplicate of second failure
    assert cb.state(V, C) is CircuitState.CLOSED
    cb.record_failure(cb.before_call(V, C))
    assert cb.state(V, C) is CircuitState.OPEN

    clock.advance(1)
    probe_a = cb.before_call(V, C)
    probe_b = cb.before_call(V, C)
    cb.record_success(probe_a)
    assert cb.state(V, C) is CircuitState.CLOSED
    cb.record_success(probe_a)  # duplicate half-open success
    assert cb.state(V, C) is CircuitState.CLOSED
    cb.record_failure(probe_b)
    assert cb.state(V, C) is CircuitState.OPEN
    cb.record_failure(probe_b)  # duplicate after invalidate path
    assert cb.state(V, C) is CircuitState.OPEN


def test_permit_fields_generation_and_call_ids() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=1.0, half_open=2)

    c1 = cb.before_call(V, C)
    c2 = cb.before_call(V, C)
    assert c1 == CircuitCallPermit(vendor=V, category=C, call_id=1, half_open_generation=None)
    assert c2 == CircuitCallPermit(vendor=V, category=C, call_id=2, half_open_generation=None)
    assert c1.call_id != c2.call_id
    cb.record_failure(c1)
    assert cb.state(V, C) is CircuitState.OPEN
    # c2 was closed-origin; still outstanding until recorded — not a probe.
    cb.record_success(c2)
    assert cb.state(V, C) is CircuitState.OPEN

    clock.advance(1)
    h1 = cb.before_call(V, C)
    h2 = cb.before_call(V, C)
    assert h1.half_open_generation == 1
    assert h2.half_open_generation == 1
    assert h1.call_id != h2.call_id
    assert {h1.call_id, h2.call_id} == {3, 4}
    cb.record_success(h1)
    assert cb.state(V, C) is CircuitState.CLOSED
    closed_after = cb.before_call(V, C)
    assert closed_after.half_open_generation is None
    assert closed_after.call_id == 5

    # Next open→half-open generation increments.
    cb.record_failure(closed_after)
    assert cb.state(V, C) is CircuitState.OPEN
    # Sibling half-open still active — its failure reopens (already open) / invalidate.
    # Use a fresh cycle after recovery.
    clock.advance(1)
    gen2_a = cb.before_call(V, C)
    assert gen2_a.half_open_generation == 2
    cb.record_failure(gen2_a)
    clock.advance(1)
    gen3 = cb.before_call(V, C)
    assert gen3.half_open_generation == 3

    # Isolation: other key has its own call_id sequence and generation.
    other = cb.before_call(V2, C)
    assert other == CircuitCallPermit(vendor=V2, category=C, call_id=1, half_open_generation=None)


def test_circuit_call_permit_validation() -> None:
    with pytest.raises(DataContractError, match="call_id"):
        CircuitCallPermit(vendor=V, category=C, call_id=0, half_open_generation=None)
    with pytest.raises(DataContractError, match="call_id"):
        CircuitCallPermit(vendor=V, category=C, call_id=-1, half_open_generation=None)
    with pytest.raises(DataContractError, match="half_open_generation"):
        CircuitCallPermit(vendor=V, category=C, call_id=1, half_open_generation=0)
    with pytest.raises(DataContractError, match="half_open_generation"):
        CircuitCallPermit(vendor=V, category=C, call_id=1, half_open_generation=-3)
    ok = CircuitCallPermit(vendor=V, category=C, call_id=1, half_open_generation=None)
    assert ok.half_open_generation is None
    ok2 = CircuitCallPermit(vendor=V, category=C, call_id=2, half_open_generation=1)
    assert ok2.half_open_generation == 1


def test_key_isolation() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1)
    cb.record_failure(cb.before_call(V, C))
    assert cb.state(V, C) is CircuitState.OPEN
    assert cb.state(V2, C) is CircuitState.CLOSED
    assert cb.state(V, C2) is CircuitState.CLOSED
    cb.record_success(cb.before_call(V2, C))


def test_constructor_validation() -> None:
    clock = FixedClock()
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker(clock, failure_threshold=0)
    with pytest.raises(ValueError, match="recovery_timeout"):
        CircuitBreaker(clock, recovery_timeout_seconds=0)
    with pytest.raises(ValueError, match="half_open"):
        CircuitBreaker(clock, half_open_max_calls=0)


def test_half_open_concurrency_probe_limit() -> None:
    clock = FixedClock()
    cb = _breaker(clock, threshold=1, recovery=1.0, half_open=1)
    cb.record_failure(cb.before_call(V, C))
    clock.advance(1)

    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def _try() -> None:
        barrier.wait(timeout=5)
        try:
            cb.before_call(V, C)
            with lock:
                outcomes.append("admit")
        except ProviderUnavailableError:
            with lock:
                outcomes.append("reject")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_try) for _ in range(8)]
        for f in as_completed(futs):
            f.result()

    assert outcomes.count("admit") == 1
    assert outcomes.count("reject") == 7


def test_thread_stress_mixed_keys() -> None:
    """Hard exit: concurrent traffic across keys must not corrupt state."""
    clock = FixedClock(datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC))
    cb = _breaker(clock, threshold=3, recovery=1000.0)
    vendors = list(VendorId)[:6]
    categories = [
        DataCategory.MARKET_QUOTE,
        DataCategory.MARKET_OHLCV,
        DataCategory.NEWS,
    ]
    n = 40
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def _worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            vendor = vendors[i % len(vendors)]
            category = categories[i % len(categories)]
            for _ in range(20):
                try:
                    permit = cb.before_call(vendor, category)
                except ProviderUnavailableError:
                    continue
                if i % 3 == 0:
                    cb.record_failure(permit)
                else:
                    cb.record_success(permit)
                _ = cb.state(vendor, category)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(_worker, i) for i in range(n)]
        for f in as_completed(futs):
            f.result(timeout=30)

    assert errors == [], f"thread stress errors: {errors!r}"
    for vendor in vendors:
        for category in categories:
            assert cb.state(vendor, category) in {
                CircuitState.CLOSED,
                CircuitState.OPEN,
                CircuitState.HALF_OPEN,
            }


def test_export() -> None:
    from application.dto import CircuitCallPermit as CCP
    from infrastructure.providers.common import CircuitBreaker as CB

    assert CB is CircuitBreaker
    assert CCP is CircuitCallPermit
