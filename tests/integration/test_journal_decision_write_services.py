"""Phase 1C C4b2 integration: JournalService + DecisionRecordService end-to-end."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.tool_envelope import DUPLICATE_IDEMPOTENCY_KEY
from application.services.decision_record_service import DecisionRecordService
from application.services.evidence_service import EvidenceService
from application.services.journal_service import JournalService
from application.services.research_archive_service import ResearchArchiveService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    InvestmentCaseStatus,
    InvestmentCaseType,
    JournalEntryType,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, InvestmentCase
from infrastructure.persistence.orm import (
    DecisionRecordRow,
    JournalEntryRow,
    SystemAuditLogRow,
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
    monkeypatch.setenv("APP_NAME", "c4b2-write-integration")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "c4b2-write-integration")
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
    path = tmp_path / "c4b2_write.db"
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
        JournalService(factory, clock, ids, redactor),
        DecisionRecordService(factory, clock, ids, redactor),
        EvidenceService(factory, clock, ids, redactor),
        ResearchArchiveService(factory, clock, ids, redactor),
        factory,
        clock,
        ids,
        eng,
    )
    eng.dispose()


def _seed_case(factory, ids, clock) -> str:  # type: ignore[no-untyped-def]
    case = InvestmentCase(
        case_id=ids.new(EntityIdPrefix.CASE),
        case_type=InvestmentCaseType.COMPANY,
        title="Integration case",
        summary="C4b2 flow",
        status=InvestmentCaseStatus.ACTIVE,
        primary_instrument_id=US,
        topic_tags=("integration",),
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
    with factory() as uow:
        uow.cases.add(case)
        uow.commit()
    return case.case_id


def test_full_journal_decision_pipeline(harness) -> None:  # type: ignore[no-untyped-def]
    journal, decision, evidence, archive, factory, clock, ids, eng = harness
    case_id = _seed_case(factory, ids, clock)

    ev = evidence.record_evidence(
        evidence_type=EvidenceType.A_SHARE_ANNOUNCEMENT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="茅台公告",
        summary="业绩",
        content_text="详情",
        structured_data_json=None,
        source_name="eastmoney",
        source_vendor="eastmoney",
        source_record_id="x1",
        source_url=None,
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(A_SHARE, US),
        topic_tags=("a股",),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.8"),
        supersedes_evidence_id=None,
        recorded_by="provider:eastmoney",
        case_ids=(case_id,),
        observed_at=EARLIER,
    )
    assert ev.ok and ev.data

    rep = archive.archive_report(
        case_id=case_id,
        report_type=ResearchReportType.AD_HOC,
        title="报告",
        summary="总结",
        content_markdown="正文",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(ev.data.evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert rep.ok and rep.data

    event = archive.record_event(
        case_id=case_id,
        event_type=ResearchEventType.COMPANY,
        title="事件",
        summary="发生",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(A_SHARE,),
        evidence_ids=(ev.data.evidence_id,),
        report_ids=(rep.data.report_id,),
        related_entity_type=None,
        related_entity_id=None,
        source_name="news",
        recorded_by="user",
    )
    assert event.ok and event.data

    j_note = journal.append(
        case_id=case_id,
        entry_type=JournalEntryType.NOTE,
        title="研究备注 uniquejournalphrase",
        body_markdown="与证据相关 uniquejournalphrase",
        authored_by="codex",
        confirmed_by="user",
        instrument_ids=(A_SHARE, US),
        topic_tags=("复盘",),
        related_entity_type="event",
        related_entity_id=event.data.event_id,
        supersedes_journal_id=None,
        idempotency_key="  J-INT-1  ",
    )
    assert j_note.ok and j_note.data is not None
    assert j_note.data.related_entity_type == "event"

    dec = decision.append(
        case_id=case_id,
        decision_type=DecisionType.HOLD,
        title="维持观察",
        rationale="证据与报告支持 HOLD 意图，不产生订单",
        decided_at=EARLIER,
        decided_by="external_agent",
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        primary_instrument_id=A_SHARE,
        thesis_revision_ids=(),
        evidence_ids=(ev.data.evidence_id,),
        report_ids=(rep.data.report_id,),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        idempotency_key="  D-INT-1  ",
    )
    assert dec.ok and dec.data is not None
    assert dec.data.execution_effect is False
    assert dec.data.primary_instrument_id == A_SHARE

    with factory() as uow:
        case = uow.cases.get(case_id)
        assert dec.data.decision_id in case.decision_ids
        assert case.updated_at == dec.data.recorded_at

    # Idempotent retries after clock advance
    clock.advance(100)
    j_dup = journal.append(
        case_id=case_id,
        entry_type=JournalEntryType.NOTE,
        title="研究备注 uniquejournalphrase",
        body_markdown="与证据相关 uniquejournalphrase",
        authored_by="codex",
        confirmed_by="user",
        instrument_ids=(A_SHARE, US),
        topic_tags=("复盘",),
        related_entity_type="event",
        related_entity_id=event.data.event_id,
        supersedes_journal_id=None,
        idempotency_key="  J-INT-1  ",
    )
    assert j_dup.ok and j_dup.degraded
    assert DUPLICATE_IDEMPOTENCY_KEY in j_dup.warnings
    assert j_dup.data is not None
    assert j_dup.data.journal_id == j_note.data.journal_id
    assert j_dup.data.created_at == j_note.data.created_at

    d_dup = decision.append(
        case_id=case_id,
        decision_type=DecisionType.HOLD,
        title="维持观察",
        rationale="证据与报告支持 HOLD 意图，不产生订单",
        decided_at=EARLIER,
        decided_by="external_agent",
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        primary_instrument_id=A_SHARE,
        thesis_revision_ids=(),
        evidence_ids=(ev.data.evidence_id,),
        report_ids=(rep.data.report_id,),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        idempotency_key="  D-INT-1  ",
    )
    assert d_dup.ok and d_dup.degraded
    assert d_dup.data is not None
    assert d_dup.data.decision_id == dec.data.decision_id

    # Journal search via structured type filter + text
    page = journal.search(
        text="uniquejournalphrase",
        case_id=case_id,
        instrument_id=None,
        entry_types=(JournalEntryType.NOTE,),
        as_of=None,
        limit=10,
        offset=0,
    )
    assert page.ok and page.data is not None
    assert page.data.total >= 1
    assert any(i.journal_id == j_note.data.journal_id for i in page.data.items)

    with Session(eng) as session:
        assert len(session.scalars(select(JournalEntryRow)).all()) == 1
        assert len(session.scalars(select(DecisionRecordRow)).all()) == 1
        audits = session.scalars(select(SystemAuditLogRow)).all()
        joined = "\n".join(a.payload_json for a in audits)
        assert "不产生订单" not in joined  # rationale not audited
        assert "与证据相关" not in joined  # journal body not audited
        j_audits = [a for a in audits if a.event_type == "phase1c.journal.appended"]
        d_audits = [a for a in audits if a.event_type == "phase1c.decision.recorded"]
        assert len(j_audits) == 1
        assert len(d_audits) == 1
        assert json.loads(j_audits[0].payload_json)["idempotency_key"] == "j-int-1"
        assert json.loads(d_audits[0].payload_json)["idempotency_key"] == "d-int-1"


def test_global_journal_and_supersede_chain(harness) -> None:  # type: ignore[no-untyped-def]
    journal, _decision, _ev, _ar, factory, clock, ids, _eng = harness
    g1 = journal.append(
        case_id=None,
        entry_type=JournalEntryType.REFLECTION,
        title="global one",
        body_markdown="global body one",
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(),
        topic_tags=(),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        idempotency_key="g1",
    )
    assert g1.ok and g1.data
    clock.advance(5)
    g2 = journal.append(
        case_id=None,
        entry_type=JournalEntryType.REFLECTION,
        title="global two",
        body_markdown="global body two",
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(),
        topic_tags=(),
        related_entity_type="journal",
        related_entity_id=g1.data.journal_id,
        supersedes_journal_id=g1.data.journal_id,
        idempotency_key="g2",
    )
    assert g2.ok and g2.data is not None
    assert g2.data.supersedes_journal_id == g1.data.journal_id

    page = journal.search(
        text="global body",
        case_id=None,
        instrument_id=None,
        entry_types=(JournalEntryType.REFLECTION,),
        as_of=None,
        limit=10,
        offset=0,
    )
    assert page.ok and page.data is not None
    # default include_superseded=False → only successor visible
    assert page.data.total == 1
    assert page.data.items[0].journal_id == g2.data.journal_id
