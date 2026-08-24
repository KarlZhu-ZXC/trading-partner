from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from application.services.operational_job_runtime import (
    DurableOperationalJobRuntime,
    OperationalJobOutcome,
)
from domain.common.ids import EntityIdPrefix
from domain.operations.enums import OperationalJobStatus
from domain.operations.models import OperationalJobRun
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.operational_job_repository import (
    SqlAlchemyOperationalJobRepository,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class IDs:
    def __init__(self) -> None:
        self.value = 0

    def new(self, prefix: EntityIdPrefix) -> str:
        self.value += 1
        return f"{prefix.value}_{self.value}"


def _repository(tmp_path: Path) -> SqlAlchemyOperationalJobRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    return SqlAlchemyOperationalJobRepository(engine)


def _candidate(clock: Clock, ids: IDs, *, key: str = "monitor:hour") -> OperationalJobRun:
    now = clock.now()
    return OperationalJobRun(
        job_run_id=ids.new(EntityIdPrefix.OPERATIONAL_JOB_RUN),
        job_name="monitor.due",
        idempotency_key=key,
        status=OperationalJobStatus.RUNNING,
        attempt=1,
        lease_owner_hash="a" * 32,
        lease_expires_at=now + timedelta(seconds=30),
        heartbeat_at=now,
        started_at=now,
        updated_at=now,
    )


def test_repository_claim_heartbeat_finish_and_retry(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    clock, ids = Clock(), IDs()
    first = repository.claim(_candidate(clock, ids), now=clock.now())
    assert first.claimed is True
    duplicate = repository.claim(_candidate(clock, ids), now=clock.now())
    assert duplicate.claimed is False
    assert duplicate.run.job_run_id == first.run.job_run_id

    clock.value += timedelta(seconds=5)
    heartbeat = repository.heartbeat(
        first.run.job_run_id,
        lease_owner_hash=first.run.lease_owner_hash,
        expected_version=first.run.version,
        heartbeat_at=clock.now(),
        lease_expires_at=clock.now() + timedelta(seconds=30),
    )
    assert heartbeat.version == 2
    terminal = repository.finish(
        heartbeat.job_run_id,
        lease_owner_hash=heartbeat.lease_owner_hash,
        expected_version=heartbeat.version,
        status=OperationalJobStatus.FAILED.value,
        result_code=None,
        error_code="MONITOR_DUE_FAILED",
        completed_at=clock.now(),
    )
    assert terminal.status is OperationalJobStatus.FAILED

    retried = repository.claim(_candidate(clock, ids), now=clock.now())
    assert retried.claimed is True
    assert retried.run.job_run_id == first.run.job_run_id
    assert retried.run.attempt == 2


def test_repository_recovers_expired_lease_without_exception_text(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    clock, ids = Clock(), IDs()
    claimed = repository.claim(_candidate(clock, ids), now=clock.now())
    clock.value += timedelta(minutes=1)
    assert repository.recover_expired(now=clock.now()) == 1
    recovered = repository.get(claimed.run.job_run_id)
    assert recovered is not None
    assert recovered.status is OperationalJobStatus.INTERRUPTED
    assert recovered.error_code == "OPERATIONAL_JOB_LEASE_EXPIRED"


def test_expired_claim_is_interrupted_before_a_later_retry(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    clock, ids = Clock(), IDs()
    first = repository.claim(_candidate(clock, ids), now=clock.now())
    clock.value += timedelta(minutes=1)

    interrupted = repository.claim(_candidate(clock, ids), now=clock.now())
    assert interrupted.claimed is False
    assert interrupted.run.status is OperationalJobStatus.INTERRUPTED
    assert interrupted.run.error_code == "OPERATIONAL_JOB_LEASE_EXPIRED"
    assert interrupted.run.job_run_id == first.run.job_run_id

    retried = repository.claim(_candidate(clock, ids), now=clock.now())
    assert retried.claimed is True
    assert retried.run.status is OperationalJobStatus.RUNNING
    assert retried.run.attempt == 2


@pytest.mark.asyncio
async def test_runtime_heartbeats_deduplicates_and_persists_safe_failure(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    clock, ids = Clock(), IDs()
    runtime = DurableOperationalJobRuntime(
        repository,
        clock,
        ids,
        lease_owner="runtime-test",
        heartbeat_interval_seconds=0.01,
    )
    calls = 0

    async def success() -> OperationalJobOutcome[str]:
        nonlocal calls
        calls += 1
        clock.value += timedelta(seconds=1)
        await asyncio.sleep(0.03)
        return OperationalJobOutcome(
            status=OperationalJobStatus.SUCCEEDED,
            result_code="MONITOR_DUE_SUCCEEDED",
            value="done",
        )

    first = await runtime.execute(
        job_name="monitor.due",
        idempotency_key="monitor:20260820T03",
        operation=success,
        lease_seconds=30,
    )
    replay = await runtime.execute(
        job_name="monitor.due",
        idempotency_key="monitor:20260820T03",
        operation=success,
        lease_seconds=30,
    )
    assert first.invoked is True and first.value == "done"
    assert first.run.version >= 3
    assert replay.invoked is False
    assert calls == 1

    async def failure() -> OperationalJobOutcome[None]:
        raise RuntimeError("api_key=must-not-persist")

    failed = await runtime.execute(
        job_name="monitor.due",
        idempotency_key="monitor:failure",
        operation=failure,
        lease_seconds=30,
    )
    assert failed.run.status is OperationalJobStatus.FAILED
    assert failed.run.error_code == "OPERATIONAL_JOB_EXECUTION_FAILED"
    assert "must-not-persist" not in repr(failed.run)
