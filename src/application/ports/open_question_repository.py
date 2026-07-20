"""OpenQuestion repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.models import OpenQuestion


class OpenQuestionRepository(Protocol):
    def list_by_case(self, case_id: str) -> tuple[OpenQuestion, ...]: ...

    def get(self, question_id: str) -> OpenQuestion: ...

    def add(self, question: OpenQuestion) -> None: ...

    def answer(
        self,
        question_id: str,
        *,
        answered_at: datetime,
        answer_summary: str,
    ) -> None: ...

    def close_without_answer(
        self,
        question_id: str,
        *,
        closed_reason: str,
    ) -> None: ...

    def mark_stale(self, question_id: str) -> None: ...
