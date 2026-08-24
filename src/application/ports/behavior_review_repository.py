"""Persistence boundary for append-only Behavior Review Runs."""

from __future__ import annotations

from typing import Protocol

from domain.behavior_review.models import (
    BehaviorActionObservation,
    BehaviorReviewRun,
)


class BehaviorReviewRepository(Protocol):
    def append_run(self, value: BehaviorReviewRun) -> BehaviorReviewRun: ...

    def get_run(self, run_id: str) -> BehaviorReviewRun | None: ...

    def get_run_by_idempotency_key(self, key: str) -> BehaviorReviewRun | None: ...

    def list_runs(self, *, limit: int = 50) -> tuple[BehaviorReviewRun, ...]: ...

    def list_action_observations(
        self,
        *,
        limit: int = 2_000,
    ) -> tuple[BehaviorActionObservation, ...]: ...


__all__ = ["BehaviorReviewRepository"]
