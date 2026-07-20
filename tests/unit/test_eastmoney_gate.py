"""Phase 1E E2: EastmoneyRequestGate singleton / serialization / cancellation."""

from __future__ import annotations

import asyncio

import pytest

from domain.common.errors import ConfigurationError
from infrastructure.providers.a_share.eastmoney_gate import (
    EastmoneyRequestGate,
    _reset_production_eastmoney_request_gate_for_tests,
    create_isolated_eastmoney_request_gate_for_tests,
    get_production_eastmoney_request_gate,
)


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture(autouse=True)
def _reset_production_gate() -> None:
    _reset_production_eastmoney_request_gate_for_tests()
    yield
    _reset_production_eastmoney_request_gate_for_tests()


def test_direct_construction_forbidden() -> None:
    with pytest.raises(ConfigurationError) as exc:
        EastmoneyRequestGate()
    assert exc.value.details.get("rule") == "construction_path"


def test_production_singleton_same_object() -> None:
    a = get_production_eastmoney_request_gate(min_interval_seconds=1.0, jitter_seconds=0.25)
    b = get_production_eastmoney_request_gate(min_interval_seconds=1.0, jitter_seconds=0.25)
    assert a is b
    assert a.min_interval_seconds == 1.0


def test_production_singleton_rejects_config_drift() -> None:
    get_production_eastmoney_request_gate(min_interval_seconds=1.0, jitter_seconds=0.25)
    with pytest.raises(ConfigurationError) as exc:
        get_production_eastmoney_request_gate(min_interval_seconds=9.0, jitter_seconds=0.25)
    assert exc.value.details.get("rule") == "production_config_drift"
    with pytest.raises(ConfigurationError) as exc2:
        get_production_eastmoney_request_gate(min_interval_seconds=1.0, jitter_seconds=0.5)
    assert exc2.value.details.get("rule") == "production_config_drift"


@pytest.mark.asyncio
async def test_production_refs_share_concurrency_state() -> None:
    gate_a = get_production_eastmoney_request_gate(min_interval_seconds=0.01, jitter_seconds=0.0)
    gate_b = get_production_eastmoney_request_gate(min_interval_seconds=0.01, jitter_seconds=0.0)
    assert gate_a is gate_b

    in_flight = 0
    max_in_flight = 0

    async def op() -> str:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return "ok"

    results = await asyncio.gather(
        gate_a.run(op),
        gate_b.run(op),
        gate_a.run(op),
    )
    assert results == ["ok", "ok", "ok"]
    assert max_in_flight == 1
    assert gate_a.max_observed_in_flight == 1


@pytest.mark.asyncio
async def test_global_concurrency_is_one() -> None:
    clock = _FakeClock()

    async def sleep(dt: float) -> None:
        clock.advance(dt)

    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.01,
        jitter_seconds=0.0,
        clock=clock,
        sleep=sleep,
        random_func=lambda: 0.0,
    )
    in_flight = 0
    max_in_flight = 0

    async def op(delay: float) -> str:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)
        clock.advance(delay)
        in_flight -= 1
        return "ok"

    async def run_one() -> str:
        return await gate.run(lambda: op(0.0))

    results = await asyncio.gather(run_one(), run_one(), run_one())
    assert results == ["ok", "ok", "ok"]
    assert max_in_flight == 1
    assert gate.max_observed_in_flight == 1


@pytest.mark.asyncio
async def test_minimum_start_interval_enforced() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []

    async def sleep(dt: float) -> None:
        sleeps.append(dt)
        clock.advance(dt)

    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=1.0,
        jitter_seconds=0.0,
        clock=clock,
        sleep=sleep,
        random_func=lambda: 0.0,
    )

    async def quick() -> int:
        return 1

    await gate.run(quick)
    await gate.run(quick)
    assert sleeps
    assert sleeps[0] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_jitter_bounded_and_deterministic() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []

    async def sleep(dt: float) -> None:
        sleeps.append(dt)
        clock.advance(dt)

    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=1.0,
        jitter_seconds=0.25,
        clock=clock,
        sleep=sleep,
        random_func=lambda: 0.5,
    )

    async def quick() -> None:
        return None

    await gate.run(quick)
    await gate.run(quick)
    assert sleeps[0] == pytest.approx(1.125)


@pytest.mark.asyncio
async def test_cancellation_while_waiting_releases_lock() -> None:
    clock = _FakeClock()
    block = asyncio.Event()

    async def hang_sleep(dt: float) -> None:
        await block.wait()

    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=1.0,
        jitter_seconds=0.0,
        clock=clock,
        sleep=hang_sleep,
        random_func=lambda: 0.0,
    )

    async def quick() -> str:
        return "done"

    await gate.run(quick)
    last_after_first = gate.last_start

    task = asyncio.create_task(gate.run(quick))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancellation during wait must not update last_start.
    assert gate.last_start == last_after_first

    async def sleep2(dt: float) -> None:
        clock.advance(dt)

    gate._sleep = sleep2  # type: ignore[method-assign]
    result = await gate.run(quick)
    assert result == "done"


@pytest.mark.asyncio
async def test_cancellation_during_operation_keeps_interval() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []

    async def sleep(dt: float) -> None:
        sleeps.append(dt)
        clock.advance(dt)

    gate = create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=1.0,
        jitter_seconds=0.0,
        clock=clock,
        sleep=sleep,
        random_func=lambda: 0.0,
    )

    started = asyncio.Event()

    async def long_op() -> str:
        started.set()
        await asyncio.sleep(3600)
        return "never"

    task = asyncio.create_task(gate.run(long_op))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # last_start was recorded; next call still waits interval.
    async def quick() -> str:
        return "ok"

    await gate.run(quick)
    assert sleeps
    assert sleeps[0] == pytest.approx(1.0)
