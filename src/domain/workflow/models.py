"""Immutable terminal workflow run and fact-step receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.workflow.enums import WorkflowRunStatus, WorkflowType


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be a bounded non-blank string")
    return value


def _codes(value: tuple[str, ...], field: str) -> None:
    if not isinstance(value, tuple) or len(value) != len(set(value)):
        raise DataContractError(f"{field} must be a unique tuple")
    for item in value:
        _text(item, field, 128)


@dataclass(frozen=True, slots=True)
class WorkflowStepReceipt:
    ordinal: int
    step_name: str
    tool_name: str
    required: bool
    ok: bool
    degraded: bool
    request_id: str
    as_of: datetime
    source_names: tuple[str, ...]
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 1 <= self.ordinal <= 50:
            raise DataContractError("workflow step ordinal must be in [1,50]")
        _text(self.step_name, "step_name", 128)
        _text(self.tool_name, "tool_name", 128)
        if type(self.required) is not bool or type(self.ok) is not bool:
            raise DataContractError("workflow step flags must be bool")
        if type(self.degraded) is not bool:
            raise DataContractError("degraded must be bool")
        _text(self.request_id, "request_id", 128)
        require_aware_datetime(self.as_of, field_name="as_of")
        _codes(self.source_names, "source_names")
        _codes(self.warning_codes, "warning_codes")
        _codes(self.error_codes, "error_codes")
        if self.ok and self.error_codes:
            raise DataContractError("successful workflow step must not contain errors")
        if not self.ok and not self.error_codes:
            raise DataContractError("failed workflow step requires error_codes")


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    run_id: str
    workflow_type: WorkflowType
    case_id: str | None
    instrument_id: str | None
    requested_as_of: datetime
    started_at: datetime
    completed_at: datetime
    status: WorkflowRunStatus
    steps: tuple[WorkflowStepReceipt, ...]
    report_id: str | None = None
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id", 128)
        if not isinstance(self.workflow_type, WorkflowType) or not isinstance(
            self.status, WorkflowRunStatus
        ):
            raise DataContractError("workflow enums are invalid")
        if self.case_id is not None:
            _text(self.case_id, "case_id", 128)
        if self.instrument_id is not None:
            parse_instrument_id(self.instrument_id)
        require_aware_datetime(self.requested_as_of, field_name="requested_as_of")
        require_aware_datetime(self.started_at, field_name="started_at")
        require_aware_datetime(self.completed_at, field_name="completed_at")
        if self.completed_at < self.started_at:
            raise DataContractError("completed_at must be >= started_at")
        if not self.steps or {item.ordinal for item in self.steps} != set(
            range(1, len(self.steps) + 1)
        ):
            raise DataContractError("workflow steps must be nonempty and contiguous")
        required_failed = any(item.required and not item.ok for item in self.steps)
        imperfect = any(not item.ok or item.degraded for item in self.steps)
        expected = (
            WorkflowRunStatus.FAILED
            if required_failed
            else WorkflowRunStatus.PARTIAL
            if imperfect
            else WorkflowRunStatus.COMPLETE
        )
        if self.status is not expected:
            raise DataContractError("workflow status does not match step outcomes")
        if self.report_id is not None:
            _text(self.report_id, "report_id", 128)
        if self.execution_effect is not False:
            raise DataContractError("workflow run must not execute")
