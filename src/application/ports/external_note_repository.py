"""Durable append-only external note storage boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from domain.external_note.enums import NoteCoverage
from domain.external_note.models import (
    ExternalNoteIdentity,
    ExternalNoteInterpretation,
    ExternalNoteRevision,
    ExternalNoteSyncReceipt,
)


@dataclass(frozen=True)
class NoteHistoryMetadata:
    note_id: str
    note_revision_id: str
    version: int
    title: str
    coverage: NoteCoverage
    observed_at: datetime
    source_timestamp: datetime | None


class ExternalNoteRepository(Protocol):
    def get(self, note_id: str) -> ExternalNoteIdentity | None: ...

    def get_by_source_id(self, source: str, external_id: str) -> ExternalNoteIdentity | None: ...

    def latest_revision(self, note_id: str) -> ExternalNoteRevision | None: ...

    def revision_by_id(self, note_revision_id: str) -> ExternalNoteRevision | None: ...

    def previous_revision(
        self, note_id: str, before_version: int
    ) -> ExternalNoteRevision | None: ...

    def previous_revisions(
        self, before_versions: Mapping[str, int]
    ) -> dict[str, ExternalNoteRevision | None]:
        """One predecessor per note; None means absent, an omitted key means unreadable."""
        ...

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

    def list_revisions_for_instrument(
        self,
        instrument_id: str,
        *,
        observed_after: datetime | None = None,
        observed_through: datetime | None = None,
    ) -> tuple[ExternalNoteRevision, ...]: ...

    def list_latest(
        self, limit: int = 100
    ) -> tuple[tuple[ExternalNoteIdentity, ExternalNoteRevision], ...]: ...

    def browse_latest(
        self, *, limit: int, offset: int, query: str, observed_through: datetime,
    ) -> tuple[tuple[tuple[ExternalNoteIdentity, ExternalNoteRevision], ...], bool]:
        """Page all saved Moomoo identities by latest revision time, without a 500-note cap."""
        ...

    def history_metadata(
        self, *, note_id: str, instrument_id: str | None,
        observed_through: datetime, limit: int,
    ) -> tuple[tuple[NoteHistoryMetadata, ...], bool]: ...

    def list_revisions(self, note_id: str, limit: int = 50) -> tuple[ExternalNoteRevision, ...]: ...
