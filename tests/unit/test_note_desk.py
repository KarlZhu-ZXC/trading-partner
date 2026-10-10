from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text

from application.services.journal_service import JournalService
from application.services.note_desk_service import NoteDeskService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import JournalEntryType
from domain.common.errors import DataContractError
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import (
    AttributedNoteBlock,
    ExternalNoteIdentity,
    ExternalNoteRevision,
)
from infrastructure.persistence.external_note_repository import SqlAlchemyExternalNoteRepository
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.agent.capability_gateway import (
    AgentCapabilityAccessDeniedError,
    AgentCapabilityGateway,
)
from interfaces.mcp.tools.view_review import build_view_review_adapters

NOW = datetime(2026, 10, 10, tzinfo=UTC)
SUFFIX = "00000000-0000-7000-8000-000000000099"
NOTE = f"external_note_{SUFFIX}"
REV = f"external_note_revision_{SUFFIX}"


def revision():
    return ExternalNoteRevision(
        note_revision_id=REV,
        note_id=NOTE,
        version=1,
        content_sha256="a" * 64,
        source_revision_key="synthetic",
        title="Synthetic note",
        summary="Private summary",
        full_body="My uncertain thinking\nQuoted opinion",
        coverage=NoteCoverage.FULL,
        source_timestamp=NOW,
        observed_at=NOW,
        visibility="PRIVATE",
        related_provider_stock_ids=(),
        related_provider_codes=(),
        blocks=(
            AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "My uncertain thinking"),
            AttributedNoteBlock(
                1, NoteSpeakerKind.NAMED_PERSON, "Quoted speaker", "Quoted opinion"
            ),
        ),
    )


def setup():
    notes, uow = MagicMock(), MagicMock()
    r = revision()
    identity = ExternalNoteIdentity(
        NOTE, "MOOMOO_NOTE", "synthetic", "Synthetic note", None, NOW, NOW
    )
    notes.revision_by_id.return_value = r
    notes.get.return_value = identity
    notes.list_latest.return_value = ((identity, r),)
    notes.previous_revision.return_value = None
    notes.list_revisions.return_value = (r,)
    uow.__enter__.return_value = uow
    uow.journal.list_related.return_value = ()
    return notes, uow, NoteDeskService(notes, lambda: uow, FixedClock(NOW), DefaultSecretRedactor())


def test_note_read_does_not_require_model_or_subject_and_keeps_speakers_separate():
    notes, uow, service = setup()
    result = service.inbox()
    assert result["items"][0]["requires_status_update"] is False
    assert "Private summary" not in str(result) and "My uncertain thinking" not in str(result)
    assert service.note(REV)["content"] is None
    with pytest.raises(DataContractError):
        service.note(REV, include_content=True)
    page = service.note(REV, include_content=True, private_content_authorized=True, max_chars=4)
    assert page["content"]["text"] == "My u" and page["content"]["next_text_offset"] == 4
    quote = service.note(REV, include_content=True, private_content_authorized=True, block_index=1)
    assert quote["content"]["speaker_kind"] == "NAMED_PERSON"
    notes.interpretation_for_revision.assert_not_called()
    uow.subjects.get.assert_not_called()
    uow.commit.assert_not_called()


def test_summary_only_and_bounded_histories_never_claim_complete_content():
    from dataclasses import replace

    notes, _, service = setup()
    notes.revision_by_id.return_value = replace(
        revision(), coverage=NoteCoverage.SUMMARY_ONLY, full_body=None, blocks=()
    )
    result = service.note(REV, include_content=True, private_content_authorized=True)
    assert result["content"] is None and result["blocks"] == []
    assert result["recording_hint"] is None
    notes.list_latest.return_value *= 500
    assert service.inbox()["coverage"] == "PARTIAL"


def test_discussion_is_linked_to_exact_note_without_creating_research_state(migrated_sqlite_url):
    engine = create_engine(migrated_sqlite_url)
    notes = SqlAlchemyExternalNoteRepository(engine)
    notes.append_identity(
        ExternalNoteIdentity(NOTE, "MOOMOO_NOTE", "synthetic", "Synthetic", None, NOW, NOW)
    )
    notes.append_revision(revision())
    clock, ids, redactor = FixedClock(NOW), SequentialIdGenerator(), DefaultSecretRedactor()

    def factory():
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    service = JournalService(factory, clock, ids, redactor, external_notes=notes)
    args = dict(
        subject_id=None,
        entry_type=JournalEntryType.NOTE,
        title="Explicit conversation result",
        body_markdown="Retain this uncertainty; no formal plan yet.",
        authored_by="codex",
        confirmed_by="user",
        instrument_ids=(),
        topic_tags=(),
        related_entity_type="external_note_revision",
        related_entity_id=REV,
        supersedes_journal_id=None,
        idempotency_key="note-discussion-synthetic",
    )
    first = service.append(**args)
    assert first.ok, first.errors
    assert service.append(**args).data.journal_id == first.data.journal_id
    assert not service.append(**{**args, "body_markdown": "Different"}).ok
    desk = NoteDeskService(notes, factory, clock, redactor)
    assert desk.inbox()["items"][0]["discussion"]["journal_id"] == first.data.journal_id
    assert desk.note(REV)["discussion_history"][0]["body_preview"] is None
    restored = desk.note(
        REV,
        journal_id=first.data.journal_id,
        include_content=True,
        private_content_authorized=True,
        max_chars=4,
    )
    assert restored["discussion_content"]["text"] == "Reta"
    assert restored["discussion_content"]["next_text_offset"] == 4
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM investment_cases")) == 0
        assert connection.scalar(text("SELECT count(*) FROM decision_records")) == 0
    engine.dispose()


