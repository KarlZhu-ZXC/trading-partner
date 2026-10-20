"""Note-led history: immutable source metadata and explicit owner records only."""

import base64
import hashlib
import json
from typing import Any

from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import UowFactory
from domain.common.errors import DataContractError
from domain.external_note.enums import NoteCoverage

PERSONAL_CONCLUSION_TAG = "personal_conclusion_v1"


def entry_kind(entry: Any) -> str:
    return (
        "PERSONAL_CONCLUSION"
        if PERSONAL_CONCLUSION_TAG in entry.topic_tags and entry.authored_by == "user"
        else "SAVED_DISCUSSION"
    )


class NoteHistoryService:
    PAGE_SIZE = 20
    REVISION_LIMIT = 1000
    JOURNAL_LIMIT = 1000

    def __init__(
        self, notes: ExternalNoteRepository, uow: UowFactory, clock: Clock, redactor: SecretRedactor
    ):
        self._notes, self._uow, self._clock, self._redactor = notes, uow, clock, redactor

    def get(self, revision_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        now = self._clock.now()
        anchor = self._notes.revision_by_id(revision_id)
        if anchor is None or anchor.observed_at > now:
            raise DataContractError("Note revision is unavailable")
        identity = self._notes.get(anchor.note_id)
        if identity is None or identity.source != "MOOMOO_NOTE":
            raise DataContractError("Moomoo note identity is unavailable")
        rows, has_older = self._notes.history_metadata(
            note_id=anchor.note_id,
            instrument_id=identity.primary_instrument_id,
            observed_through=now,
            limit=self.REVISION_LIMIT,
        )
        items = [
            {
                "kind": "NOTE_REVISION",
                "event_id": row.note_revision_id,
                "at": row.observed_at,
                "note_id": row.note_id,
                "note_revision_id": row.note_revision_id,
                "version": row.version,
                "title": self._display(row.title),
                "coverage": row.coverage.value,
                "source_timestamp": row.source_timestamp,
                "instrument_id": identity.primary_instrument_id,
            }
            for row in rows
        ]
        journals_status = "AVAILABLE"
        try:
            with self._uow() as uow:
                journals = uow.journal.list_related(
                    "external_note_revision",
                    tuple(
                        row.note_revision_id for row in rows if row.coverage is NoteCoverage.FULL
                    ),
                    limit=self.JOURNAL_LIMIT + 1,
                )
            journals_status = "PARTIAL" if len(journals) > self.JOURNAL_LIMIT else "AVAILABLE"
            for entry in journals[: self.JOURNAL_LIMIT]:
                if entry.created_at > now:
                    continue
                items.append(
                    {
                        "kind": entry_kind(entry),
                        "event_id": entry.journal_id,
                        "journal_id": entry.journal_id,
                        "at": entry.created_at,
                        "note_revision_id": entry.related_entity_id,
                        "title": self._display(entry.title),
                        "confirmed_by": entry.confirmed_by,
                        "supersedes_journal_id": entry.supersedes_journal_id,
                    }
                )
        except Exception:  # noqa: BLE001 - source history stays readable on journal failure
            journals_status = "UNAVAILABLE"
        items.sort(key=lambda row: (row["at"], row["event_id"]), reverse=True)
        fingerprint = hashlib.sha256(
            json.dumps(
                [revision_id, identity.primary_instrument_id, has_older, journals_status, items],
                default=str,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        offset = 0
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor))
                offset = token["offset"]
                if (
                    token["fingerprint"] != fingerprint
                    or type(offset) is not int
                    or not 0 <= offset < len(items)
                ):
                    raise ValueError("changed")
            except (ValueError, TypeError, KeyError):
                raise DataContractError("History changed; reload the first page") from None
        page = items[offset : offset + self.PAGE_SIZE]
        next_offset = offset + len(page)
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "offset": next_offset,
                    }
                ).encode()
            ).decode()
            if next_offset < len(items)
            else None
        )
        return {
            "note_revision_id": revision_id,
            "instrument_id": identity.primary_instrument_id,
            "scope": "INSTRUMENT" if identity.primary_instrument_id else "NOTE",
            "scope_basis": "CURRENT_EXACT_INSTRUMENT_MAPPING",
            "items": page,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_older_revisions": has_older,
            "journals_status": journals_status,
            "coverage": "PARTIAL" if has_older or journals_status != "AVAILABLE" else "COMPLETE",
            "window_start": page[-1]["at"] if page else None,
            "window_end": items[offset - 1]["at"] if offset else now,
            "as_of": now,
            "execution_effect": False,
        }

    def _display(self, value: str) -> str:
        import re

        return re.sub(
            r'https?://[^\s"<>]+',
            "[REDACTED_URL]",
            self._redactor.redact_text(value),
            flags=re.IGNORECASE,
        )
