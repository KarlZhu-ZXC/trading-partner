"""Evidence repository port (Phase 1C C2b)."""

from __future__ import annotations

from typing import Protocol

from domain.research.models import Evidence


class EvidenceRepository(Protocol):
    def add(self, evidence: Evidence) -> None: ...

    def get(self, evidence_id: str) -> Evidence: ...

    def get_by_content_sha256(self, content_sha256: str) -> Evidence | None: ...
