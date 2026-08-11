"""Persistence port for immutable Judgment Scorecard runs."""

from __future__ import annotations

from typing import Protocol

from domain.scorecard.models import JudgmentScorecardRun


class JudgmentScorecardRepository(Protocol):
    def append(self, value: JudgmentScorecardRun) -> JudgmentScorecardRun: ...

    def get_by_idempotency_key(self, key: str) -> JudgmentScorecardRun | None: ...

    def list(
        self,
        *,
        subject_id: str | None = None,
        thesis_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[tuple[JudgmentScorecardRun, ...], int]: ...
