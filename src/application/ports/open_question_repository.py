"""OpenQuestion repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.evidence_impact import ResearchSourceRef
from domain.research.models import OpenQuestion


class OpenQuestionRepository(Protocol):
    def list_by_subject(self, subject_id: str) -> tuple[OpenQuestion, ...]: ...

    def get(self, question_id: str) -> OpenQuestion: ...

    def add(self, question: OpenQuestion) -> None: ...

    def answer(
        self,
        question_id: str,
        *,
        answered_at: datetime,
        answer_summary: str,
        expected_version: int | None = None,
        source_refs: tuple[ResearchSourceRef, ...] | None = None,
    ) -> None: ...

    def close_without_answer(
        self,
        question_id: str,
        *,
        closed_reason: str,
        expected_version: int | None = None,
    ) -> None: ...

    def mark_stale(self, question_id: str, *, expected_version: int | None = None) -> None: ...

    def update_research(
        self,
        question_id: str,
        *,
        expected_version: int,
        evidence_requirements: tuple[str, ...],
        due_at: datetime | None,
        progress_note: str | None,
        source_refs: tuple[ResearchSourceRef, ...],
    ) -> None: ...

    def link_decisions(
        self,
        question_id: str,
        *,
        expected_version: int,
        decision_ids: tuple[str, ...],
    ) -> None: ...

    def list_due(
        self,
        *,
        now: datetime,
        subject_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[OpenQuestion, ...]: ...
