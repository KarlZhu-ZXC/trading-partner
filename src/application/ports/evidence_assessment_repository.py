"""EvidenceAssessment repository port (Phase 1C C2b)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import EvidenceStance
from domain.research.models import EvidenceAssessment


class EvidenceAssessmentRepository(Protocol):
    def add(self, assessment: EvidenceAssessment) -> None: ...

    def list_for_evidence(
        self, evidence_id: str, *, as_of: datetime | None = None
    ) -> tuple[EvidenceAssessment, ...]: ...

    def list_for_thesis(
        self,
        thesis_id: str,
        *,
        stance: EvidenceStance | None = None,
        as_of: datetime | None = None,
    ) -> tuple[EvidenceAssessment, ...]: ...
