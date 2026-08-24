"""Durable lease/CAS boundary for scheduled operational jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.operations.models import OperationalJobClaim, OperationalJobRun


class OperationalJobRepository(Protocol):
    def claim(self, candidate: OperationalJobRun, *, now: datetime) -> OperationalJobClaim: ...

    def heartbeat(
        self,
        job_run_id: str,
        *,
        lease_owner_hash: str,
        expected_version: int,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> OperationalJobRun: ...

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
    ) -> OperationalJobRun: ...

    def get(self, job_run_id: str) -> OperationalJobRun | None: ...

    def get_by_key(self, job_name: str, idempotency_key: str) -> OperationalJobRun | None: ...

    def recover_expired(self, *, now: datetime, limit: int = 100) -> int: ...
