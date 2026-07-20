"""SQLAlchemy JournalEntry repository (append-only, session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import JournalEntryType
from domain.common.errors import InvalidResearchLink, ResearchMemoryNotFound
from domain.research.models import JournalEntry
from infrastructure.persistence.models import JournalEntryRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    require_case_exists,
    require_idempotency_storage,
    require_instruments_exist,
    require_journal_page_bounds,
    require_same_case_supersedes,
    require_visible_not_after,
)


def _to_domain(row: JournalEntryRow) -> JournalEntry:
    return JournalEntry(
        journal_id=row.journal_id,
        case_id=row.case_id,
        entry_type=JournalEntryType(row.entry_type),
        title=row.title,
        body_markdown=row.body_markdown,
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        authored_by=row.authored_by,
        confirmed_by=row.confirmed_by,
        instrument_ids=tuple(row.instrument_ids_json),
        topic_tags=tuple(row.topic_tags_json),
        related_entity_type=row.related_entity_type,
        related_entity_id=row.related_entity_id,
        supersedes_journal_id=row.supersedes_journal_id,
        schema_version=row.schema_version,
    )


def _to_row(
    entry: JournalEntry,
    *,
    idempotency_key: str,
    idempotency_payload_sha256: str,
) -> JournalEntryRow:
    return JournalEntryRow(
        journal_id=entry.journal_id,
        case_id=entry.case_id,
        entry_type=entry.entry_type.value,
        title=entry.title,
        body_markdown=entry.body_markdown,
        created_at=dt_to_db(entry.created_at),
        authored_by=entry.authored_by,
        confirmed_by=entry.confirmed_by,
        instrument_ids_json=entry.instrument_ids,
        topic_tags_json=entry.topic_tags,
        related_entity_type=entry.related_entity_type,
        related_entity_id=entry.related_entity_id,
        supersedes_journal_id=entry.supersedes_journal_id,
        idempotency_key=idempotency_key,
        idempotency_payload_sha256=idempotency_payload_sha256,
        schema_version=entry.schema_version,
    )


class SqlAlchemyJournalRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        entry: JournalEntry,
        *,
        idempotency_key: str,
        idempotency_payload_sha256: str,
    ) -> None:
        require_idempotency_storage(
            idempotency_key=idempotency_key,
            idempotency_payload_sha256=idempotency_payload_sha256,
        )
        if entry.case_id is not None:
            require_case_exists(self._session, entry.case_id)
        require_instruments_exist(self._session, entry.instrument_ids)
        # related entity pair: domain-only; no string-based cross-table resolution
        if entry.supersedes_journal_id is not None:
            old = self._session.get(JournalEntryRow, entry.supersedes_journal_id)
            if old is None:
                raise InvalidResearchLink(
                    "superseded journal entry does not exist",
                    details={
                        "entity_type": "journal",
                        "supersedes_journal_id": entry.supersedes_journal_id,
                    },
                )
            require_same_case_supersedes(
                new_case_id=entry.case_id,
                old_case_id=old.case_id,
                entity_type="journal",
                supersedes_id=entry.supersedes_journal_id,
            )
            require_visible_not_after(
                old_visible_at=dt_from_db(old.created_at, field_name="created_at"),
                new_visible_at=entry.created_at,
                entity_type="journal",
                supersedes_id=entry.supersedes_journal_id,
            )
        self._session.add(
            _to_row(
                entry,
                idempotency_key=idempotency_key,
                idempotency_payload_sha256=idempotency_payload_sha256,
            )
        )
        self._session.flush()

    def get(self, journal_id: str) -> JournalEntry:
        row = self._session.get(JournalEntryRow, journal_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "JournalEntry not found",
                details={"entity_type": "journal", "journal_id": journal_id},
            )
        return _to_domain(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> JournalEntry | None:
        stmt = select(JournalEntryRow).where(
            JournalEntryRow.idempotency_key == idempotency_key
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            return None
        return _to_domain(row)

    def list(
        self,
        *,
        case_id: str | None,
        as_of: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[JournalEntry, ...]:
        require_journal_page_bounds(limit=limit, offset=offset)
        stmt = select(JournalEntryRow)
        if case_id is not None:
            stmt = stmt.where(JournalEntryRow.case_id == case_id)
        if as_of is not None:
            stmt = stmt.where(JournalEntryRow.created_at <= dt_to_db(as_of))
        stmt = stmt.order_by(
            JournalEntryRow.created_at.desc(),
            JournalEntryRow.journal_id.asc(),
        )
        stmt = stmt.offset(offset).limit(limit)
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())
