"""Durable append-only external note storage boundary."""

from __future__ import annotations

from typing import Protocol

from domain.external_note.models import (
    ExternalNoteIdentity,
    ExternalNoteInterpretation,
    ExternalNoteRevision,
    ExternalNoteSyncReceipt,
)


class ExternalNoteRepository(Protocol):
    def get_by_source_id(self, source: str, external_id: str) -> ExternalNoteIdentity | None: ...

    def latest_revision(self, note_id: str) -> ExternalNoteRevision | None: ...

    def revision_by_id(self, note_revision_id: str) -> ExternalNoteRevision | None: ...

    def previous_revision(
        self, note_id: str, before_version: int
    ) -> ExternalNoteRevision | None: ...

    def revision_by_source_key(
        self, note_id: str, source_revision_key: str
    ) -> ExternalNoteRevision | None: ...

    def append_identity(self, value: ExternalNoteIdentity) -> None: ...

    def update_identity(self, value: ExternalNoteIdentity) -> None: ...

    def append_revision(self, value: ExternalNoteRevision) -> None: ...

    def append_interpretation(self, value: ExternalNoteInterpretation) -> None: ...

    def interpretation_for_revision(
        self, note_revision_id: str
    ) -> ExternalNoteInterpretation | None: ...

    def append_sync_receipt(self, value: ExternalNoteSyncReceipt) -> None: ...

    def list_latest(
        self, limit: int = 100
    ) -> tuple[tuple[ExternalNoteIdentity, ExternalNoteRevision], ...]: ...

    def list_revisions(
        self, note_id: str, limit: int = 50
    ) -> tuple[ExternalNoteRevision, ...]: ...
