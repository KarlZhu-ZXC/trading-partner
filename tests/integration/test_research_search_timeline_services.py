"""Phase 1C C4b1 integration: ResearchSearchService + ResearchTimelineService."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.dto.research_memory import ResearchSearchQuery
from application.services.research_archive_service import ResearchArchiveService
from application.services.research_search_service import ResearchSearchService
from application.services.research_timeline_service import ResearchTimelineService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    InvestmentCaseStatus,
    InvestmentCaseType,
    JournalEntryType,
    ReliabilityLevel,
    ResearchReportType,
    ResearchSearchEntityType,
    ResearchTimelineEntityType,
)
from domain.common.errors import ResearchMemoryNotFound
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    CaseEvidenceLink,
    Evidence,
    InvestmentCase,
    JournalEntry,
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
    monkeypatch.setenv("APP_NAME", "search-timeline-int")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "search-timeline-int")
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
    path = tmp_path / "search_timeline.db"
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

    yield (
        ResearchSearchService(factory, clock, ids, redactor),
        ResearchTimelineService(factory, clock, ids, redactor),
        ResearchArchiveService(factory, clock, ids, redactor),
        factory,
        clock,
        ids,
    )
    eng.dispose()


def _make_case(ids: SequentialIdGenerator, clock: FixedClock) -> InvestmentCase:
    return InvestmentCase(
        case_id=ids.new(EntityIdPrefix.CASE),
        case_type=InvestmentCaseType.COMPANY,
        title="Integration case",
        summary="Summary",
        status=InvestmentCaseStatus.ACTIVE,
        primary_instrument_id=US,
        topic_tags=("ai",),
        created_at=clock.now(),
        updated_at=clock.now(),
        created_by="user",
        archived_at=None,
        archived_reason=None,
        linked_case_ids=(),
        evidence_ids=(),
        report_ids=(),
        event_ids=(),
        decision_ids=(),
        schema_version=RESEARCH_SCHEMA_VERSION,
    )


def _make_evidence(ids: SequentialIdGenerator, **overrides: Any) -> Evidence:
    base: dict[str, Any] = {
        "evidence_id": ids.new(EntityIdPrefix.EVIDENCE),
        "evidence_type": EvidenceType.MARKET_SNAPSHOT,
        "origin": EvidenceOrigin.EXTERNAL_FACT,
        "title": "US tape",
        "summary": "Close",
        "content_text": "detail",
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
        "confidence": Decimal("0.8"),
        "content_sha256": "",
        "supersedes_evidence_id": None,
        "recorded_by": "provider:mock_us",
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    if not base["content_sha256"]:
        base["content_sha256"] = compute_evidence_content_sha256(
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


def test_search_and_timeline_share_case_memory(harness) -> None:  # type: ignore[no-untyped-def]
    search, timeline, archive, factory, clock, ids = harness
    case = _make_case(ids, clock)
    evidence = _make_evidence(ids, title="A share 茅台 inventory", instrument_ids=(A_SHARE,))
    link = CaseEvidenceLink(
        link_id=ids.new(EntityIdPrefix.REV),
        case_id=case.case_id,
        evidence_id=evidence.evidence_id,
        linked_at=NOW,
        linked_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    note = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        case_id=case.case_id,
        entry_type=JournalEntryType.NOTE,
        title="Channel note",
        body_markdown="observed restocking",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE,),
        topic_tags=(),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.cases.add(case)
        uow.evidence.add(evidence)
        uow.case_evidence_links.add(link)
        uow.journal.add(
            note,
            idempotency_key="int-j1",
            idempotency_payload_sha256="1" * 64,
        )
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence.evidence_id)
        uow.search_index.index(ResearchSearchEntityType.JOURNAL, note.journal_id)
        uow.commit()

    report_env = archive.archive_report(
        case_id=case.case_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Case report",
        summary="summary",
        content_markdown="# body",
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

    search_env = search.search(
        ResearchSearchQuery(
            case_id=case.case_id,
            journal_entry_types=(JournalEntryType.NOTE,),
        )
    )
    assert search_env.ok is True
    assert search_env.data is not None
    assert search_env.data.total == 1
    assert search_env.data.items[0].entity_id == note.journal_id

    report_get = search.get_report(report_env.data.report_id)
    assert report_get.ok is True
    assert report_get.data is not None
    assert report_get.data.content_markdown == "# body"

    # Event get is available via repository after C4b1.
    missing_event = "event_00000000-0000-7000-8000-000000000099"
    with factory() as uow, pytest.raises(ResearchMemoryNotFound):
        uow.events.get(missing_event)

    tl = timeline.get_timeline(
        case_id=case.case_id,
        entity_types=(
            ResearchTimelineEntityType.EVIDENCE,
            ResearchTimelineEntityType.JOURNAL,
            ResearchTimelineEntityType.REPORT,
        ),
        as_of=NOW,
    )
    assert tl.ok is True
    assert tl.data is not None
    assert tl.data.total == 3
    entity_ids = {i.entity_id for i in tl.data.items}
    assert evidence.evidence_id in entity_ids
    assert note.journal_id in entity_ids
    assert report_env.data.report_id in entity_ids
