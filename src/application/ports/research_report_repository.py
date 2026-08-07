"""ResearchReport repository port (Phase 1C C2b)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.models import ResearchReport


class ResearchReportRepository(Protocol):
    def add(self, report: ResearchReport) -> None: ...

    def get(self, report_id: str) -> ResearchReport: ...

    def get_by_content_sha256(self, content_sha256: str) -> ResearchReport | None: ...

    def list_by_subject(
        self, subject_id: str, *, as_of: datetime | None = None
    ) -> tuple[ResearchReport, ...]: ...
