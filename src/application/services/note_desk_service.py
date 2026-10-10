"""Moomoo-first durable reads: no model, archive lifecycle, or implicit write."""

import re
from datetime import datetime
from typing import Any

from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import UowFactory
from domain.common.errors import DataContractError
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import ExternalNoteRevision


def user_text(revision: ExternalNoteRevision) -> str:
    return "\n".join(
        " ".join(block.body.split())
        for block in revision.blocks
        if block.speaker_kind is NoteSpeakerKind.USER
    )


class NoteDeskService:
    def __init__(
        self,
        notes: ExternalNoteRepository,
        uow_factory: UowFactory,
        clock: Clock,
        redactor: SecretRedactor,
    ):
        self._notes, self._uow, self._clock = notes, uow_factory, clock
        self._redactor = redactor

    def _display_text(self, value: str) -> str:
        return re.sub(
            r'https?://[^\s"<>]+',
            "[REDACTED_URL]",
            self._redactor.redact_text(value),
            flags=re.IGNORECASE,
        )

    def _metadata(self, identity: Any, revision: ExternalNoteRevision) -> dict[str, Any]:
        previous_available = True
        try:
            previous = self._notes.previous_revision(revision.note_id, revision.version)
            if previous is not None and previous.observed_at > self._clock.now():
                previous = None
                previous_available = False
        except Exception:  # noqa: BLE001 - an unread predecessor is not a new note
            previous = None
            previous_available = False
        change = "CONTENT_UNAVAILABLE"
        if revision.coverage is NoteCoverage.FULL:
            if not previous_available:
                change = "PREVIOUS_CONTENT_UNAVAILABLE"
            elif previous is None:
                change = "NEW_USER_NOTE" if user_text(revision) else "NO_USER_TEXT"
            elif previous.coverage is NoteCoverage.FULL:
                change = (
                    "USER_TEXT_CHANGED"
                    if user_text(previous) != user_text(revision)
                    else "USER_TEXT_UNCHANGED"
                )
            else:
                change = "PREVIOUS_CONTENT_UNAVAILABLE"
        return {
            "note_id": revision.note_id,
            "note_revision_id": revision.note_revision_id,
            "version": revision.version,
            "title": self._display_text(revision.title),
            "source": identity.source,
            "instrument_id": identity.primary_instrument_id,
            "coverage": revision.coverage.value,
            "observed_at": revision.observed_at,
            "source_timestamp": revision.source_timestamp,
            "change": change,
            "previous_revision_id": previous.note_revision_id if previous else None,
            "requires_status_update": False,
        }

    def inbox(self, *, limit: int = 30, observed_after: datetime | None = None) -> dict[str, Any]:
        if observed_after is not None and observed_after.utcoffset() is None:
            raise DataContractError("observed_after must include a timezone")
        now = self._clock.now()
        values = self._notes.list_latest(limit=500)
        items = [
            self._metadata(identity, revision)
            for identity, revision in values[:500]
            if identity.source == "MOOMOO_NOTE"
            and revision.observed_at <= now
            and (observed_after is None or revision.observed_at > observed_after)
        ]
        discussion_coverage = "COMPLETE"
        try:
            with self._uow() as uow:
                entries = uow.journal.list_related(
                    "external_note_revision",
                    tuple(row["note_revision_id"] for row in items),
                    limit=501,
                )
            discussion_coverage = "PARTIAL" if len(entries) > 500 else "COMPLETE"
            latest_discussions = {}
            for entry in entries[:500]:
                if entry.created_at <= now and entry.related_entity_id not in latest_discussions:
                    latest_discussions[entry.related_entity_id] = entry
            for row in items:
                matched = latest_discussions.get(row["note_revision_id"])
                row["discussion"] = (
                    {"journal_id": matched.journal_id, "created_at": matched.created_at}
                    if matched
                    else None
                )
        except Exception:  # noqa: BLE001 - never hide a note after an incomplete discussion read
            discussion_coverage = "UNAVAILABLE"
        items.sort(key=lambda row: (row["observed_at"], row["note_revision_id"]), reverse=True)
        return {
            "items": items[:limit],
            "as_of": now,
            "returned_count": min(len(items), limit),
            "has_more": len(items) > limit or len(values) >= 500,
            "coverage": "PARTIAL" if len(values) >= 500 else "COMPLETE",
            "basis": "LATEST_SYNCED_REVISIONS",
            "discussion_coverage": discussion_coverage,
            "execution_effect": False,
        }

    def note(
        self,
        note_revision_id: str,
        *,
        include_content: bool = False,
        private_content_authorized: bool = False,
        block_index: int = 0,
        text_offset: int = 0,
        max_chars: int = 6000,
        journal_id: str | None = None,
    ) -> dict[str, Any]:
        if include_content and not private_content_authorized:
            raise DataContractError(
                "Explicit owner authorization is required to read private note text"
            )
        revision = self._notes.revision_by_id(note_revision_id)
        now = self._clock.now()
        if revision is None or revision.observed_at > now:
            raise DataContractError("Note revision is unavailable")
        identity = self._notes.get(revision.note_id)
        if identity is None or identity.source != "MOOMOO_NOTE":
            raise DataContractError("Moomoo note identity is unavailable")
        result = self._metadata(identity, revision)
        result["blocks"] = (
            [
                {
                    "index": i,
                    "ordinal": b.ordinal,
                    "speaker_kind": b.speaker_kind.value,
                    "speaker_label": self._display_text(b.speaker_label),
                    "section_date": b.section_date,
                    "characters": len(self._display_text(b.body)),
                }
                for i, b in enumerate(revision.blocks)
                if block_index <= i < block_index + 10
            ]
            if revision.coverage is NoteCoverage.FULL
            else []
        )
        result["block_count"] = (
            len(revision.blocks) if revision.coverage is NoteCoverage.FULL else 0
        )
        result["block_descriptors_have_more"] = block_index + 10 < len(revision.blocks)
        result["content"] = None
        result["discussion_content"] = None
        if include_content and journal_id is None and revision.coverage is NoteCoverage.FULL:
            if (
                not 0 <= block_index < len(revision.blocks)
                or text_offset < 0
                or not 1 <= max_chars <= 12000
            ):
                raise DataContractError("Note text page is outside its bounds")
            block = revision.blocks[block_index]
            display_text = self._display_text(block.body)
            if text_offset > len(display_text):
                raise DataContractError("Note text offset is outside this block")
            end = min(len(display_text), text_offset + max_chars)
            result["content"] = {
                "block_index": block_index,
                "speaker_kind": block.speaker_kind.value,
                "speaker_label": self._display_text(block.speaker_label),
                "section_date": block.section_date,
                "text": display_text[text_offset:end],
                "content_redacted": display_text != block.body,
                "offset_basis": "REDACTED_DISPLAY_TEXT",
                "text_offset": text_offset,
                "next_text_offset": end if end < len(display_text) else None,
                "next_block_index": block_index + 1
                if end == len(display_text) and block_index + 1 < len(revision.blocks)
                else None,
            }
        if journal_id is not None:
            with self._uow() as uow:
                journal = uow.journal.get(journal_id)
            linked = self._notes.revision_by_id(journal.related_entity_id or "")
            if (
                journal.related_entity_type != "external_note_revision"
                or linked is None
                or linked.note_id != revision.note_id
                or linked.observed_at > now
                or linked.coverage is not NoteCoverage.FULL
                or journal.created_at > now
            ):
                raise DataContractError("Discussion does not belong to this note")
            if include_content:
                discussion_text = self._display_text(journal.body_markdown)
                if (
                    text_offset < 0
                    or text_offset > len(discussion_text)
                    or not 1 <= max_chars <= 12000
                ):
                    raise DataContractError("Discussion text page is outside its bounds")
                end = min(len(discussion_text), text_offset + max_chars)
                result["discussion_content"] = {
                    "journal_id": journal_id,
                    "note_revision_id": journal.related_entity_id,
                    "created_at": journal.created_at,
                    "text": discussion_text[text_offset:end],
                    "text_offset": text_offset,
                    "next_text_offset": end if end < len(discussion_text) else None,
                }
        try:
            revisions = self._notes.list_revisions(revision.note_id, limit=51)
            ids = tuple(r.note_revision_id for r in revisions[:50] if r.observed_at <= now)
            with self._uow() as uow:
                discussion = uow.journal.list_related("external_note_revision", ids, limit=51)
            result["discussion_history"] = [
                {
                    "journal_id": j.journal_id,
                    "note_revision_id": j.related_entity_id,
                    "title": self._display_text(j.title),
                    "created_at": j.created_at,
                    "confirmed_by": j.confirmed_by,
                    "supersedes_journal_id": j.supersedes_journal_id,
                    "body_preview": self._display_text(j.body_markdown)[:1000]
                    if include_content and private_content_authorized
                    else None,
                    "body_truncated": len(j.body_markdown) > 1000,
                }
                for j in discussion[:10]
                if j.created_at <= now
            ]
            result["discussion_coverage"] = (
                "PARTIAL" if len(discussion) > 10 or len(revisions) > 50 else "COMPLETE"
            )
        except Exception:  # noqa: BLE001 - history failure must not block reading source text
            result["discussion_history"] = []
            result["discussion_coverage"] = "UNAVAILABLE"
        result["recording_hint"] = (
            {
                "tool": "research_memory_append",
                "operation": "journal",
                "case_id": None,
                "related_entity_type": "external_note_revision",
                "related_entity_id": note_revision_id,
                "requires_explicit_user_request": True,
            }
            if revision.coverage is NoteCoverage.FULL
            else None
        )
        result["formal_judgment_updated"] = False
        return result
