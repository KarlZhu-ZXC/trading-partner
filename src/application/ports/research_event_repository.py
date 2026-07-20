"""ResearchEvent repository port (Phase 1C C2b)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import ResearchEventType
from domain.research.models import ResearchEvent


class ResearchEventRepository(Protocol):
    def add(self, event: ResearchEvent) -> None: ...

    def get(self, event_id: str) -> ResearchEvent: ...

    def list_timeline(
        self,
        case_id: str,
        *,
        start: datetime | None,
        end: datetime | None,
        as_of: datetime | None,
        event_types: tuple[ResearchEventType, ...],
    ) -> tuple[ResearchEvent, ...]: ...
