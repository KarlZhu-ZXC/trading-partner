"""JournalEntry repository port (Phase 1C C2b)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.models import JournalEntry


class JournalRepository(Protocol):
    def add(
        self,
        entry: JournalEntry,
        *,
        idempotency_key: str,
        idempotency_payload_sha256: str,
    ) -> None: ...

    def get(self, journal_id: str) -> JournalEntry: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> JournalEntry | None: ...

    def list(
        self,
        *,
        case_id: str | None,
        as_of: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[JournalEntry, ...]: ...
