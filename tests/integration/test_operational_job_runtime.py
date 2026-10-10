from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from application.services.operational_job_runtime import (
    DurableOperationalJobRuntime,
    OperationalJobOutcome,
    OperationalJobRuntimeError,
)
from domain.common.errors import PersistenceError
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


class _DelegatingRepository:
    def __init__(self, repository: SqlAlchemyOperationalJobRepository) -> None:
        self.repository = repository
        self.finish_calls = 0
        self.get_calls = 0

    def claim(self, candidate: OperationalJobRun, *, now: datetime):
        return self.repository.claim(candidate, now=now)

    def heartbeat(
        self,
        job_run_id: str,
        *,
        lease_owner_hash: str,
        expected_version: int,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ):
        return self.repository.heartbeat(
            job_run_id,
            lease_owner_hash=lease_owner_hash,
            expected_version=expected_version,
            heartbeat_at=heartbeat_at,
            lease_expires_at=lease_expires_at,
        )

    def finish(
        self,
        job_run_id: str,
        *,
        lease_owner_hash: str,
        expected_version: int,
        status: str,
        result_code: str | None,
        error_code: str | None,
        completed_at: datetime,
    ):
        self.finish_calls += 1
        return self.repository.finish(
            job_run_id,
            lease_owner_hash=lease_owner_hash,
            expected_version=expected_version,
            status=status,
            result_code=result_code,
            error_code=error_code,
            completed_at=completed_at,
        )

    def get(self, job_run_id: str):
        self.get_calls += 1
        return self.repository.get(job_run_id)

    def get_by_key(self, job_name: str, idempotency_key: str):
        return self.repository.get_by_key(job_name, idempotency_key)

    def recover_expired(self, *, now: datetime, limit: int = 100):
        return self.repository.recover_expired(now=now, limit=limit)


class _HeartbeatFailureRepository(_DelegatingRepository):
    def __init__(self, repository: SqlAlchemyOperationalJobRepository) -> None:
        super().__init__(repository)
        self.heartbeat_calls = 0

    def heartbeat(self, *args, **kwargs):
        self.heartbeat_calls += 1
        if self.heartbeat_calls == 1:
            raise PersistenceError("secret=must-not-escape", code="DB_BUSY")
        return super().heartbeat(*args, **kwargs)


class _FinishAfterWriteFailureRepository(_DelegatingRepository):
    def finish(self, *args, **kwargs):
        self.finish_calls += 1
        self.repository.finish(*args, **kwargs)
        raise PersistenceError("write outcome is unknown; token=hidden", code="DB_BUSY")


class _FinishReceiptChangedRepository(_DelegatingRepository):
    def finish(self, *args, **kwargs):
        self.finish_calls += 1
        changed = dict(kwargs)
        changed["status"] = OperationalJobStatus.INTERRUPTED.value
        changed["result_code"] = None
        changed["error_code"] = "OPERATIONAL_JOB_HEARTBEAT_FAILED"
        self.repository.finish(args[0], **changed)
        raise PersistenceError("receipt changed after write", code="DB_BUSY")


