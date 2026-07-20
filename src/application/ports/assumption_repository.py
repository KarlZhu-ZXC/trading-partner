"""Assumption repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.models import Assumption


class AssumptionRepository(Protocol):
    def list_by_revision(self, thesis_id: str, revision_no: int) -> tuple[Assumption, ...]: ...

    def get(self, assumption_id: str) -> Assumption: ...

    def add(self, assumption: Assumption) -> None: ...

    def retire(
        self,
        assumption_id: str,
        *,
        retired_at: datetime,
        retired_reason: str,
    ) -> None: ...

    def list_active(self, thesis_id: str, revision_no: int) -> tuple[Assumption, ...]: ...
