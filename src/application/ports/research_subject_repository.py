"""ResearchSubject repository port."""

from __future__ import annotations

from typing import Protocol

from domain.common.enums import ResearchSubjectStatus, ResearchSubjectType
from domain.research.models import ResearchSubject


class ResearchSubjectRepository(Protocol):
    def get(self, subject_id: str) -> ResearchSubject: ...

    def list(
        self,
        *,
        subject_type: ResearchSubjectType | None = None,
        status: ResearchSubjectStatus | None = None,
        primary_instrument_id: str | None = None,
        topic_tag: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ResearchSubject, ...]: ...

    def add(self, subject: ResearchSubject) -> None: ...

    def update(self, subject: ResearchSubject) -> None: ...

    def list_live_primary_thesis_ids(self, subject_id: str) -> tuple[str, ...]: ...