class _FinishBeforeWriteFailureRepository(_DelegatingRepository):
    def finish(self, *args, **kwargs):
        self.finish_calls += 1
        raise PersistenceError("secret=must-not-escape", code="DB_BUSY")


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


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_and_drains_once_without_success(tmp_path: Path) -> None:
    repository = _HeartbeatFailureRepository(_repository(tmp_path))
    clock, ids = Clock(), IDs()
    runtime = DurableOperationalJobRuntime(
        repository,
        clock,
        ids,
        lease_owner="heartbeat-failure",
        heartbeat_interval_seconds=0.01,
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def operation() -> OperationalJobOutcome[str]:
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return OperationalJobOutcome(
            status=OperationalJobStatus.SUCCEEDED,
            result_code="MUST_NOT_BE_REPORTED",
            value="unknown",
        )

    execution = await runtime.execute(
        job_name="monitor.due",
        idempotency_key="heartbeat:fault",
        operation=operation,
        lease_seconds=30,
    )

    assert started.is_set()
    assert cancelled.is_set()
    assert calls == 1
    assert repository.heartbeat_calls == 1
    assert execution.run.status is OperationalJobStatus.INTERRUPTED
    assert execution.run.error_code == "OPERATIONAL_JOB_HEARTBEAT_FAILED"
    assert execution.run.result_code is None
    assert execution.value is None
    assert "must-not-escape" not in repr(execution.run)


@pytest.mark.asyncio
async def test_finish_failure_after_write_reads_known_receipt_without_replaying(
    tmp_path: Path,
) -> None:
    repository = _FinishAfterWriteFailureRepository(_repository(tmp_path))
    clock, ids = Clock(), IDs()
    runtime = DurableOperationalJobRuntime(repository, clock, ids, lease_owner="finish-known")
    calls = 0

    async def operation() -> OperationalJobOutcome[str]:
        nonlocal calls
        calls += 1
        return OperationalJobOutcome(
            status=OperationalJobStatus.SUCCEEDED,
            result_code="DONE",
            value="receipt-known",
        )

    execution = await runtime.execute(
        job_name="monitor.due",
        idempotency_key="finish:known",
        operation=operation,
    )

    assert calls == 1
    assert repository.finish_calls == 1
    assert repository.get_calls == 1
    assert execution.run.status is OperationalJobStatus.SUCCEEDED
    assert execution.run.result_code == "DONE"
    assert execution.value == "receipt-known"


@pytest.mark.asyncio
async def test_finish_failure_does_not_reuse_success_value_from_interrupted_receipt(
    tmp_path: Path,
) -> None:
    repository = _FinishReceiptChangedRepository(_repository(tmp_path))
    clock, ids = Clock(), IDs()
    runtime = DurableOperationalJobRuntime(repository, clock, ids, lease_owner="finish-changed")

    async def operation() -> OperationalJobOutcome[str]:
        return OperationalJobOutcome(
            status=OperationalJobStatus.SUCCEEDED,
            result_code="DONE",
            value="must-not-escape",
        )

    execution = await runtime.execute(
        job_name="monitor.due",
        idempotency_key="finish:changed",
        operation=operation,
    )

    assert execution.run.status is OperationalJobStatus.INTERRUPTED
    assert execution.run.error_code == "OPERATIONAL_JOB_HEARTBEAT_FAILED"
    assert execution.value is None
    assert repository.finish_calls == 1
    assert repository.get_calls == 1


@pytest.mark.asyncio
async def test_finish_failure_leaves_running_receipt_for_recovery_without_replay(
    tmp_path: Path,
) -> None:
    repository = _FinishBeforeWriteFailureRepository(_repository(tmp_path))
    clock, ids = Clock(), IDs()
    runtime = DurableOperationalJobRuntime(repository, clock, ids, lease_owner="finish-unknown")
    calls = 0

    async def operation() -> OperationalJobOutcome[None]:
        nonlocal calls
        calls += 1
        return OperationalJobOutcome(status=OperationalJobStatus.SUCCEEDED, result_code="DONE")

    with pytest.raises(OperationalJobRuntimeError) as raised:
        await runtime.execute(
            job_name="monitor.due",
            idempotency_key="finish:unknown",
            operation=operation,
        )

    assert raised.value.code == "OPERATIONAL_JOB_FINISH_FAILED"
    assert "must-not-escape" not in str(raised.value)
    assert calls == 1
    assert repository.finish_calls == 1
    assert repository.get_calls == 1
    running = await runtime.get_by_key("monitor.due", "finish:unknown")
    assert running is not None
    assert running.status is OperationalJobStatus.RUNNING

    clock.value += timedelta(seconds=91)
    assert await runtime.recover_expired() == 1
    recovered = await runtime.get_by_key("monitor.due", "finish:unknown")
    assert recovered is not None
    assert recovered.status is OperationalJobStatus.INTERRUPTED
    assert calls == 1


@pytest.mark.asyncio
async def test_external_cancellation_finishes_interrupted_then_reraises(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    clock, ids = Clock(), IDs()
    runtime = DurableOperationalJobRuntime(
        repository,
        clock,
        ids,
        lease_owner="external-cancel",
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def operation() -> OperationalJobOutcome[None]:
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(
        runtime.execute(
            job_name="monitor.due",
            idempotency_key="cancel:external",
            operation=operation,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()
    assert calls == 1
    receipt = await runtime.get_by_key("monitor.due", "cancel:external")
    assert receipt is not None
    assert receipt.status is OperationalJobStatus.INTERRUPTED
    assert receipt.error_code == "OPERATIONAL_JOB_CANCELLED"


@pytest.mark.asyncio
async def test_claim_callback_failure_closes_claim_without_invoking_operation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    clock, ids = Clock(), IDs()
    runtime = DurableOperationalJobRuntime(repository, clock, ids, lease_owner="claim-callback")
    calls = 0

    async def operation() -> OperationalJobOutcome[None]:
        nonlocal calls
        calls += 1
        return OperationalJobOutcome(status=OperationalJobStatus.SUCCEEDED, result_code="DONE")

    def on_claim(_: OperationalJobRun) -> None:
        raise RuntimeError("callback secret=must-not-escape")

    with pytest.raises(OperationalJobRuntimeError) as raised:
        await runtime.execute(
            job_name="monitor.due",
            idempotency_key="claim:callback-fault",
            operation=operation,
            on_claim=on_claim,
        )

    assert raised.value.code == "OPERATIONAL_JOB_CLAIM_CALLBACK_FAILED"
    assert "must-not-escape" not in str(raised.value)
    assert calls == 0
    receipt = await runtime.get_by_key("monitor.due", "claim:callback-fault")
    assert receipt is not None
    assert receipt.status is OperationalJobStatus.FAILED
    assert receipt.error_code == "OPERATIONAL_JOB_CLAIM_CALLBACK_FAILED"
