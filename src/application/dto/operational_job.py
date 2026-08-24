"""Safe Operational Job runtime receipts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from domain.operations.models import OperationalJobRun


class OperationalJobRunDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_run_id: str
    job_name: str
    idempotency_key: str
    status: str
    attempt: int = Field(ge=1)
    lease_expires_at: datetime
    heartbeat_at: datetime
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    result_code: str | None
    error_code: str | None
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, value: OperationalJobRun) -> OperationalJobRunDTO:
        # lease_owner_hash is intentionally internal and never projected.
        return cls(
            job_run_id=value.job_run_id,
            job_name=value.job_name,
            idempotency_key=value.idempotency_key,
            status=value.status.value,
            attempt=value.attempt,
            lease_expires_at=value.lease_expires_at,
            heartbeat_at=value.heartbeat_at,
            started_at=value.started_at,
            updated_at=value.updated_at,
            completed_at=value.completed_at,
            result_code=value.result_code,
            error_code=value.error_code,
            version=value.version,
        )


__all__ = ["OperationalJobRunDTO"]
