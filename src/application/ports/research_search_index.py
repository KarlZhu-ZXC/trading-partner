"""Research search index port (Phase 1C C3).

Search index is a rebuildable projection, not business source of truth.
"""

from __future__ import annotations

from typing import Protocol

from application.dto.research_memory import ResearchSearchPageDTO, ResearchSearchQuery
from domain.common.enums import ResearchSearchEntityType


class ResearchSearchIndex(Protocol):
    def index(
        self,
        entity_type: ResearchSearchEntityType,
        entity_id: str,
    ) -> None: ...

    def refresh_evidence_membership(self, evidence_id: str) -> None: ...

    def search(self, query: ResearchSearchQuery) -> ResearchSearchPageDTO: ...

    def rebuild(self) -> int: ...

    def probe(self) -> bool: ...
