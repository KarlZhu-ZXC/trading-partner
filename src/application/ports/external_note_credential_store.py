"""Secret-safe credential boundary for external observation sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExternalNoteCredentialStatus:
    source_code: str
    supported: bool
    configured: bool


class ExternalNoteCredentialStore(Protocol):
    @property
    def source_code(self) -> str: ...

    def configured(self) -> bool: ...

    def set_secret(self, value: str) -> None: ...


__all__ = ["ExternalNoteCredentialStatus", "ExternalNoteCredentialStore"]
