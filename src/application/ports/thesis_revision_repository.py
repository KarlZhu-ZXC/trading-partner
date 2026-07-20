"""ThesisRevision repository port (append-only)."""

from __future__ import annotations

from typing import Protocol

from domain.research.models import ThesisRevision


class ThesisRevisionRepository(Protocol):
    def get(self, revision_id: str) -> ThesisRevision: ...

    def list_by_thesis(self, thesis_id: str) -> tuple[ThesisRevision, ...]: ...

    def append(self, revision: ThesisRevision) -> None: ...

    def next_revision_no(self, thesis_id: str) -> int: ...
