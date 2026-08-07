"""SQLAlchemy durable, idempotent workflow execution repository."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import cast

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.workflow_run_repository import (
    WorkflowRunClaim,
    WorkflowRunRecord,
)
from domain.common.errors import IdempotencyConflict, WorkflowRunNotFound
from domain.workflow.enums import WorkflowRunStatus, WorkflowType
from domain.workflow.models import WorkflowRun, WorkflowStepReceipt
from infrastructure.persistence.orm import (
    WorkflowRunFactArtifactRow,
    WorkflowRunRow,
    WorkflowRunStepRow,
)

_ARTIFACT_LIMIT_BYTES = 1_048_576
_TOTAL_ARTIFACT_LIMIT_BYTES = 8_388_608


class SqlAlchemyWorkflowRunRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim(
        self,
        run: WorkflowRun,
        *,
        idempotency_key: str,
        request_payload_sha256: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRunClaim:
        existing = self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return self._existing_claim(
                existing,
                request_payload_sha256,
                heartbeat_at=heartbeat_at,
                lease_expires_at=lease_expires_at,
            )
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    WorkflowRunRow(
                        run_id=run.run_id,
                        workflow_type=run.workflow_type.value,
                        subject_id=run.subject_id,
                        instrument_id=run.instrument_id,
                        requested_as_of=run.requested_as_of.isoformat(),
                        started_at=run.started_at.isoformat(),
                        completed_at=None,
                        status=WorkflowRunStatus.STARTED.value,
                        report_id=None,
                        idempotency_key=idempotency_key,
                        request_payload_sha256=request_payload_sha256,
                        heartbeat_at=heartbeat_at.isoformat(),
                        lease_expires_at=lease_expires_at.isoformat(),
                        missing_capabilities=(),
                    )
                )
        except IntegrityError:
            raced = self.get_by_idempotency_key(idempotency_key)
            if raced is None:
                raise
            return self._existing_claim(
                raced,
                request_payload_sha256,
                heartbeat_at=heartbeat_at,
                lease_expires_at=lease_expires_at,
            )
        return WorkflowRunClaim(
            record=WorkflowRunRecord(
                run=run,
                request_payload_sha256=request_payload_sha256,
                heartbeat_at=heartbeat_at,
                lease_expires_at=lease_expires_at,
            ),
            claimed=True,
        )

    def _existing_claim(
        self,
        existing: WorkflowRunRecord,
        request_payload_sha256: str,
        *,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> WorkflowRunClaim:
        if existing.request_payload_sha256 != request_payload_sha256:
            raise IdempotencyConflict("Workflow idempotency key was reused")
        if (
            existing.run.status in {WorkflowRunStatus.STARTED, WorkflowRunStatus.RUNNING}
            and existing.lease_expires_at <= heartbeat_at
        ):
            with Session(self._engine) as session, session.begin():
                row = session.get(WorkflowRunRow, existing.run.run_id)
                if row is not None and datetime.fromisoformat(row.lease_expires_at) <= heartbeat_at:
                    row.heartbeat_at = heartbeat_at.isoformat()
                    row.lease_expires_at = lease_expires_at.isoformat()
                    row.status = WorkflowRunStatus.STARTED.value
                    return WorkflowRunClaim(
                        record=WorkflowRunRecord(
                            run=existing.run,
                            request_payload_sha256=request_payload_sha256,
                            heartbeat_at=heartbeat_at,
                            lease_expires_at=lease_expires_at,
                        ),
                        claimed=True,
                    )
        return WorkflowRunClaim(record=existing, claimed=False)

    def mark_running(
        self,
        run_id: str,
        *,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> None:
        with Session(self._engine) as session, session.begin():
            row = session.get(WorkflowRunRow, run_id)
            if row is None:
                raise WorkflowRunNotFound("Workflow run was not found")
            if row.status not in {
                WorkflowRunStatus.STARTED.value,
                WorkflowRunStatus.RUNNING.value,
            }:
                raise IdempotencyConflict("Terminal Workflow run cannot be restarted")
            row.status = WorkflowRunStatus.RUNNING.value
            row.heartbeat_at = heartbeat_at.isoformat()
            row.lease_expires_at = lease_expires_at.isoformat()

    def complete(
        self,
        run: WorkflowRun,
        *,
        fact_data: tuple[JsonValue | None, ...],
        missing_capabilities: tuple[str, ...],
    ) -> WorkflowRunRecord:
        if len(fact_data) != len(run.steps):
            raise ValueError("fact_data must align with workflow steps")
        artifacts, artifact_warnings = self._encode_artifacts(run.run_id, fact_data)
        durable_missing = tuple(dict.fromkeys((*missing_capabilities, *artifact_warnings)))
        with Session(self._engine) as session, session.begin():
            row = session.get(WorkflowRunRow, run.run_id)
            if row is None:
                raise WorkflowRunNotFound("Workflow run was not found")
            if row.status not in {
                WorkflowRunStatus.STARTED.value,
                WorkflowRunStatus.RUNNING.value,
            }:
                raise IdempotencyConflict("Terminal Workflow run cannot be completed twice")
            row.completed_at = run.completed_at.isoformat() if run.completed_at else None
            row.status = run.status.value
            row.report_id = run.report_id
            row.heartbeat_at = (run.completed_at or run.started_at).isoformat()
            row.lease_expires_at = row.heartbeat_at
            row.missing_capabilities = durable_missing
            session.add_all(
                [
                    WorkflowRunStepRow(
                        run_id=run.run_id,
                        ordinal=item.ordinal,
                        step_name=item.step_name,
                        tool_name=item.tool_name,
                        required=int(item.required),
                        ok=int(item.ok),
                        degraded=int(item.degraded),
                        request_id=item.request_id,
                        as_of=item.as_of.isoformat(),
                        source_names=item.source_names,
                        warning_codes=item.warning_codes,
                        error_codes=item.error_codes,
                    )
                    for item in run.steps
                ]
            )
            session.add_all(artifacts)
        return self.get_record(run.run_id)

    @staticmethod
    def _encode_artifacts(
        run_id: str, fact_data: tuple[JsonValue | None, ...]
    ) -> tuple[list[WorkflowRunFactArtifactRow], tuple[str, ...]]:
        rows: list[WorkflowRunFactArtifactRow] = []
        warnings: list[str] = []
        total = 0
        for ordinal, value in enumerate(fact_data, 1):
            payload = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            encoded = payload.encode("utf-8")
            if (
                len(encoded) > _ARTIFACT_LIMIT_BYTES
                or total + len(encoded) > _TOTAL_ARTIFACT_LIMIT_BYTES
            ):
                payload = "null"
                encoded = payload.encode("utf-8")
                warnings.append(
                    f"Workflow fact artifact {ordinal} exceeded the durable replay size limit"
                )
            total += len(encoded)
            rows.append(
                WorkflowRunFactArtifactRow(
                    run_id=run_id,
                    ordinal=ordinal,
                    payload_json=payload,
                    payload_sha256=hashlib.sha256(encoded).hexdigest(),
                    size_bytes=len(encoded),
                )
            )
        return rows, tuple(warnings)

    def get_by_idempotency_key(self, idempotency_key: str) -> WorkflowRunRecord | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(WorkflowRunRow).where(WorkflowRunRow.idempotency_key == idempotency_key)
            )
            if row is None:
                return None
            return self._hydrate_record(session, row)

    def get_record(self, run_id: str) -> WorkflowRunRecord:
        with Session(self._engine) as session:
            row = session.get(WorkflowRunRow, run_id)
            if row is None:
                raise WorkflowRunNotFound("Workflow run was not found")
            return self._hydrate_record(session, row)

    def get(self, run_id: str) -> WorkflowRun:
        return self.get_record(run_id).run

    @staticmethod
    def _hydrate_record(session: Session, row: WorkflowRunRow) -> WorkflowRunRecord:
        step_rows = tuple(
            session.scalars(
                select(WorkflowRunStepRow)
                .where(WorkflowRunStepRow.run_id == row.run_id)
                .order_by(WorkflowRunStepRow.ordinal)
            )
        )
        artifact_rows = tuple(
            session.scalars(
                select(WorkflowRunFactArtifactRow)
                .where(WorkflowRunFactArtifactRow.run_id == row.run_id)
                .order_by(WorkflowRunFactArtifactRow.ordinal)
            )
        )
        run = WorkflowRun(
            run_id=row.run_id,
            workflow_type=WorkflowType(row.workflow_type),
            subject_id=row.subject_id,
            instrument_id=row.instrument_id,
            requested_as_of=datetime.fromisoformat(row.requested_as_of),
            started_at=datetime.fromisoformat(row.started_at),
            completed_at=(datetime.fromisoformat(row.completed_at) if row.completed_at else None),
            status=WorkflowRunStatus(row.status),
            steps=tuple(
                WorkflowStepReceipt(
                    ordinal=item.ordinal,
                    step_name=item.step_name,
                    tool_name=item.tool_name,
                    required=bool(item.required),
                    ok=bool(item.ok),
                    degraded=bool(item.degraded),
                    request_id=item.request_id,
                    as_of=datetime.fromisoformat(item.as_of),
                    source_names=item.source_names,
                    warning_codes=item.warning_codes,
                    error_codes=item.error_codes,
                )
                for item in step_rows
            ),
            report_id=row.report_id,
            execution_effect=False,
        )
        fact_data = tuple(
            cast(JsonValue | None, json.loads(item.payload_json)) for item in artifact_rows
        )
        return WorkflowRunRecord(
            run=run,
            request_payload_sha256=row.request_payload_sha256,
            heartbeat_at=datetime.fromisoformat(row.heartbeat_at),
            lease_expires_at=datetime.fromisoformat(row.lease_expires_at),
            fact_data=fact_data,
            missing_capabilities=row.missing_capabilities,
        )
