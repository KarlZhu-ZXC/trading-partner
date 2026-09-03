"""Minimal callback used by Observation analysis to enqueue eligible reviews."""

from __future__ import annotations

from typing import Protocol

from application.dto.external_note_review import ExternalNoteReviewDTO


class ExternalNoteReviewMaterializer(Protocol):
    def ensure_pending(
        self,
        *,
        note_revision_id: str,
        subject_id: str | None = None,
    ) -> ExternalNoteReviewDTO: ...
