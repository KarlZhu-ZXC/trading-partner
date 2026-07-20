"""InvalidationCondition repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import InvalidationStatus
from domain.research.models import InvalidationCondition


class InvalidationConditionRepository(Protocol):
    def list_by_revision(
        self, thesis_id: str, revision_no: int
    ) -> tuple[InvalidationCondition, ...]: ...

    def get(self, invalidation_id: str) -> InvalidationCondition: ...

    def add(self, invalidation: InvalidationCondition) -> None: ...

    def transition_status(
        self,
        invalidation_id: str,
        *,
        new_status: InvalidationStatus,
        triggered_at: datetime | None,
        triggered_reason: str | None,
        last_checked_at: datetime | None,
    ) -> None: ...

    def list_armed(
        self, thesis_id: str, revision_no: int
    ) -> tuple[InvalidationCondition, ...]: ...
