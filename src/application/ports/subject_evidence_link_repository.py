"""SubjectEvidenceLink repository port (Phase 1C C2b)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.models import Evidence, SubjectEvidenceLink


class SubjectEvidenceLinkRepository(Protocol):
    def add(self, link: SubjectEvidenceLink) -> None: ...

    def get(self, subject_id: str, evidence_id: str) -> SubjectEvidenceLink: ...

    def exists(self, subject_id: str, evidence_id: str) -> bool: ...

    def list_evidence(
        self, subject_id: str, *, as_of: datetime | None = None
    ) -> tuple[Evidence, ...]: ...

    def list_subjects(self, evidence_id: str) -> tuple[str, ...]: ...
