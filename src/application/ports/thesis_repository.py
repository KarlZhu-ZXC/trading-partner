"""Thesis repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import ThesisRole, ThesisStatus
from domain.research.models import Thesis


class ThesisRepository(Protocol):
    def get(self, thesis_id: str) -> Thesis: ...

    def list_by_subject(self, subject_id: str) -> tuple[Thesis, ...]: ...

    def add(self, thesis: Thesis) -> None: ...

    def update_status(
        self,
        thesis_id: str,
        *,
        new_status: ThesisStatus,
        archived_at: datetime | None,
    ) -> None: ...

    def update_metadata(
        self,
        thesis_id: str,
        *,
        title: str,
        role: ThesisRole,
        parent_thesis_id: str | None,
        rival_thesis_ids: tuple[str, ...],
    ) -> None: ...

    def advance_current_revision(
        self,
        thesis_id: str,
        *,
        new_revision_no: int,
        new_latest_revision_id: str,
    ) -> None: ...
