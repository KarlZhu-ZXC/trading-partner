"""InvestmentCase repository port."""

from __future__ import annotations

from typing import Protocol

from domain.common.enums import InvestmentCaseStatus, InvestmentCaseType
from domain.research.models import InvestmentCase


class InvestmentCaseRepository(Protocol):
    def get(self, case_id: str) -> InvestmentCase: ...

    def list(
        self,
        *,
        case_type: InvestmentCaseType | None = None,
        status: InvestmentCaseStatus | None = None,
        primary_instrument_id: str | None = None,
        topic_tag: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[InvestmentCase, ...]: ...

    def add(self, case: InvestmentCase) -> None: ...

    def update(self, case: InvestmentCase) -> None: ...

    def list_active_primary_thesis_ids(self, case_id: str) -> tuple[str, ...]: ...
