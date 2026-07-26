"""Durable, idempotent workflow execution repository port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import JsonValue

from domain.workflow.models import WorkflowRun


@dataclass(frozen=True, slots=True)
class WorkflowRunRecord:
    run: WorkflowRun
    request_payload_sha256: str
    heartbeat_at: datetime
    lease_expires_at: datetime
    fact_data: tuple[JsonValue | None, ...] = ()
    missing_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowRunClaim:
    record: WorkflowRunRecord
    claimed: bool


class WorkflowRunRepository(Protocol):
    def claim(
        self,
        run: WorkflowRun,
        *,
        idempotency_key: str,
        request_payload_sha256: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRunClaim: ...

    def mark_running(
        self,
        run_id: str,
        *,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> None: ...

    def complete(
        self,
        run: WorkflowRun,
        *,
        fact_data: tuple[JsonValue | None, ...],
        missing_capabilities: tuple[str, ...],
    ) -> WorkflowRunRecord: ...

    def get_record(self, run_id: str) -> WorkflowRunRecord: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRunRecord | None: ...

    def get(self, run_id: str) -> WorkflowRun: ...
