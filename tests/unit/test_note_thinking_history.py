"""Personal records and note timelines use durable facts, never inferred judgments."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from test_note_desk import NOTE, NOW, REV, revision

from application.services.journal_service import JournalService
from application.services.note_desk_service import NoteDeskService
from application.services.note_history_service import NoteHistoryService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import JournalEntryType
from domain.common.errors import DataContractError
from domain.external_note.enums import NoteCoverage
from domain.external_note.models import ExternalNoteIdentity
from infrastructure.persistence.external_note_repository import SqlAlchemyExternalNoteRepository
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

STOCK = "equity:US:SYNM"


def test_personal_record_and_cross_note_history_preserve_scope_privacy_and_recovery(
    migrated_sqlite_url,
    monkeypatch,
):
    engine = create_engine(migrated_sqlite_url)
    notes = SqlAlchemyExternalNoteRepository(engine)
    clock, ids, redactor = FixedClock(NOW), SequentialIdGenerator(), DefaultSecretRedactor()

    def factory():
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    def seed(note_id, instrument, source="MOOMOO_NOTE"):
        notes.append_identity(
            ExternalNoteIdentity(
                note_id,
                source,
                note_id,
                "Synthetic",
                instrument,
                NOW,
                NOW,
            )
        )

    try:
        seed(NOTE, STOCK)
        seed("synthetic_other_note", STOCK)
        seed("synthetic_unrelated", "equity:US:OTHER")
        seed("synthetic_other_source", STOCK, "OTHER_SOURCE")
        first = replace(revision(), observed_at=NOW - timedelta(days=5))
        notes.append_revision(first)
        notes.append_revision(
            replace(
                first,
                note_revision_id="synthetic_later",
                version=2,
                source_revision_key="v2",
                observed_at=NOW - timedelta(days=3),
            )
        )
        for identity, revision_id, at in (
            ("synthetic_other_note", "synthetic_other_revision", NOW - timedelta(days=2)),
            ("synthetic_unrelated", "synthetic_unrelated_revision", NOW - timedelta(days=1)),
            ("synthetic_other_source", "synthetic_other_source_revision", NOW),
            (NOTE, "synthetic_future", NOW + timedelta(days=1)),
        ):
            notes.append_revision(
                replace(
                    first,
                    note_id=identity,
                    note_revision_id=revision_id,
                    version=3,
                    source_revision_key=revision_id,
                    observed_at=at,
                )
            )
        journal = JournalService(factory, clock, ids, redactor, external_notes=notes)
        args = dict(
            subject_id=None,
            entry_type=JournalEntryType.NOTE,
            title="Personal conclusion",
            body_markdown="Synthetic uncertainty; https://synthetic.invalid/private",
            authored_by="user",
            confirmed_by="user",
            instrument_ids=(),
            topic_tags=("personal_conclusion_v1",),
            related_entity_type="external_note_revision",
            related_entity_id=REV,
            supersedes_journal_id=None,
            idempotency_key="synthetic-save",
        )
        saved = journal.append(**args)
        assert saved.ok
        assert journal.append(**args).data.journal_id == saved.data.journal_id
        assert not journal.append(**{**args, "body_markdown": "Changed request"}).ok
        desk = NoteDeskService(notes, factory, clock, redactor)
        receipt = desk.conclusion_receipt(REV, "synthetic-save")
        assert receipt["journal_id"] == saved.data.journal_id
        assert receipt["formal_judgment_updated"] is False
        assert "body" not in str(receipt)
        assert desk.conclusion_receipt(REV, "absent")["status"] == "NOT_FOUND"
        with pytest.raises(DataContractError):
            desk.conclusion_receipt("synthetic_other_revision", "synthetic-save")
        monkeypatch.setattr(NoteHistoryService, "PAGE_SIZE", 2)
        page = desk.history(REV)
        assert page["scope"] == "INSTRUMENT"
        assert page["items"][0]["kind"] == "PERSONAL_CONCLUSION"
        assert "Synthetic uncertainty" not in str(page) and "My uncertain thinking" not in str(page)
        assert page["window_end"] == NOW
        cursor = page["next_cursor"]
        older = desk.history(REV, cursor=cursor)
        ids_seen = [row["event_id"] for row in page["items"] + older["items"]]
        assert ids_seen == [
            saved.data.journal_id,
            "synthetic_other_revision",
            "synthetic_later",
            REV,
        ]
        assert older["next_cursor"] is None
        content = desk.note(
            REV,
            journal_id=saved.data.journal_id,
            include_content=True,
            private_content_authorized=True,
        )
        assert "synthetic.invalid" not in content["discussion_content"]["text"]
        assert content["discussion_history"][0]["entry_kind"] == "PERSONAL_CONCLUSION"
        journal.append(**{**args, "idempotency_key": "new-save", "title": "Later thought"})
        with pytest.raises(DataContractError, match="History changed"):
            desk.history(REV, cursor=cursor)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM investment_cases")) == 0
            assert connection.scalar(text("SELECT count(*) FROM decision_records")) == 0
        # An unmapped note scopes to itself, not every unmapped note.
        notes.update_identity(replace(notes.get(NOTE), primary_instrument_id=None))
        assert desk.history(REV)["scope"] == "NOTE"
        assert all(
            row["event_id"] != "synthetic_other_revision" for row in desk.history(REV)["items"]
        )
    finally:
        engine.dispose()


def test_history_metadata_keeps_unreadable_bodies_and_reports_bounded_and_failed_sources(
    migrated_sqlite_url,
    monkeypatch,
):
    from infrastructure.persistence.repositories.journal import SqlAlchemyJournalRepository

    engine = create_engine(migrated_sqlite_url)
    notes = SqlAlchemyExternalNoteRepository(engine)
    clock, ids, redactor = FixedClock(NOW), SequentialIdGenerator(), DefaultSecretRedactor()
    try:
        notes.append_identity(
            ExternalNoteIdentity(NOTE, "MOOMOO_NOTE", "synthetic", "T", STOCK, NOW, NOW)
        )
        notes.append_revision(revision())
        notes.append_revision(
            replace(
                revision(),
                version=2,
                note_revision_id="older",
                source_revision_key="older",
                observed_at=NOW - timedelta(days=2),
                coverage=NoteCoverage.SUMMARY_ONLY,
                blocks=(),
                full_body=None,
            )
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE external_note_revisions SET blocks_json = 'broken' "
                    "WHERE note_revision_id = 'older'"
                )
            )
        rows, more = notes.history_metadata(
            note_id=NOTE, instrument_id=STOCK, observed_through=NOW, limit=1
        )
        assert len(rows) == 1 and more

        def unavailable(*args, **kwargs):
            raise RuntimeError("Synthetic private details")

        monkeypatch.setattr(SqlAlchemyJournalRepository, "list_related", unavailable)
        desk = NoteDeskService(
            notes,
            lambda: SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor),
            clock,
            redactor,
        )
        result = desk.history(REV)
        assert result["journals_status"] == "UNAVAILABLE" and result["coverage"] == "PARTIAL"
        assert len(result["items"]) == 2 and "Synthetic private details" not in str(result)
    finally:
        engine.dispose()
