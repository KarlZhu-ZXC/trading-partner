"""Phase 1C C4b2 unit tests for JournalService."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research_memory import ResearchSearchQuery
from application.dto.tool_envelope import DUPLICATE_IDEMPOTENCY_KEY
from application.services._research_memory_write_support import (
    compute_journal_idempotency_payload_sha256,
)
from application.services.evidence_service import EvidenceService
from application.services.journal_service import JournalService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    JournalEntryType,
    ReliabilityLevel,
    ResearchSearchEntityType,
    ResearchSubjectStatus,
    ResearchSubjectType,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject
from infrastructure.persistence.orm import JournalEntryRow, SystemAuditLogRow
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
FUTURE = NOW + timedelta(hours=3)
A_SHARE = "equity:A_SHARE:600519.SH"
US = "equity:US:NVDA"


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "journal-service-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "journal-service-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def harness(tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "journal_svc.db"
    database_url = f"sqlite:///{path}"
    _set_test_env(monkeypatch, database_url)
    command.upgrade(_alembic_config(database_url, project_root), "head")
    eng = create_engine(database_url)
    _enable_fk(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    journal = JournalService(factory, clock, ids, redactor)
    evidence = EvidenceService(factory, clock, ids, redactor)
    yield journal, evidence, factory, clock, ids, eng
    eng.dispose()


def _create_subject(factory, ids, clock) -> str:  # type: ignore[no-untyped-def]
    subject = ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="Case",
        summary="Summary",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id=US,
        topic_tags=("ai",),
        created_at=clock.now(),
        updated_at=clock.now(),
        created_by="user",
        archived_at=None,
        archived_reason=None,
        linked_subject_ids=(),
        evidence_ids=(),
        report_ids=(),
        event_ids=(),
        decision_ids=(),
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.subjects.add(subject)
        uow.commit()
    return subject.subject_id


def _base_append(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "subject_id": None,
        "entry_type": JournalEntryType.NOTE,
        "title": "Note title",
        "body_markdown": "Body with api_key=should_redact_if_mapped",
        "authored_by": "codex",
        "confirmed_by": "user",
        "instrument_ids": (US,),
        "topic_tags": ("AI", "  ai ", "gpu"),
        "related_entity_type": None,
        "related_entity_id": None,
        "supersedes_journal_id": None,
        "idempotency_key": "  journal-1  ",
    }
    base.update(overrides)
    return base


def test_append_happy_path_redaction_and_topic_dedupe(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    env = journal.append(**_base_append(subject_id=subject_id, body_markdown="plain body"))
    assert env.ok and env.data is not None
    assert env.data.topic_tags == ("ai", "gpu")
    assert env.data.created_at == NOW
    assert env.data.confirmed_by == "user"
    assert env.data.instrument_ids == (US,)

    with Session(eng) as session:
        rows = session.scalars(select(SystemAuditLogRow)).all()
        assert len(rows) == 1
        payload = json.loads(rows[0].payload_json)
        assert rows[0].event_type == "phase1c.journal.appended"
        assert payload["idempotency_key"] == "journal-1"
        assert "plain body" not in rows[0].payload_json
        assert payload["content_sha256"] is not None
        assert len(payload["content_sha256"]) == 64


def test_confirmed_by_gate_rejects_codex(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    env = journal.append(**_base_append(subject_id=subject_id, confirmed_by="codex"))
    assert env.ok is False
    assert any(e.code == "UNAUTHORIZED_REVIEWER" for e in env.errors)


def test_same_key_same_payload_after_clock_advance(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    first = journal.append(**_base_append(subject_id=subject_id, body_markdown="stable"))
    assert first.ok and first.data is not None
    jid = first.data.journal_id
    created = first.data.created_at

    clock.advance(3600)
    second = journal.append(**_base_append(subject_id=subject_id, body_markdown="stable"))
    assert second.ok is True
    assert second.degraded is True
    assert DUPLICATE_IDEMPOTENCY_KEY in second.warnings
    assert second.data is not None
    assert second.data.journal_id == jid
    assert second.data.created_at == created

    with Session(eng) as session:
        assert session.scalars(select(JournalEntryRow)).all().__len__() == 1
        audits = session.scalars(select(SystemAuditLogRow)).all()
        assert len(audits) == 1  # no second audit on duplicate


def test_same_key_different_payload_conflicts(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    first = journal.append(**_base_append(subject_id=subject_id, body_markdown="one"))
    assert first.ok
    conflict = journal.append(**_base_append(subject_id=subject_id, body_markdown="two"))
    assert conflict.ok is False
    assert any(e.code == "DUPLICATE_IDEMPOTENCY_KEY" for e in conflict.errors)


def test_normalized_tuple_and_redaction_hash_coherence(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    # First write redacts password-like free text if redactor maps it;
    # tuple order/dedupe must match stored payload for idempotency recompute.
    env = journal.append(
        **_base_append(
            subject_id=subject_id,
            instrument_ids=(US, "  ", US, A_SHARE),
            topic_tags=("Alpha", "alpha", "Beta"),
            body_markdown="password=hunter2 should be scrubbed if mapped",
            idempotency_key="Hash-Coherence",
        )
    )
    assert env.ok and env.data is not None
    stored = env.data
    recomputed = compute_journal_idempotency_payload_sha256(
        subject_id=stored.subject_id,
        entry_type=JournalEntryType(stored.entry_type)
        if not isinstance(stored.entry_type, JournalEntryType)
        else stored.entry_type,
        title=stored.title,
        body_markdown=stored.body_markdown,
        authored_by=stored.authored_by,
        confirmed_by=stored.confirmed_by,
        instrument_ids=stored.instrument_ids,
        topic_tags=stored.topic_tags,
        related_entity_type=stored.related_entity_type,
        related_entity_id=stored.related_entity_id,
        supersedes_journal_id=stored.supersedes_journal_id,
    )
    with Session(eng) as session:
        row = session.get(JournalEntryRow, stored.journal_id)
        assert row is not None
        assert row.idempotency_payload_sha256 == recomputed
        assert row.idempotency_key == "hash-coherence"
    assert stored.instrument_ids == (US, A_SHARE)
    assert stored.topic_tags == ("alpha", "beta")


def test_global_journal_only_relates_to_global_journal(harness) -> None:  # type: ignore[no-untyped-def]
    journal, evidence, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    case_j = journal.append(
        **_base_append(
            subject_id=subject_id,
            body_markdown="case note",
            idempotency_key="case-j1",
            instrument_ids=(),
        )
    )
    assert case_j.ok and case_j.data
    global_a = journal.append(
        **_base_append(
            subject_id=None,
            body_markdown="global a",
            idempotency_key="global-a",
            instrument_ids=(),
        )
    )
    assert global_a.ok and global_a.data

    # Global → case-scoped journal rejected
    bad = journal.append(
        **_base_append(
            subject_id=None,
            body_markdown="global bad",
            idempotency_key="global-bad",
            instrument_ids=(),
            related_entity_type="journal",
            related_entity_id=case_j.data.journal_id,
        )
    )
    assert bad.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in bad.errors)

    # Global → non-journal type rejected
    bad2 = journal.append(
        **_base_append(
            subject_id=None,
            body_markdown="global bad2",
            idempotency_key="global-bad2",
            instrument_ids=(),
            related_entity_type="case",
            related_entity_id=subject_id,
        )
    )
    assert bad2.ok is False

    # Global → global journal ok
    ok = journal.append(
        **_base_append(
            subject_id=None,
            body_markdown="global b",
            idempotency_key="global-b",
            instrument_ids=(),
            related_entity_type="journal",
            related_entity_id=global_a.data.journal_id,
        )
    )
    assert ok.ok is True


def test_cross_case_and_future_related_refs_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    journal, evidence, factory, clock, ids, _eng = harness
    case_a = _create_subject(factory, ids, clock)
    case_b = _create_subject(factory, ids, clock)
    ev_b = evidence.record_evidence(
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="other case ev",
        summary="s",
        content_text="c",
        structured_data_json=None,
        source_name="mock",
        source_vendor="mock_us",
        source_record_id=None,
        source_url=None,
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(US,),
        topic_tags=(),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.5"),
        supersedes_evidence_id=None,
        recorded_by="provider:mock_us",
        subject_ids=(case_b,),
        observed_at=EARLIER,
    )
    assert ev_b.ok and ev_b.data

    cross = journal.append(
        **_base_append(
            subject_id=case_a,
            body_markdown="cross",
            idempotency_key="cross-ev",
            related_entity_type="evidence",
            related_entity_id=ev_b.data.evidence_id,
        )
    )
    assert cross.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in cross.errors)

    # Evidence observed/linked in the future relative to journal created_at=NOW.
    clock.set(FUTURE)
    ev_future = evidence.record_evidence(
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="future ev",
        summary="s",
        content_text="c",
        structured_data_json=None,
        source_name="mock",
        source_vendor="mock_us",
        source_record_id=None,
        source_url=None,
        published_at=FUTURE,
        effective_from=None,
        effective_to=None,
        instrument_ids=(US,),
        topic_tags=(),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.5"),
        supersedes_evidence_id=None,
        recorded_by="provider:mock_us",
        subject_ids=(case_a,),
        observed_at=FUTURE,
    )
    assert ev_future.ok and ev_future.data
    clock.set(NOW)
    future = journal.append(
        **_base_append(
            subject_id=case_a,
            body_markdown="future ref",
            idempotency_key="future-ev",
            related_entity_type="evidence",
            related_entity_id=ev_future.data.evidence_id,
        )
    )
    assert future.ok is False
    assert any(e.code == "HISTORICAL_VISIBILITY_VIOLATION" for e in future.errors)


def test_supersedes_same_case_and_time_rules(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    old = journal.append(
        **_base_append(
            subject_id=subject_id,
            body_markdown="old",
            idempotency_key="old-j",
        )
    )
    assert old.ok and old.data
    clock.advance(10)
    good = journal.append(
        **_base_append(
            subject_id=subject_id,
            body_markdown="new",
            idempotency_key="new-j",
            supersedes_journal_id=old.data.journal_id,
        )
    )
    assert good.ok is True

    other = _create_subject(factory, ids, clock)
    cross = journal.append(
        **_base_append(
            subject_id=other,
            body_markdown="cross supersede",
            idempotency_key="cross-sup",
            supersedes_journal_id=old.data.journal_id,
        )
    )
    assert cross.ok is False


def test_search_projection_failure_full_rollback(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    real_factory = factory

    class BoomUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner

        def __enter__(self) -> BoomUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def search_index(self) -> Any:
            mock = MagicMock()
            mock.index.side_effect = RuntimeError("search boom")
            return mock

    boom = JournalService(
        lambda: BoomUow(real_factory()),
        clock,
        SequentialIdGenerator(start=5000),
        DefaultSecretRedactor(),
    )
    env = boom.append(
        **_base_append(
            subject_id=subject_id,
            body_markdown="should vanish",
            title="rollback-journal",
            idempotency_key="rb-j",
        )
    )
    assert env.ok is False
    with Session(eng) as session:
        titles = session.scalars(select(JournalEntryRow.title)).all()
        assert "rollback-journal" not in titles
        assert session.scalars(select(SystemAuditLogRow)).all() == []


def test_audit_failure_full_rollback(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    real_factory = factory

    class BoomUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner

        def __enter__(self) -> BoomUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def audit(self) -> Any:
            mock = MagicMock()
            mock.append.side_effect = RuntimeError("audit boom")
            return mock

    boom = JournalService(
        lambda: BoomUow(real_factory()),
        clock,
        SequentialIdGenerator(start=6000),
        DefaultSecretRedactor(),
    )
    env = boom.append(
        **_base_append(
            subject_id=subject_id,
            title="audit-rb-journal",
            body_markdown="x",
            idempotency_key="audit-rb-j",
        )
    )
    assert env.ok is False
    with Session(eng) as session:
        assert "audit-rb-journal" not in session.scalars(select(JournalEntryRow.title)).all()
        count = session.execute(text("SELECT COUNT(*) FROM research_search_documents")).scalar_one()
        assert count == 0


def test_search_rejects_when_all_effective_filters_absent(harness) -> None:  # type: ignore[no-untyped-def]
    """Forced JOURNAL entity type must not count as a caller filter (§8.5)."""
    journal, _ev, _factory, _clock, _ids, _eng = harness

    none_text = journal.search(
        text=None,
        subject_id=None,
        instrument_id=None,
        entry_types=(),
        as_of=None,
        limit=10,
        offset=0,
    )
    assert none_text.ok is False
    assert any(e.code == "INPUT_VALIDATION_ERROR" for e in none_text.errors)

    blank_text = journal.search(
        text="   \t  ",
        subject_id=None,
        instrument_id=None,
        entry_types=(),
        as_of=None,
        limit=10,
        offset=0,
    )
    assert blank_text.ok is False
    assert any(e.code == "INPUT_VALIDATION_ERROR" for e in blank_text.errors)

    # as_of alone is a valid effective filter.
    as_of_only = journal.search(
        text=None,
        subject_id=None,
        instrument_id=None,
        entry_types=(),
        as_of=NOW,
        limit=10,
        offset=0,
    )
    assert as_of_only.ok is True
    assert as_of_only.data is not None
    assert as_of_only.data.total == 0


def test_same_key_tuple_order_only_is_duplicate_not_conflict(harness) -> None:  # type: ignore[no-untyped-def]
    """instrument_ids/topic_tags order differs only in hash; domain keeps first write."""
    journal, _ev, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)

    first = journal.append(
        **_base_append(
            subject_id=subject_id,
            body_markdown="order-stable body",
            instrument_ids=(US, A_SHARE),
            topic_tags=("gpu", "ai"),
            idempotency_key="order-j-key",
        )
    )
    assert first.ok and first.data is not None
    jid = first.data.journal_id
    assert first.data.instrument_ids == (US, A_SHARE)
    assert first.data.topic_tags == ("gpu", "ai")

    with factory() as uow:
        page_before = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject_id,
                entity_types=(ResearchSearchEntityType.JOURNAL,),
            )
        )
        total_before = page_before.total

    clock.advance(60)
    second = journal.append(
        **_base_append(
            subject_id=subject_id,
            body_markdown="order-stable body",
            instrument_ids=(A_SHARE, US),
            topic_tags=("ai", "gpu"),
            idempotency_key="order-j-key",
        )
    )
    assert second.ok is True
    assert second.degraded is True
    assert DUPLICATE_IDEMPOTENCY_KEY in second.warnings
    assert second.data is not None
    assert second.data.journal_id == jid
    # Persisted first-seen order from first write, not retry order.
    assert second.data.instrument_ids == (US, A_SHARE)
    assert second.data.topic_tags == ("gpu", "ai")

    with factory() as uow:
        stored = uow.journal.get(jid)
        assert stored.instrument_ids == (US, A_SHARE)
        assert stored.topic_tags == ("gpu", "ai")
        page_after = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject_id,
                entity_types=(ResearchSearchEntityType.JOURNAL,),
            )
        )
        assert page_after.total == total_before

    with Session(eng) as session:
        assert len(session.scalars(select(JournalEntryRow)).all()) == 1
        audits = [
            a
            for a in session.scalars(select(SystemAuditLogRow)).all()
            if a.event_type == "phase1c.journal.appended"
        ]
        assert len(audits) == 1


def test_search_type_text_as_of_pagination(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)

    clock.set(NOW - timedelta(hours=2))
    j1 = journal.append(
        **_base_append(
            subject_id=subject_id,
            entry_type=JournalEntryType.NOTE,
            title="alpha note unique",
            body_markdown="body alpha uniquephrase",
            idempotency_key="s1",
            instrument_ids=(US,),
        )
    )
    clock.set(NOW - timedelta(hours=1))
    j2 = journal.append(
        **_base_append(
            subject_id=subject_id,
            entry_type=JournalEntryType.POSTMORTEM,
            title="beta postmortem",
            body_markdown="body beta uniquephrase",
            idempotency_key="s2",
            instrument_ids=(US,),
        )
    )
    clock.set(NOW)
    j3 = journal.append(
        **_base_append(
            subject_id=subject_id,
            entry_type=JournalEntryType.NOTE,
            title="gamma note",
            body_markdown="body gamma other",
            idempotency_key="s3",
            instrument_ids=(A_SHARE,),
        )
    )
    assert j1.ok and j2.ok and j3.ok

    by_type = journal.search(
        text=None,
        subject_id=subject_id,
        instrument_id=None,
        entry_types=(JournalEntryType.NOTE,),
        as_of=None,
        limit=10,
        offset=0,
    )
    assert by_type.ok and by_type.data is not None
    assert by_type.data.total == 2
    assert all(e.entry_type in {JournalEntryType.NOTE, "note"} for e in by_type.data.items)

    by_text = journal.search(
        text="uniquephrase",
        subject_id=subject_id,
        instrument_id=None,
        entry_types=(),
        as_of=None,
        limit=10,
        offset=0,
    )
    assert by_text.ok and by_text.data is not None
    assert by_text.data.total == 2

    as_of_mid = journal.search(
        text=None,
        subject_id=subject_id,
        instrument_id=None,
        entry_types=(),
        as_of=NOW - timedelta(hours=1, minutes=30),
        limit=10,
        offset=0,
    )
    assert as_of_mid.ok and as_of_mid.data is not None
    assert as_of_mid.data.total == 1
    assert as_of_mid.data.items[0].journal_id == j1.data.journal_id

    page0 = journal.search(
        text=None,
        subject_id=subject_id,
        instrument_id=None,
        entry_types=(),
        as_of=None,
        limit=1,
        offset=0,
    )
    page1 = journal.search(
        text=None,
        subject_id=subject_id,
        instrument_id=None,
        entry_types=(),
        as_of=None,
        limit=1,
        offset=1,
    )
    assert page0.ok and page1.ok
    assert page0.data is not None and page1.data is not None
    assert page0.data.total == 3
    assert page0.data.has_more is True
    assert len(page0.data.items) == 1
    assert page1.data.items[0].journal_id != page0.data.items[0].journal_id

    by_inst = journal.search(
        text=None,
        subject_id=subject_id,
        instrument_id=A_SHARE,
        entry_types=(),
        as_of=None,
        limit=10,
        offset=0,
    )
    assert by_inst.ok and by_inst.data is not None
    assert by_inst.data.total == 1
    assert by_inst.data.items[0].journal_id == j3.data.journal_id


def test_us_and_a_share_instruments_preserved(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _ev, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    env = journal.append(
        **_base_append(
            subject_id=subject_id,
            instrument_ids=(A_SHARE, US),
            body_markdown="both markets",
            idempotency_key="markets",
        )
    )
    assert env.ok and env.data is not None
    assert env.data.instrument_ids == (A_SHARE, US)
