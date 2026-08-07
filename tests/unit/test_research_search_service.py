"""Phase 1C C4b1 unit tests for ResearchSearchService."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.dto.research_memory import ResearchSearchQuery
from application.services.research_archive_service import ResearchArchiveService
from application.services.research_search_service import ResearchSearchService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    JournalEntryType,
    ReliabilityLevel,
    ResearchReportType,
    ResearchSearchEntityType,
    ResearchSubjectStatus,
    ResearchSubjectType,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    Evidence,
    JournalEntry,
    ResearchSubject,
    SubjectEvidenceLink,
    compute_evidence_content_sha256,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
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
    monkeypatch.setenv("APP_NAME", "search-service-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "search-service-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _make_case(ids: SequentialIdGenerator, clock: FixedClock, **overrides: Any) -> ResearchSubject:
    base: dict[str, Any] = {
        "subject_id": ids.new(EntityIdPrefix.SUBJECT),
        "subject_type": ResearchSubjectType.COMPANY,
        "title": "Case",
        "summary": "Summary",
        "status": ResearchSubjectStatus.ACTIVE,
        "primary_instrument_id": US,
        "topic_tags": ("ai",),
        "created_at": clock.now(),
        "updated_at": clock.now(),
        "created_by": "user",
        "archived_at": None,
        "archived_reason": None,
        "linked_subject_ids": (),
        "evidence_ids": (),
        "report_ids": (),
        "event_ids": (),
        "decision_ids": (),
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    return ResearchSubject(**base)


def _evidence_hash(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "evidence_type": EvidenceType.MARKET_SNAPSHOT,
        "origin": EvidenceOrigin.EXTERNAL_FACT,
        "title": "title",
        "summary": "summary",
        "content_text": None,
        "structured_data_json": None,
        "source_name": "mock",
        "source_vendor": "mock",
        "source_record_id": None,
        "published_at": EARLIER,
        "effective_from": None,
        "effective_to": None,
        "instrument_ids": (US,),
    }
    base.update(overrides)
    return compute_evidence_content_sha256(**base)


def _make_evidence(ids: SequentialIdGenerator, **overrides: Any) -> Evidence:
    base: dict[str, Any] = {
        "evidence_id": ids.new(EntityIdPrefix.EVIDENCE),
        "evidence_type": EvidenceType.MARKET_SNAPSHOT,
        "origin": EvidenceOrigin.EXTERNAL_FACT,
        "title": "NVIDIA snapshot",
        "summary": "Close snapshot",
        "content_text": "price detail",
        "structured_data_json": None,
        "source_name": "mock_us",
        "source_vendor": "mock_us",
        "source_record_id": None,
        "source_url": None,
        "published_at": EARLIER,
        "observed_at": NOW,
        "effective_from": None,
        "effective_to": None,
        "instrument_ids": (US,),
        "topic_tags": ("us",),
        "quality": EvidenceQuality.PRIMARY,
        "reliability": ReliabilityLevel.HIGH,
        "confidence": Decimal("0.9"),
        "content_sha256": "",
        "supersedes_evidence_id": None,
        "recorded_by": "provider:mock_us",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    if not base["content_sha256"]:
        base["content_sha256"] = _evidence_hash(
            evidence_type=base["evidence_type"],
            origin=base["origin"],
            title=base["title"],
            summary=base["summary"],
            content_text=base["content_text"],
            structured_data_json=base["structured_data_json"],
            source_name=base["source_name"],
            source_vendor=base["source_vendor"],
            source_record_id=base["source_record_id"],
            published_at=base["published_at"],
            effective_from=base["effective_from"],
            effective_to=base["effective_to"],
            instrument_ids=base["instrument_ids"],
        )
    return Evidence(**base)


@pytest.fixture
def harness(tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "search_svc.db"
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

    search = ResearchSearchService(factory, clock, ids, redactor)
    archive = ResearchArchiveService(factory, clock, ids, redactor)
    yield search, archive, factory, clock, ids, eng
    eng.dispose()


def test_search_delegates_to_index_and_returns_page(harness) -> None:  # type: ignore[no-untyped-def]
    search, _archive, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock, primary_instrument_id=A_SHARE)
    evidence = _make_evidence(
        ids,
        title="贵州茅台发布业绩预告",
        summary="业绩预告摘要",
        content_text="半年度",
        instrument_ids=(A_SHARE,),
        source_name="mock_a_share",
        source_vendor="mock_a_share",
    )
    link = SubjectEvidenceLink(
        link_id=ids.new(EntityIdPrefix.REV),
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=NOW,
        linked_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.subjects.add(subject)
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence.evidence_id)
        uow.commit()

    env = search.search(ResearchSearchQuery(text="茅台", subject_id=subject.subject_id))
    assert env.ok is True
    assert env.data is not None
    assert env.data.total >= 1
    assert any(h.entity_id == evidence.evidence_id for h in env.data.items)
    assert env.request_id.startswith("req_")


def test_search_journal_entry_types_filter(harness) -> None:  # type: ignore[no-untyped-def]
    search, _archive, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock)
    note = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="Note",
        body_markdown="body note",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(US,),
        topic_tags=(),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    pm = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.POSTMORTEM,
        title="Postmortem",
        body_markdown="body pm",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(US,),
        topic_tags=(),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.subjects.add(subject)
        uow.journal.add(note, idempotency_key="jn", idempotency_payload_sha256="a" * 64)
        uow.journal.add(pm, idempotency_key="jp", idempotency_payload_sha256="b" * 64)
        uow.search_index.index(ResearchSearchEntityType.JOURNAL, note.journal_id)
        uow.search_index.index(ResearchSearchEntityType.JOURNAL, pm.journal_id)
        uow.commit()

    env = search.search(
        ResearchSearchQuery(
            subject_id=subject.subject_id,
            journal_entry_types=(JournalEntryType.NOTE,),
        )
    )
    assert env.ok is True
    assert env.data is not None
    assert env.data.total == 1
    assert env.data.items[0].entity_id == note.journal_id


def test_get_report_hydrates_from_repository(harness) -> None:  # type: ignore[no-untyped-def]
    search, archive, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock)
    evidence = _make_evidence(ids)
    link = SubjectEvidenceLink(
        link_id=ids.new(EntityIdPrefix.REV),
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=NOW,
        linked_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.subjects.add(subject)
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.commit()

    report_env = archive.archive_report(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Deep dive",
        summary="Structural view",
        content_markdown="# Body\nDetails",
        as_of=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert report_env.ok is True
    assert report_env.data is not None
    report_id = report_env.data.report_id

    env = search.get_report(report_id)
    assert env.ok is True
    assert env.data is not None
    assert env.data.report_id == report_id
    assert env.data.title == "Deep dive"
    assert env.data.content_markdown == "# Body\nDetails"
    assert env.data.summary == "Structural view"


def test_get_report_not_found_is_envelope_failure(harness) -> None:  # type: ignore[no-untyped-def]
    search, _archive, _factory, _clock, _ids, _eng = harness
    env = search.get_report("report_00000000-0000-7000-8000-000000000099")
    assert env.ok is False
    assert env.errors
    assert env.errors[0].code == "RESEARCH_MEMORY_NOT_FOUND"


def test_search_and_get_report_do_not_commit_or_audit(harness) -> None:  # type: ignore[no-untyped-def]
    search, archive, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock)
    evidence = _make_evidence(ids)
    link = SubjectEvidenceLink(
        link_id=ids.new(EntityIdPrefix.REV),
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=NOW,
        linked_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.subjects.add(subject)
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence.evidence_id)
        uow.commit()

    report_env = archive.archive_report(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="R",
        summary="S",
        content_markdown="# x",
        as_of=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert report_env.ok is True
    assert report_env.data is not None

    # Spy on UoW: commit must not be called by read methods.
    real_factory = factory
    commits: list[str] = []

    def spying_factory() -> SqlAlchemyResearchUnitOfWork:
        uow = real_factory()
        original_commit = uow.commit

        def tracked_commit() -> None:
            commits.append("commit")
            original_commit()

        uow.commit = tracked_commit  # type: ignore[method-assign]
        return uow

    search_ro = ResearchSearchService(spying_factory, clock, ids, DefaultSecretRedactor())
    env_search = search_ro.search(ResearchSearchQuery(subject_id=subject.subject_id))
    env_report = search_ro.get_report(report_env.data.report_id)
    assert env_search.ok is True
    assert env_report.ok is True
    assert commits == []


def test_search_maps_domain_errors(harness) -> None:  # type: ignore[no-untyped-def]
    search, _archive, _factory, clock, ids, _eng = harness
    broken_index = MagicMock()
    broken_index.search.side_effect = RuntimeError("boom")

    class _Uow:
        def __enter__(self) -> _Uow:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        @property
        def search_index(self) -> MagicMock:
            return broken_index

    svc = ResearchSearchService(
        lambda: _Uow(),
        clock,
        ids,
        DefaultSecretRedactor(),  # type: ignore[arg-type,return-value]
    )
    query = ResearchSearchQuery(
        text="x",
        subject_id="case_00000000-0000-7000-8000-000000000001",
    )
    env = svc.search(query)
    assert env.ok is False
    assert env.errors