@pytest.mark.asyncio
async def test_notes_sync_is_isolated_from_models_and_broker_refresh():
    container = MagicMock()
    container.context.clock = FixedClock(NOW)
    container.context.id_generator = SequentialIdGenerator()
    from dataclasses import dataclass

    @dataclass
    class Receipt:
        status: str = "SUCCEEDED"

    container.services.external_notes.sync = AsyncMock(return_value=Receipt())
    result = await build_view_review_adapters(container).notes_sync()
    assert result["ok"]
    container.services.external_notes.sync.assert_awaited_once_with(
        analyze=False, source_code="MOOMOO_NOTE"
    )
    container.services.accounts.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_built_in_agent_cannot_bypass_private_text_authorization():
    gateway = object.__new__(AgentCapabilityGateway)
    with pytest.raises(AgentCapabilityAccessDeniedError):
        await gateway.read(
            "view_get",
            "note",
            {"request": {"include_content": True, "private_content_authorized": True}},
        )


def test_owner_change_detection_ignores_quoted_changes_and_redacts_before_paging():
    from dataclasses import replace

    notes, _, service = setup()
    old = revision()
    changed_quote = replace(
        old,
        version=2,
        blocks=(
            old.blocks[0],
            AttributedNoteBlock(
                1, NoteSpeakerKind.NAMED_PERSON, "Quoted speaker", "Different quote"
            ),
        ),
    )
    notes.previous_revision.return_value = old
    notes.list_latest.return_value = ((notes.get.return_value, changed_quote),)
    assert service.inbox()["items"][0]["change"] == "USER_TEXT_UNCHANGED"
    changed_owner = replace(
        changed_quote,
        blocks=(AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "api_key=fixture"),),
    )
    notes.revision_by_id.return_value = changed_owner
    notes.list_latest.return_value = ((notes.get.return_value, changed_owner),)
    assert service.inbox()["items"][0]["change"] == "USER_TEXT_CHANGED"
    page = service.note(REV, include_content=True, private_content_authorized=True)
    assert "fixture" not in page["content"]["text"]
    assert page["content"]["content_redacted"]
    notes.previous_revision.side_effect = RuntimeError("private provider detail")
    assert service.inbox()["items"][0]["change"] == "PREVIOUS_CONTENT_UNAVAILABLE"


@pytest.mark.parametrize("max_bytes", [15360, 1500])
def test_mcp_note_pages_survive_compaction_without_losing_cursor_text(max_bytes):
    from dataclasses import replace

    from interfaces.shared.result_compaction import compact_tool_result, encode_result

    notes, _, service = setup()
    body = "观察" * 3000
    notes.revision_by_id.return_value = replace(
        revision(), blocks=(AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", body),)
    )
    container = MagicMock()
    container.context.clock = FixedClock(NOW)
    container.context.id_generator = SequentialIdGenerator()
    container.services.note_desk = service
    adapter = build_view_review_adapters(container)
    chunks = []
    offset = 0
    while True:
        result = adapter.note_read(
            REV, include_content=True, private_content_authorized=True, text_offset=offset
        )
        compact = compact_tool_result(
            result, capability="view_get", operation="note", max_bytes=max_bytes
        )
        assert len(encode_result(compact)) <= max_bytes
        content = compact["data"]["content"]
        chunks.append(content["text"])
        if content["next_text_offset"] is None:
            break
        offset = content["next_text_offset"]
    assert "".join(chunks) == body


def test_discussion_read_failure_does_not_block_note_or_claim_no_prior_discussion():
    _, uow, service = setup()
    uow.journal.list_related.side_effect = RuntimeError("private database details")
    assert service.inbox()["discussion_coverage"] == "UNAVAILABLE"
    data = service.note(REV, include_content=True, private_content_authorized=True)
    assert data["content"]["text"] == "My uncertain thinking"
    assert data["discussion_coverage"] == "UNAVAILABLE"
    assert "private database" not in str(data)
