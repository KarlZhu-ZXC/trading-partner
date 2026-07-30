"""Phase 1C C4a integration: EvidenceService + ResearchArchiveService end-to-end."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research_memory import ResearchSearchQuery
from application.dto.tool_envelope import DUPLICATE_CONTENT
from application.services.evidence_service import EvidenceService
from application.services.research_archive_service import ResearchArchiveService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    InvestmentCaseStatus,
    InvestmentCaseType,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, InvestmentCase
from infrastructure.persistence.orm import SystemAuditLogRow
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
    monkeypatch.setenv("APP_NAME", "c4a-write-integration")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "c4a-write-integration")
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
    path = tmp_path / "c4a_write.db"
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

    # 0003 migration already seeds A_SHARE/US instruments.
    yield (
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
        summary="C4a flow",
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


def test_full_write_pipeline_evidence_report_event(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, eng = harness
    case_id = _seed_case(factory, ids, clock)

    ev = evidence_svc.record_evidence(
        evidence_type=EvidenceType.A_SHARE_ANNOUNCEMENT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="茅台公告",
        summary="业绩预告",
        content_text="详情",
        structured_data_json=json.dumps({"api_key": "leak", "metric": 1}),
        source_name="eastmoney",
        source_vendor="eastmoney",
        source_record_id="x1",
        source_url="https://news.example/a?access_token=t#section",
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(A_SHARE, A_SHARE, "  ", US),
        topic_tags=("A股", " 公告 ", "a股"),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.75"),
        supersedes_evidence_id=None,
        recorded_by="provider:eastmoney",
        case_ids=(case_id,),
        observed_at=EARLIER,
    )
    assert ev.ok and ev.data is not None
    assert ev.data.instrument_ids == (A_SHARE, US)
    assert "leak" not in (ev.data.structured_data_json or "")
    assert "access_token" in (ev.data.source_url or "")
    assert "***REDACTED***" in (ev.data.source_url or "")
    assert "#section" not in (ev.data.source_url or "")

    # Duplicate content + second case membership
    case2 = _seed_case(factory, ids, clock)
    dup = evidence_svc.record_evidence(
        evidence_type=EvidenceType.A_SHARE_ANNOUNCEMENT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="茅台公告",
        summary="业绩预告",
        content_text="详情",
        structured_data_json=json.dumps({"api_key": "leak", "metric": 1}),
        source_name="eastmoney",
        source_vendor="eastmoney",
        source_record_id="x1",
        source_url="https://news.example/a?access_token=t#section",
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(A_SHARE, US),
        topic_tags=("A股", "公告"),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.75"),
        supersedes_evidence_id=None,
        recorded_by="provider:eastmoney",
        case_ids=(case_id, case2),
        observed_at=EARLIER,
    )
    assert dup.ok and dup.degraded and DUPLICATE_CONTENT in dup.warnings
    assert dup.data is not None
    assert dup.data.evidence_id == ev.data.evidence_id

    assess = evidence_svc.assess_evidence(
        evidence_id=ev.data.evidence_id,
        case_id=case_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.SUPPORTS,
        materiality=Decimal("0.6"),
        rationale="Supports long thesis",
        assessed_by="codex",
        confirmed_by="user",
    )
    assert assess.ok

    report = archive_svc.archive_report(
        case_id=case_id,
        report_type=ResearchReportType.A_SHARE_MARKET_REVIEW,
        title="A-share review",
        summary="茅台仍是核心",
        content_markdown="## body\npassword=not_in_audit",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(ev.data.evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert report.ok and report.data is not None

    event = archive_svc.record_event(
        case_id=case_id,
        event_type=ResearchEventType.COMPANY,
        title="Company event",
        summary="announcement day",
        occurred_at=EARLIER,
        published_at=EARLIER,
        instrument_ids=(A_SHARE,),
        evidence_ids=(ev.data.evidence_id,),
        report_ids=(report.data.report_id,),
        related_entity_type=None,
        related_entity_id=None,
        source_name="sse",
        recorded_by="user",
    )
    assert event.ok and event.data is not None

    with factory() as uow:
        case = uow.cases.get(case_id)
        assert ev.data.evidence_id in case.evidence_ids
        assert report.data.report_id in case.report_ids
        assert event.data.event_id in case.event_ids
        page = uow.search_index.search(ResearchSearchQuery(text="茅台", case_id=case_id))
        assert page.total >= 1
        page2 = uow.search_index.search(ResearchSearchQuery(text="review", case_id=case_id))
        assert page2.total >= 1

    with Session(eng) as session:
        event_types = {r.event_type for r in session.scalars(select(SystemAuditLogRow)).all()}
        assert "phase1c.evidence.recorded" in event_types
        assert "phase1c.evidence.linked" in event_types
        assert "phase1c.evidence.assessed" in event_types
        assert "phase1c.report.archived" in event_types
        assert "phase1c.event.recorded" in event_types
        payloads = "\n".join(
            r.payload_json for r in session.scalars(select(SystemAuditLogRow)).all()
        )
        assert "password=not_in_audit" not in payloads
        assert "Supports long thesis" not in payloads
        # Search documents present
        n = session.execute(text("SELECT COUNT(*) FROM research_search_documents")).scalar_one()
        assert n >= 3


def test_us_sec_and_a_share_capital_flow_types(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, _archive, factory, clock, ids, _eng = harness
    case_id = _seed_case(factory, ids, clock)
    a = evidence_svc.record_evidence(
        evidence_type=EvidenceType.A_SHARE_CAPITAL_FLOW,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="capital flow",
        summary="northbound",
        content_text=None,
        structured_data_json=None,
        source_name="eastmoney",
        source_vendor="eastmoney",
        source_record_id=None,
        source_url=None,
        published_at=None,
        effective_from=None,
        effective_to=None,
        instrument_ids=(A_SHARE,),
        topic_tags=(),
        quality=EvidenceQuality.SECONDARY,
        reliability=ReliabilityLevel.MEDIUM,
        confidence=None,
        supersedes_evidence_id=None,
        recorded_by="provider:eastmoney",
        case_ids=(case_id,),
    )
    u = evidence_svc.record_evidence(
        evidence_type=EvidenceType.US_INSIDER_ACTIVITY,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="form4",
        summary="insider buy",
        content_text=None,
        structured_data_json=None,
        source_name="sec",
        source_vendor="sec_edgar",
        source_record_id=None,
        source_url=None,
        published_at=None,
        effective_from=None,
        effective_to=None,
        instrument_ids=(US,),
        topic_tags=(),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=None,
        supersedes_evidence_id=None,
        recorded_by="provider:sec_edgar",
        case_ids=(case_id,),
    )
    assert a.ok and u.ok
    assert a.data is not None and u.data is not None
    with factory() as uow:
        case = uow.cases.get(case_id)
        assert a.data.evidence_id in case.evidence_ids
        assert u.data.evidence_id in case.evidence_ids
