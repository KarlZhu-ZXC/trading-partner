"""CandidateThesisRevision repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import CandidateKind, CandidateStatus, ConfirmationMode
from domain.research.models import CandidateThesisRevision


class CandidateThesisRevisionRepository(Protocol):
    def get(self, candidate_id: str) -> CandidateThesisRevision: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> CandidateThesisRevision | None:
        """Return the candidate for a normalized idempotency key, or None."""
        ...

    def list(
        self,
        *,
        case_id: str | None = None,
        kind: CandidateKind | None = None,
        status: CandidateStatus | None = None,
        confirmation_mode: ConfirmationMode | None = None,
        proposed_by: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[CandidateThesisRevision, ...]: ...

    def add(self, candidate: CandidateThesisRevision) -> None: ...

    def update_status(
        self,
        candidate_id: str,
        *,
        new_status: CandidateStatus,
        reviewed_at: datetime | None,
        reviewed_by: str | None,
        review_note: str | None,
        rejection_reason: str | None,
    ) -> None: ...

    def expire_due(self, *, now: datetime, limit: int = 200) -> tuple[str, ...]: ...
