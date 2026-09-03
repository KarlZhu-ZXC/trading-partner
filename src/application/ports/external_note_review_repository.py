"""Append-only persistence boundary for Observation review state."""

from __future__ import annotations

from typing import Protocol

from domain.external_note.enums import ExternalNoteReviewStatus
from domain.external_note.models import ExternalNoteReview


class ExternalNoteReviewRepository(Protocol):
    def append(
        self,
        value: ExternalNoteReview,
        *,
        expected_version: int,
    ) -> ExternalNoteReview: ...

    def latest(self, review_id: str) -> ExternalNoteReview | None: ...

    def latest_for_revision(self, note_revision_id: str) -> ExternalNoteReview | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> ExternalNoteReview | None: ...

    def list_latest(
        self,
        *,
        statuses: frozenset[ExternalNoteReviewStatus] | None = None,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ExternalNoteReview, ...]: ...
