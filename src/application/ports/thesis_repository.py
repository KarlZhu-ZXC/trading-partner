"""Thesis repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import ThesisStatus
from domain.research.models import Thesis


class ThesisRepository(Protocol):
    def get(self, thesis_id: str) -> Thesis: ...

    def list_by_case(self, case_id: str) -> tuple[Thesis, ...]: ...

    def add(self, thesis: Thesis) -> None: ...

    def update_status(
        self,
        thesis_id: str,
        *,
        new_status: ThesisStatus,
        archived_at: datetime | None,
    ) -> None: ...

    def advance_current_revision(
        self,
        thesis_id: str,
        *,
        new_revision_no: int,
        new_latest_revision_id: str,
    ) -> None: ...
