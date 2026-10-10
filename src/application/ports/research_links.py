"""Application ports for source snapshots and confirmed evidence impacts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.evidence_impact import (
    EvidenceImpact,
    ResearchSourceRef,
    ResearchSourceSnapshot,
)


class EvidenceImpactRepository(Protocol):
    def get(self, impact_id: str, version: int | None = None) -> EvidenceImpact | None: ...

    def list_by_subject(
        self,
        subject_id: str,
        *,
        include_history: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[EvidenceImpact, ...]: ...

    def append(self, value: EvidenceImpact, *, expected_version: int | None) -> None: ...


class ResearchSourceRepository(Protocol):
    def get(
        self,
        subject_id: str,
        ref: ResearchSourceRef,
        *,
        as_of: datetime,
    ) -> ResearchSourceSnapshot | None: ...

    def list_for_subject(
        self,
        subject_id: str,
        *,
        as_of: datetime,
        limit: int = 200,
    ) -> tuple[ResearchSourceSnapshot, ...]: ...


__all__ = ["EvidenceImpactRepository", "ResearchSourceRepository"]
