"""Optional async escalation callback for eligible Observation revisions."""

from __future__ import annotations

from typing import Protocol

from application.dto.external_note_review import ExternalNoteReviewDraftDTO


class ExternalNoteDeepReviewer(Protocol):
    async def review(
        self,
        note_revision_id: str,
        *,
        explicit_review: bool = False,
        force: bool = False,
    ) -> ExternalNoteReviewDraftDTO | None: ...

