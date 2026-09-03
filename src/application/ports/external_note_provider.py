"""Read-only Provider boundary for external living notes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.external_note.models import ExternalNoteSourceSnapshot


@dataclass(frozen=True, slots=True)
class ObservationSourceCapability:
    source_code: str
    display_name: str
    supports_full_text: bool
    supports_incremental_sync: bool
    requires_interactive_session: bool
    content_modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExternalNoteScanResult:
    snapshots: tuple[ExternalNoteSourceSnapshot, ...]
    cache_files_scanned: int
    warning_codes: tuple[str, ...] = ()


class ExternalNoteProvider(Protocol):
    @property
    def capability(self) -> ObservationSourceCapability: ...

    def scan(self) -> ExternalNoteScanResult: ...
