"""Durable workflow receipt repository port."""

from typing import Protocol

from domain.workflow.models import WorkflowRun


class WorkflowRunRepository(Protocol):
    def append(self, run: WorkflowRun) -> WorkflowRun: ...

    def get(self, run_id: str) -> WorkflowRun: ...
