"""SQLAlchemy durable Operational Job lease and terminal receipt repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.operational_job_repository import OperationalJobRepository
from domain.common.errors import PersistenceError
from domain.common.time import require_aware_datetime
from domain.operations.enums import OperationalJobStatus
from domain.operations.models import OperationalJobClaim, OperationalJobRun
from infrastructure.persistence.orm import OperationalJobRunRow


def _domain(row: OperationalJobRunRow) -> OperationalJobRun:
    return OperationalJobRun(
        job_run_id=row.job_run_id,
        job_name=row.job_name,
        idempotency_key=row.idempotency_key,
        status=OperationalJobStatus(row.status),
        attempt=row.attempt,
        lease_owner_hash=row.lease_owner_hash,
        lease_expires_at=datetime.fromisoformat(row.lease_expires_at),
        heartbeat_at=datetime.fromisoformat(row.heartbeat_at),
        started_at=datetime.fromisoformat(row.started_at),
        updated_at=datetime.fromisoformat(row.updated_at),
        completed_at=(datetime.fromisoformat(row.completed_at) if row.completed_at else None),
        result_code=row.result_code,
        error_code=row.error_code,
        version=row.version,
    )


class SqlAlchemyOperationalJobRepository(OperationalJobRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim(self, candidate: OperationalJobRun, *, now: datetime) -> OperationalJobClaim:
        timestamp = require_aware_datetime(now, field_name="now")
        if candidate.status is not OperationalJobStatus.RUNNING:
            raise PersistenceError("Operational Job claim requires RUNNING candidate")
        with Session(self._engine) as session:
            try:
                with session.begin():
                    row = session.scalar(
                        select(OperationalJobRunRow).where(
                            OperationalJobRunRow.job_name == candidate.job_name,
                            OperationalJobRunRow.idempotency_key == candidate.idempotency_key,
                        )
                    )
                    if row is None:
                        session.add(
                            OperationalJobRunRow(
                                job_run_id=candidate.job_run_id,
                                job_name=candidate.job_name,
                                idempotency_key=candidate.idempotency_key,
                                status=candidate.status.value,
                                attempt=1,
                                lease_owner_hash=candidate.lease_owner_hash,
                                lease_expires_at=candidate.lease_expires_at.isoformat(),
                                heartbeat_at=candidate.heartbeat_at.isoformat(),
                                started_at=candidate.started_at.isoformat(),
                                updated_at=candidate.updated_at.isoformat(),
                                completed_at=None,
                                result_code=None,
                                error_code=None,
                                version=1,
                            )
                        )
                        return OperationalJobClaim(run=candidate, claimed=True)
                    existing = _domain(row)
                    if existing.status in {
                        OperationalJobStatus.SUCCEEDED,
                        OperationalJobStatus.SKIPPED,
                    }:
                        return OperationalJobClaim(run=existing, claimed=False)
                    if existing.status is OperationalJobStatus.RUNNING:
                        if existing.lease_expires_at > timestamp:
                            return OperationalJobClaim(run=existing, claimed=False)
                        # Surface an expired lease as a durable terminal state for
                        # at least one dispatcher cycle.  In particular, a crashed
                        # order-adjacent job must never be taken over in the same
                        # call that first discovers its unknown outcome.
                        row.status = OperationalJobStatus.INTERRUPTED.value
                        row.completed_at = timestamp.isoformat()
                        row.updated_at = timestamp.isoformat()
                        row.heartbeat_at = timestamp.isoformat()
                        row.lease_expires_at = timestamp.isoformat()
                        row.result_code = None
                        row.error_code = "OPERATIONAL_JOB_LEASE_EXPIRED"
                        row.version = existing.version + 1
                        session.flush()
                        return OperationalJobClaim(run=_domain(row), claimed=False)
                    row.status = OperationalJobStatus.RUNNING.value
                    row.attempt = existing.attempt + 1
                    row.lease_owner_hash = candidate.lease_owner_hash
                    row.lease_expires_at = candidate.lease_expires_at.isoformat()
                    row.heartbeat_at = timestamp.isoformat()
                    row.started_at = timestamp.isoformat()
                    row.updated_at = timestamp.isoformat()
                    row.completed_at = None
                    row.result_code = None
                    row.error_code = None
                    row.version = existing.version + 1
                    session.flush()
                    return OperationalJobClaim(run=_domain(row), claimed=True)
            except IntegrityError:
                session.rollback()
        raced = self.get_by_key(candidate.job_name, candidate.idempotency_key)
        if raced is None:
            raise PersistenceError("Operational Job claim conflict")
        return OperationalJobClaim(run=raced, claimed=False)

    def heartbeat(
        self,
        job_run_id: str,
        *,
        lease_owner_hash: str,
        expected_version: int,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> OperationalJobRun:
        heartbeat = require_aware_datetime(heartbeat_at, field_name="heartbeat_at")
        lease = require_aware_datetime(lease_expires_at, field_name="lease_expires_at")
        with Session(self._engine) as session, session.begin():
            result = session.execute(
                update(OperationalJobRunRow)
                .where(
                    OperationalJobRunRow.job_run_id == job_run_id,
                    OperationalJobRunRow.status == OperationalJobStatus.RUNNING.value,
                    OperationalJobRunRow.lease_owner_hash == lease_owner_hash,
                    OperationalJobRunRow.version == expected_version,
                )
                .values(
                    heartbeat_at=heartbeat.isoformat(),
                    lease_expires_at=lease.isoformat(),
                    updated_at=heartbeat.isoformat(),
                    version=expected_version + 1,
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise PersistenceError(
                    "Operational Job heartbeat conflict",
                    code="OPERATIONAL_JOB_CAS_CONFLICT",
                )
        value = self.get(job_run_id)
        assert value is not None
        return value

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
    ) -> OperationalJobRun:
        terminal = OperationalJobStatus(status)
        if terminal is OperationalJobStatus.RUNNING:
            raise PersistenceError("Operational Job finish requires terminal status")
        completed = require_aware_datetime(completed_at, field_name="completed_at")
        with Session(self._engine) as session, session.begin():
            result = session.execute(
                update(OperationalJobRunRow)
                .where(
                    OperationalJobRunRow.job_run_id == job_run_id,
                    OperationalJobRunRow.status == OperationalJobStatus.RUNNING.value,
                    OperationalJobRunRow.lease_owner_hash == lease_owner_hash,
                    OperationalJobRunRow.version == expected_version,
                )
                .values(
                    status=terminal.value,
                    heartbeat_at=completed.isoformat(),
                    lease_expires_at=completed.isoformat(),
                    updated_at=completed.isoformat(),
                    completed_at=completed.isoformat(),
                    result_code=result_code,
                    error_code=error_code,
                    version=expected_version + 1,
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise PersistenceError(
                    "Operational Job finish conflict",
                    code="OPERATIONAL_JOB_CAS_CONFLICT",
                )
        value = self.get(job_run_id)
        assert value is not None
        return value

    def get(self, job_run_id: str) -> OperationalJobRun | None:
        with Session(self._engine) as session:
            row = session.get(OperationalJobRunRow, job_run_id)
            return _domain(row) if row is not None else None

    def get_by_key(self, job_name: str, idempotency_key: str) -> OperationalJobRun | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(OperationalJobRunRow).where(
                    OperationalJobRunRow.job_name == job_name,
                    OperationalJobRunRow.idempotency_key == idempotency_key,
                )
            )
            return _domain(row) if row is not None else None

    def recover_expired(self, *, now: datetime, limit: int = 100) -> int:
        timestamp = require_aware_datetime(now, field_name="now")
        bounded = max(1, min(limit, 500))
        with Session(self._engine) as session, session.begin():
            active_rows = tuple(
                session.scalars(
                    select(OperationalJobRunRow)
                    .where(
                        OperationalJobRunRow.status == OperationalJobStatus.RUNNING.value,
                    )
                )
            )
            rows = tuple(
                sorted(
                    (
                        row
                        for row in active_rows
                        if datetime.fromisoformat(row.lease_expires_at) <= timestamp
                    ),
                    key=lambda row: datetime.fromisoformat(row.lease_expires_at),
                )[:bounded]
            )
            for row in rows:
                row.status = OperationalJobStatus.INTERRUPTED.value
                row.completed_at = timestamp.isoformat()
                row.updated_at = timestamp.isoformat()
                row.heartbeat_at = timestamp.isoformat()
                row.lease_expires_at = timestamp.isoformat()
                row.error_code = "OPERATIONAL_JOB_LEASE_EXPIRED"
                row.version += 1
            return len(rows)


__all__ = ["SqlAlchemyOperationalJobRepository"]
