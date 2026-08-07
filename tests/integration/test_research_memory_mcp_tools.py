"""Phase 1C C5: real stdio MCP for the six research-memory tools.

Seeds A-share and US research memory via internal services (not public MCP),
then exercises all six public tools against migrated temp SQLite.
"""

from __future__ import annotations

import json
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

from application.services.decision_record_service import DecisionRecordService
from application.services.evidence_service import EvidenceService
from application.services.journal_service import JournalService
from application.services.research_archive_service import ResearchArchiveService
from bootstrap import build_application
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AppEnvironment,
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    JournalEntryType,
    LogLevel,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ResearchSubjectStatus,
    ResearchSubjectType,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject
from infrastructure.config.settings import AppSettings
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.mcp.server import PUBLIC_TOOL_NAMES, create_mcp_server

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
A_SHARE = "equity:A_SHARE:600519.SH"
US = "equity:US:NVDA"


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_env(monkeypatch: pytest.MonkeyPatch, database_url: str, name: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", name)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", name)
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _parse_envelope(result: Any) -> dict[str, Any]:
    assert result.isError is False, getattr(result, "content", result)
    assert result.content, "tool result missing content"
    payload = json.loads(result.content[0].text)
    assert isinstance(payload, dict)
    assert "ok" in payload
    return payload


def _seed_research_memory(
    database_url: str,
) -> dict[str, str]:
    """Seed A-share + US evidence, report, event, journal, decision via internal services."""
    eng = create_engine(database_url)
    _enable_fk(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    evidence_svc = EvidenceService(factory, clock, ids, redactor)
    archive_svc = ResearchArchiveService(factory, clock, ids, redactor)
    journal_svc = JournalService(factory, clock, ids, redactor)
    decision_svc = DecisionRecordService(factory, clock, ids, redactor)

    subject = ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="C5 dual-market subject",
        summary="A-share 茅台 + US NVDA research memory seed",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id=A_SHARE,
        topic_tags=("a_share", "us", "c5"),
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
    subject_id = subject.subject_id

    a_ev = evidence_svc.record_evidence(
        evidence_type=EvidenceType.A_SHARE_ANNOUNCEMENT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="茅台业绩预告",
        summary="贵州茅台发布半年度业绩预告，归母净利润同比增长",
        content_text="公告正文：茅台半年度业绩稳健，渠道改革持续。",
        structured_data_json=None,
        source_name="eastmoney",
        source_vendor="eastmoney",
        source_record_id="a-ann-1",
        source_url=None,
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(A_SHARE,),
        topic_tags=("A股", "公告"),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.8"),
        supersedes_evidence_id=None,
        recorded_by="provider:eastmoney",
        subject_ids=(subject_id,),
        observed_at=EARLIER,
    )
    assert a_ev.ok and a_ev.data is not None

    us_ev = evidence_svc.record_evidence(
        evidence_type=EvidenceType.SEC_FILING,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="NVIDIA 10-Q filing",
        summary="NVIDIA quarterly report shows strong data center revenue",
        content_text="SEC 10-Q: data center growth and CUDA ecosystem expansion.",
        structured_data_json=None,
        source_name="sec",
        source_vendor="sec_edgar",
        source_record_id="us-10q-1",
        source_url=None,
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=(US,),
        topic_tags=("us", "sec"),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.85"),
        supersedes_evidence_id=None,
        recorded_by="provider:sec_edgar",
        subject_ids=(subject_id,),
        observed_at=EARLIER,
    )
    assert us_ev.ok and us_ev.data is not None

    # Counter-evidence stance for retrieval of opposing views.
    assess = evidence_svc.assess_evidence(
        evidence_id=us_ev.data.evidence_id,
        subject_id=subject_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.CONTRADICTS,
        materiality=Decimal("0.4"),
        rationale="Export controls may cap growth",
        assessed_by="user",
        confirmed_by="user",
    )
    assert assess.ok

    report = archive_svc.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Dual-market deep dive",
        summary="Combined A-share and US structural view",
        content_markdown="# Dual market\n茅台 channels and NVIDIA compute demand.",
        as_of=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(a_ev.data.evidence_id, us_ev.data.evidence_id),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert report.ok and report.data is not None

    event = archive_svc.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.EARNINGS,
        title="Earnings window",
        summary="Both names approach earnings season",
        occurred_at=EARLIER,
        published_at=EARLIER,
        instrument_ids=(A_SHARE, US),
        evidence_ids=(a_ev.data.evidence_id,),
        report_ids=(report.data.report_id,),
        related_entity_type=None,
        related_entity_id=None,
        source_name="calendar",
        recorded_by="user",
    )
    assert event.ok and event.data is not None

    journal = journal_svc.append(
        subject_id=subject_id,
        entry_type=JournalEntryType.NOTE,
        title="Seed journal",
        body_markdown="Pre-seeded reflection on dual market setup",
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE, US),
        topic_tags=("seed",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        idempotency_key="seed-journal-1",
    )
    assert journal.ok and journal.data is not None

    decision = decision_svc.append(
        subject_id=subject_id,
        decision_type=DecisionType.WATCH,
        title="Seed watch decision",
        rationale="Hold research stance; no trade intent yet",
        decided_at=EARLIER,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=A_SHARE,
        thesis_revision_ids=(),
        evidence_ids=(a_ev.data.evidence_id,),
        report_ids=(report.data.report_id,),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        idempotency_key="seed-decision-1",
    )
    assert decision.ok and decision.data is not None

    eng.dispose()
    return {
        "subject_id": subject_id,
        "a_evidence_id": a_ev.data.evidence_id,
        "us_evidence_id": us_ev.data.evidence_id,
        "report_id": report.data.report_id,
        "event_id": event.data.event_id,
        "journal_id": journal.data.journal_id,
        "decision_id": decision.data.decision_id,
    }


def test_c5_container_wires_six_services_and_health_component(
    tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "c5_container.db"
    database_url = f"sqlite:///{db_path}"
    _set_env(monkeypatch, database_url, "c5-container")
    command.upgrade(_alembic_config(database_url, project_root), "head")

    settings = AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="c5-container",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url=database_url,
        mcp_server_name="c5-container",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
    )
    container = build_application(settings)
    try:
        assert container.services.research_archive is not None
        assert container.services.research_search is not None
        assert container.services.research_timeline is not None
        assert container.services.journal is not None
        assert container.services.decisions is not None

        server = create_mcp_server(container)
        tools = getattr(server, "_tool_manager", None)
        assert tools is not None
        listed = tools.list_tools()
        names = {t.name for t in listed}
        assert names == set(PUBLIC_TOOL_NAMES)
        assert "evidence_create" not in names

        health = container.services.health.check()
        assert health.ok is True
        assert health.data is not None
        assert "research_search" in health.data.components
        search_comp = health.data.components["research_search"]
        wire = getattr(search_comp.state, "value", search_comp.state)
        assert wire == "ok"
        assert search_comp.check_kind == "live_probe"
    finally:
        container.close()
