"""CaseEvidenceLink repository port (Phase 1C C2b)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.models import CaseEvidenceLink, Evidence


class CaseEvidenceLinkRepository(Protocol):
    def add(self, link: CaseEvidenceLink) -> None: ...

    def get(self, case_id: str, evidence_id: str) -> CaseEvidenceLink: ...

    def exists(self, case_id: str, evidence_id: str) -> bool: ...

    def list_evidence(
        self, case_id: str, *, as_of: datetime | None = None
    ) -> tuple[Evidence, ...]: ...

    def list_cases(self, evidence_id: str) -> tuple[str, ...]: ...
