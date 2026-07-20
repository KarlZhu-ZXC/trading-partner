"""SQLAlchemy durable workflow receipt repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from domain.common.errors import WorkflowRunNotFound
from domain.workflow.enums import WorkflowRunStatus, WorkflowType
from domain.workflow.models import WorkflowRun, WorkflowStepReceipt
from infrastructure.persistence.models import WorkflowRunRow, WorkflowRunStepRow


class SqlAlchemyWorkflowRunRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, run: WorkflowRun) -> WorkflowRun:
        with Session(self._engine) as session, session.begin():
            session.add(
                WorkflowRunRow(
                    run_id=run.run_id,
                    workflow_type=run.workflow_type.value,
                    case_id=run.case_id,
                    instrument_id=run.instrument_id,
                    requested_as_of=run.requested_as_of.isoformat(),
                    started_at=run.started_at.isoformat(),
                    completed_at=run.completed_at.isoformat(),
                    status=run.status.value,
                    report_id=run.report_id,
                )
            )
            session.flush()
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
        return run

    def get(self, run_id: str) -> WorkflowRun:
        with Session(self._engine) as session:
            row = session.get(WorkflowRunRow, run_id)
            if row is None:
                raise WorkflowRunNotFound("Workflow run was not found")
            step_rows = session.scalars(
                select(WorkflowRunStepRow)
                .where(WorkflowRunStepRow.run_id == run_id)
                .order_by(WorkflowRunStepRow.ordinal)
            )
            return WorkflowRun(
                run_id=row.run_id,
                workflow_type=WorkflowType(row.workflow_type),
                case_id=row.case_id,
                instrument_id=row.instrument_id,
                requested_as_of=datetime.fromisoformat(row.requested_as_of),
                started_at=datetime.fromisoformat(row.started_at),
                completed_at=datetime.fromisoformat(row.completed_at),
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
