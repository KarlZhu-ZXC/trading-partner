"""Owner-controlled durable intake for full-text external observations."""

from __future__ import annotations

from typing import Protocol

from domain.external_note.models import ExternalNoteSourceSnapshot


class ExternalObservationCaptureStore(Protocol):
    def append(self, snapshot: ExternalNoteSourceSnapshot) -> bool: ...
