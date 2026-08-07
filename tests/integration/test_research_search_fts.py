"""Phase 1C C3 integration: ResearchSearchIndex FTS + structured filters."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research_memory import ResearchSearchQuery
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfidenceBand,
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceStance,
    EvidenceType,
    InvestmentRating,
    JournalEntryType,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ResearchSearchEntityType,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
    ThesisStatus,
)
from domain.common.errors import (
    ResearchMemoryNotFound,
    SearchBackendUnavailable,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    DecisionRecord,
    Evidence,
    EvidenceAssessment,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    ResearchSubject,
    SubjectEvidenceLink,
    Thesis,
    ThesisRevision,
    compute_evidence_content_sha256,
    compute_report_content_sha256,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
LATER = NOW + timedelta(hours=2)
MUCH_LATER = NOW + timedelta(days=1)

A_SHARE_INSTRUMENT = "equity:A_SHARE:600519.SH"
US_INSTRUMENT = "equity:US:NVDA"


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "research-search-fts-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "research-search-fts-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine(tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "research_search.db"
    database_url = f"sqlite:///{path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")
    eng = create_engine(database_url)
    _enable_fk(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def uow_factory(engine: Engine):  # type: ignore[no-untyped-def]
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    factory.clock = clock  # type: ignore[attr-defined]
    factory.ids = ids  # type: ignore[attr-defined]
    factory.engine = engine  # type: ignore[attr-defined]
    return factory


def _make_case(ids: SequentialIdGenerator, clock: FixedClock, **overrides: Any) -> ResearchSubject:
    base: dict[str, Any] = {
        "subject_id": ids.new(EntityIdPrefix.SUBJECT),
        "subject_type": ResearchSubjectType.COMPANY,
        "title": "Research case",
        "summary": "Long horizon",
        "status": ResearchSubjectStatus.ACTIVE,
        "primary_instrument_id": US_INSTRUMENT,
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
        "source_name": "mock_a_share",
        "source_vendor": "mock_a_share",
        "source_record_id": None,
        "published_at": EARLIER,
        "effective_from": None,
        "effective_to": None,
        "instrument_ids": (A_SHARE_INSTRUMENT,),
    }
    base.update(overrides)
    return compute_evidence_content_sha256(**base)


def _make_evidence(ids: SequentialIdGenerator, **overrides: Any) -> Evidence:
    base: dict[str, Any] = {
        "evidence_id": ids.new(EntityIdPrefix.EVIDENCE),
        "evidence_type": EvidenceType.MARKET_SNAPSHOT,
        "origin": EvidenceOrigin.EXTERNAL_FACT,
        "title": "Moutai quote",
        "summary": "Close snapshot",
        "content_text": None,
        "structured_data_json": None,
        "source_name": "mock_a_share",
        "source_vendor": "mock_a_share",
        "source_record_id": None,
        "source_url": None,
        "published_at": EARLIER,
        "observed_at": NOW,
        "effective_from": None,
        "effective_to": None,
        "instrument_ids": (A_SHARE_INSTRUMENT,),
        "topic_tags": ("a-share", "liquor"),
        "quality": EvidenceQuality.PRIMARY,
        "reliability": ReliabilityLevel.HIGH,
        "confidence": Decimal("0.9"),
        "content_sha256": "",
        "supersedes_evidence_id": None,
        "recorded_by": "provider:mock_a_share",
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


def _make_link(
    ids: SequentialIdGenerator,
    *,
    subject_id: str,
    evidence_id: str,
    linked_at: datetime = NOW,
) -> SubjectEvidenceLink:
    return SubjectEvidenceLink(
        link_id=ids.new(EntityIdPrefix.REV),
        subject_id=subject_id,
        evidence_id=evidence_id,
        linked_at=linked_at,
        linked_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )


def _bootstrap_case(
    uow_factory,  # type: ignore[no-untyped-def]
    *,
    primary_instrument_id: str = US_INSTRUMENT,
) -> ResearchSubject:
    # 0003 seed already provides A_SHARE/US instruments after alembic upgrade.
    subject = _make_case(
        uow_factory.ids, uow_factory.clock, primary_instrument_id=primary_instrument_id
    )
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.commit()
    return subject


def test_index_five_entity_types_and_search_filters(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject = _bootstrap_case(uow_factory, primary_instrument_id=A_SHARE_INSTRUMENT)
    ids = uow_factory.ids

    evidence = _make_evidence(
        ids,
        title="贵州茅台发布业绩预告",
        summary="业绩预告摘要",
        content_text="公司发布半年度业绩预告",
        instrument_ids=(A_SHARE_INSTRUMENT,),
        topic_tags=("a-share", "liquor"),
        observed_at=EARLIER,
        published_at=EARLIER,
    )
    link = _make_link(
        ids,
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=EARLIER,
    )
    report_hash = compute_report_content_sha256(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Deep dive",
        summary="Structural demand intact",
        content_markdown="# Review\nDetails about NVIDIA",
        as_of=NOW,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(),
    )
    report = ResearchReport(
        report_id=ids.new(EntityIdPrefix.REPORT),
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Deep dive",
        summary="Structural demand intact",
        content_markdown="# Review\nDetails about NVIDIA",
        as_of=NOW,
        created_at=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        content_sha256=report_hash,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.COMPANY,
        title="Regulatory notice",
        summary="Regulator published guidance",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(A_SHARE_INSTRUMENT,),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="csrc",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="Watch Moutai",
        rationale="Wait for confirmation",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=A_SHARE_INSTRUMENT,
        thesis_revision_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    journal = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="Field note",
        body_markdown="Observed channel inventory",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE_INSTRUMENT,),
        topic_tags=("channel",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    with uow_factory() as uow:
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.reports.add(report)
        uow.events.add(event)
        uow.decisions.add(
            decision,
            idempotency_key="dec-1",
            idempotency_payload_sha256="a" * 64,
        )
        uow.journal.add(
            journal,
            idempotency_key="j-1",
            idempotency_payload_sha256="b" * 64,
        )
        for entity_type, entity_id in (
            (ResearchSearchEntityType.EVIDENCE, evidence.evidence_id),
            (ResearchSearchEntityType.REPORT, report.report_id),
            (ResearchSearchEntityType.EVENT, event.event_id),
            (ResearchSearchEntityType.DECISION, decision.decision_id),
            (ResearchSearchEntityType.JOURNAL, journal.journal_id),
        ):
            uow.search_index.index(entity_type, entity_id)
            # re-index is idempotent
            uow.search_index.index(entity_type, entity_id)
        uow.commit()

    with uow_factory() as uow:
        assert uow.search_index.probe() is True

        # CJK unigram recall
        page = uow.search_index.search(
            ResearchSearchQuery(text="茅台", subject_id=subject.subject_id)
        )
        assert page.total >= 1
        assert any(h.entity_id == evidence.evidence_id for h in page.items)
        hit = next(h for h in page.items if h.entity_id == evidence.evidence_id)
        assert hit.subject_id == subject.subject_id
        assert hit.snippet == evidence.summary
        assert hit.source_name == "mock_a_share"
        assert hit.title == evidence.title  # business source, not FTS unigram text

        page2 = uow.search_index.search(
            ResearchSearchQuery(text="业绩", subject_id=subject.subject_id)
        )
        assert any(h.entity_id == evidence.evidence_id for h in page2.items)

        # instrument structured filter (A-share vs US)
        a_page = uow.search_index.search(ResearchSearchQuery(instrument_id=A_SHARE_INSTRUMENT))
        assert a_page.total >= 1
        assert all(A_SHARE_INSTRUMENT in h.instrument_ids for h in a_page.items)

        us_page = uow.search_index.search(ResearchSearchQuery(instrument_id=US_INSTRUMENT))
        assert all(h.entity_id != evidence.evidence_id for h in us_page.items)

        # topic tags require all requested tags
        tag_page = uow.search_index.search(ResearchSearchQuery(topic_tags=("a-share", "liquor")))
        assert any(h.entity_id == evidence.evidence_id for h in tag_page.items)
        missing_tag = uow.search_index.search(
            ResearchSearchQuery(topic_tags=("a-share", "missing-tag"))
        )
        assert missing_tag.total == 0

        # entity type filter
        only_report = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                entity_types=(ResearchSearchEntityType.REPORT,),
            )
        )
        assert only_report.total == 1
        assert only_report.items[0].entity_id == report.report_id

        # Malicious MATCH operators must be quoted literals, never expand results.
        # A guaranteed-nonexistent token AND-ed with operator-looking tokens must
        # yield total=0; full-table MATCH expansion would fail this assertion.
        malicious_text = "zzz_no_such_token_c3_fts_9f3a OR NOT NEAR * ()"
        malicious = uow.search_index.search(ResearchSearchQuery(text=malicious_text))
        assert malicious.total == 0
        assert malicious.items == ()


def test_stance_thesis_and_pagination(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject = _bootstrap_case(uow_factory)
    ids = uow_factory.ids
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = Thesis(
        thesis_id=ids.new(EntityIdPrefix.THESIS),
        subject_id=subject.subject_id,
        title="Primary",
        role=ThesisRole.PRIMARY,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=rev_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=NOW,
        updated_at=NOW,
        archived_at=None,
    )
    revision = ThesisRevision(
        revision_id=rev_id,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Demand structural",
        rationale="Capex",
        confidence_band=ConfidenceBand.HIGH,
        rating=InvestmentRating.BUY,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=NOW,
        confirmed_at=NOW,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="Watch",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    e1 = _make_evidence(
        ids,
        title="Supports thesis",
        summary="bull case",
        instrument_ids=(US_INSTRUMENT,),
    )
    e2 = _make_evidence(
        ids,
        title="Contradicts thesis",
        summary="bear case",
        instrument_ids=(US_INSTRUMENT,),
        content_text="risks",
    )
    link1 = _make_link(ids, subject_id=subject.subject_id, evidence_id=e1.evidence_id)
    link2 = _make_link(ids, subject_id=subject.subject_id, evidence_id=e2.evidence_id)
    a1 = EvidenceAssessment(
        assessment_id=ids.new(EntityIdPrefix.REV),
        evidence_id=e1.evidence_id,
        subject_id=subject.subject_id,
        thesis_id=thesis.thesis_id,
        thesis_revision_id=rev_id,
        stance=EvidenceStance.SUPPORTS,
        materiality=Decimal("0.8"),
        rationale="Supports",
        assessed_at=NOW,
        assessed_by="user",
        confirmed_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    a2 = EvidenceAssessment(
        assessment_id=ids.new(EntityIdPrefix.REV),
        evidence_id=e2.evidence_id,
        subject_id=subject.subject_id,
        thesis_id=thesis.thesis_id,
        thesis_revision_id=rev_id,
        stance=EvidenceStance.CONTRADICTS,
        materiality=Decimal("0.9"),
        rationale="Contradicts",
        assessed_at=NOW,
        assessed_by="user",
        confirmed_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    with uow_factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.evidence.add(e1)
        uow.evidence.add(e2)
        uow.subject_evidence_links.add(link1)
        uow.subject_evidence_links.add(link2)
        uow.evidence_assessments.add(a1)
        uow.evidence_assessments.add(a2)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, e1.evidence_id)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, e2.evidence_id)
        uow.commit()

    with uow_factory() as uow:
        contra = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                thesis_id=thesis.thesis_id,
                stances=(EvidenceStance.CONTRADICTS,),
            )
        )
        assert contra.total == 1
        assert contra.items[0].entity_id == e2.evidence_id
        assert contra.items[0].matched_stances == (EvidenceStance.CONTRADICTS,)
        assert a2.assessment_id in contra.items[0].matched_assessment_ids

        thesis_page = uow.search_index.search(ResearchSearchQuery(thesis_id=thesis.thesis_id))
        # default entity types for thesis-only: evidence/report/decision
        assert all(
            h.entity_type
            in {
                ResearchSearchEntityType.EVIDENCE.value,
                ResearchSearchEntityType.REPORT.value,
                ResearchSearchEntityType.DECISION.value,
            }
            for h in thesis_page.items
        )
        assert thesis_page.total == 2

        page0 = uow.search_index.search(
            ResearchSearchQuery(subject_id=subject.subject_id, limit=1, offset=0)
        )
        page1 = uow.search_index.search(
            ResearchSearchQuery(subject_id=subject.subject_id, limit=1, offset=1)
        )
        assert page0.total == 2
        assert page0.has_more is True
        assert page1.has_more is False
        assert {page0.items[0].entity_id, page1.items[0].entity_id} == {
            e1.evidence_id,
            e2.evidence_id,
        }


def test_thesis_id_filter_returns_matching_evidence_report_decision(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    """thesis_id alone must return exactly linked Evidence/Report/Decision."""
    subject = _bootstrap_case(uow_factory)
    ids = uow_factory.ids

    match_rev_id = ids.new(EntityIdPrefix.REV)
    match_thesis = Thesis(
        thesis_id=ids.new(EntityIdPrefix.THESIS),
        subject_id=subject.subject_id,
        title="Match thesis",
        role=ThesisRole.PRIMARY,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=match_rev_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=NOW,
        updated_at=NOW,
        archived_at=None,
    )
    match_revision = ThesisRevision(
        revision_id=match_rev_id,
        thesis_id=match_thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Structural demand",
        rationale="Capex cycle",
        confidence_band=ConfidenceBand.HIGH,
        rating=InvestmentRating.BUY,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=EARLIER,
        confirmed_at=EARLIER,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="Watch volume",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    other_rev_id = ids.new(EntityIdPrefix.REV)
    other_thesis = Thesis(
        thesis_id=ids.new(EntityIdPrefix.THESIS),
        subject_id=subject.subject_id,
        title="Other thesis",
        role=ThesisRole.BEAR,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=other_rev_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=NOW,
        updated_at=NOW,
        archived_at=None,
    )
    other_revision = ThesisRevision(
        revision_id=other_rev_id,
        thesis_id=other_thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Bear case",
        rationale="Competition",
        confidence_band=ConfidenceBand.MEDIUM,
        rating=InvestmentRating.SELL,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=EARLIER,
        confirmed_at=EARLIER,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="Watch share",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    evidence = _make_evidence(
        ids,
        title="Supports match thesis",
        summary="bull facts",
        instrument_ids=(US_INSTRUMENT,),
        observed_at=EARLIER,
    )
    link = _make_link(
        ids,
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=EARLIER,
    )
    assessment = EvidenceAssessment(
        assessment_id=ids.new(EntityIdPrefix.REV),
        evidence_id=evidence.evidence_id,
        subject_id=subject.subject_id,
        thesis_id=match_thesis.thesis_id,
        thesis_revision_id=match_rev_id,
        stance=EvidenceStance.SUPPORTS,
        materiality=Decimal("0.7"),
        rationale="Aligned",
        assessed_at=NOW,
        assessed_by="user",
        confirmed_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    match_report_hash = compute_report_content_sha256(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Match report",
        summary="Bound to match revision",
        content_markdown="# Match report body",
        as_of=NOW,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(match_rev_id,),
    )
    match_report = ResearchReport(
        report_id=ids.new(EntityIdPrefix.REPORT),
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Match report",
        summary="Bound to match revision",
        content_markdown="# Match report body",
        as_of=NOW,
        created_at=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(match_rev_id,),
        supersedes_report_id=None,
        content_sha256=match_report_hash,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    other_report_hash = compute_report_content_sha256(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Other report",
        summary="Bound to other revision",
        content_markdown="# Other report body",
        as_of=NOW,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(other_rev_id,),
    )
    other_report = ResearchReport(
        report_id=ids.new(EntityIdPrefix.REPORT),
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Other report",
        summary="Bound to other revision",
        content_markdown="# Other report body",
        as_of=NOW,
        created_at=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(other_rev_id,),
        supersedes_report_id=None,
        content_sha256=other_report_hash,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    match_decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="Match decision",
        rationale="Act on match thesis",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=US_INSTRUMENT,
        thesis_revision_ids=(match_rev_id,),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    other_decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="Other decision",
        rationale="Act on other thesis",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=US_INSTRUMENT,
        thesis_revision_ids=(other_rev_id,),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    with uow_factory() as uow:
        uow.theses.add(match_thesis)
        uow.theses.add(other_thesis)
        uow.revisions.append(match_revision)
        uow.revisions.append(other_revision)
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.evidence_assessments.add(assessment)
        uow.reports.add(match_report)
        uow.reports.add(other_report)
        uow.decisions.add(
            match_decision,
            idempotency_key="dec-match-thesis",
            idempotency_payload_sha256="e" * 64,
        )
        uow.decisions.add(
            other_decision,
            idempotency_key="dec-other-thesis",
            idempotency_payload_sha256="f" * 64,
        )
        for entity_type, entity_id in (
            (ResearchSearchEntityType.EVIDENCE, evidence.evidence_id),
            (ResearchSearchEntityType.REPORT, match_report.report_id),
            (ResearchSearchEntityType.REPORT, other_report.report_id),
            (ResearchSearchEntityType.DECISION, match_decision.decision_id),
            (ResearchSearchEntityType.DECISION, other_decision.decision_id),
        ):
            uow.search_index.index(entity_type, entity_id)
        uow.commit()

    with uow_factory() as uow:
        page = uow.search_index.search(ResearchSearchQuery(thesis_id=match_thesis.thesis_id))
        hit_ids = {h.entity_id for h in page.items}
        assert page.total == 3
        assert hit_ids == {
            evidence.evidence_id,
            match_report.report_id,
            match_decision.decision_id,
        }
        assert other_report.report_id not in hit_ids
        assert other_decision.decision_id not in hit_ids
        assert all(
            h.entity_type
            in {
                ResearchSearchEntityType.EVIDENCE.value,
                ResearchSearchEntityType.REPORT.value,
                ResearchSearchEntityType.DECISION.value,
            }
            for h in page.items
        )


def test_legacy_decision_excludes_future_confirmed_revision_under_as_of(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    """Decision visible at as_of but revision confirmed later must not match.

    C2b rejects this row shape; insert via raw SQL only for the fixture, then
    prove thesis_id + as_of excludes it while the document remains as_of-visible
    without the thesis filter.
    """
    import json

    subject = _bootstrap_case(uow_factory)
    ids = uow_factory.ids
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = Thesis(
        thesis_id=ids.new(EntityIdPrefix.THESIS),
        subject_id=subject.subject_id,
        title="Legacy thesis",
        role=ThesisRole.PRIMARY,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=rev_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=EARLIER,
        updated_at=LATER,
        archived_at=None,
    )
    # confirmed after decision recorded_at / query as_of — illegal via C2b add
    revision = ThesisRevision(
        revision_id=rev_id,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Confirmed after decision",
        rationale="Hindsight revision",
        confidence_band=ConfidenceBand.MEDIUM,
        rating=InvestmentRating.HOLD,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=EARLIER,
        confirmed_at=LATER,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="n/a",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    decision_id = ids.new(EntityIdPrefix.DECISION)

    with uow_factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.commit()

    # Bypass C2b require_thesis_revision_ids_visible for legacy fixture only.
    with Session(uow_factory.engine) as session:
        session.execute(
            text(
                "INSERT INTO decision_records ("
                "decision_id, case_id, decision_type, title, rationale, "
                "decided_at, recorded_at, decided_by, confirmation_mode, "
                "primary_instrument_id, thesis_revision_ids_json, "
                "evidence_ids_json, report_ids_json, supersedes_decision_id, "
                "position_context_snapshot_id, idempotency_key, "
                "idempotency_payload_sha256, schema_version"
                ") VALUES ("
                ":decision_id, :subject_id, :decision_type, :title, :rationale, "
                ":decided_at, :recorded_at, :decided_by, :confirmation_mode, "
                ":primary_instrument_id, :thesis_revision_ids_json, "
                ":evidence_ids_json, :report_ids_json, NULL, NULL, "
                ":idempotency_key, :idempotency_payload_sha256, 1"
                ")"
            ),
            {
                "decision_id": decision_id,
                "subject_id": subject.subject_id,
                "decision_type": DecisionType.WATCH.value,
                "title": "Legacy pre-confirmation decision",
                "rationale": "Recorded before revision confirm",
                "decided_at": EARLIER.isoformat(),
                "recorded_at": NOW.isoformat(),
                "decided_by": "user",
                "confirmation_mode": ConfirmationMode.NORMAL.value,
                "primary_instrument_id": US_INSTRUMENT,
                "thesis_revision_ids_json": json.dumps(
                    [rev_id], ensure_ascii=False, separators=(",", ":")
                ),
                "evidence_ids_json": "[]",
                "report_ids_json": "[]",
                "idempotency_key": "dec-legacy-future-rev",
                "idempotency_payload_sha256": "a" * 64,
            },
        )
        session.commit()

    with uow_factory() as uow:
        uow.search_index.index(ResearchSearchEntityType.DECISION, decision_id)
        uow.commit()

    with uow_factory() as uow:
        # Document is visible at as_of=NOW (recorded_at=NOW).
        visible = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                as_of=NOW,
                entity_types=(ResearchSearchEntityType.DECISION,),
            )
        )
        assert any(h.entity_id == decision_id for h in visible.items)

        # thesis_id without as_of still intersects the revision id.
        without_as_of = uow.search_index.search(ResearchSearchQuery(thesis_id=thesis.thesis_id))
        assert any(h.entity_id == decision_id for h in without_as_of.items)

        # thesis_id + as_of excludes revision confirmed after as_of.
        with_as_of = uow.search_index.search(
            ResearchSearchQuery(thesis_id=thesis.thesis_id, as_of=NOW)
        )
        assert with_as_of.total == 0
        assert all(h.entity_id != decision_id for h in with_as_of.items)


def test_supersession_case_specific_and_as_of(uow_factory) -> None:  # type: ignore[no-untyped-def]
    ids = uow_factory.ids
    clock = uow_factory.clock
    case_a = _make_case(ids, clock, primary_instrument_id=A_SHARE_INSTRUMENT)
    case_b = _make_case(ids, clock, primary_instrument_id=A_SHARE_INSTRUMENT)
    old = _make_evidence(
        ids,
        title="Old fact",
        summary="outdated",
        observed_at=EARLIER,
        instrument_ids=(A_SHARE_INSTRUMENT,),
    )
    correction = _make_evidence(
        ids,
        title="Correction",
        summary="fixed",
        observed_at=LATER,
        evidence_type=EvidenceType.CORRECTION,
        supersedes_evidence_id=old.evidence_id,
        instrument_ids=(A_SHARE_INSTRUMENT,),
    )
    link_old_a = _make_link(
        ids, subject_id=case_a.subject_id, evidence_id=old.evidence_id, linked_at=EARLIER
    )
    link_old_b = _make_link(
        ids, subject_id=case_b.subject_id, evidence_id=old.evidence_id, linked_at=EARLIER
    )
    # Correction only linked to case A
    link_corr_a = _make_link(
        ids, subject_id=case_a.subject_id, evidence_id=correction.evidence_id, linked_at=LATER
    )

    with uow_factory() as uow:
        uow.subjects.add(case_a)
        uow.subjects.add(case_b)
        uow.evidence.add(old)
        uow.evidence.add(correction)
        uow.subject_evidence_links.add(link_old_a)
        uow.subject_evidence_links.add(link_old_b)
        uow.subject_evidence_links.add(link_corr_a)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, old.evidence_id)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, correction.evidence_id)
        uow.commit()

    with uow_factory() as uow:
        # Current global: old superseded
        global_cur = uow.search_index.search(
            ResearchSearchQuery(
                entity_types=(ResearchSearchEntityType.EVIDENCE,),
                instrument_id=A_SHARE_INSTRUMENT,
            )
        )
        global_ids = {h.entity_id for h in global_cur.items}
        assert correction.evidence_id in global_ids
        assert old.evidence_id not in global_ids

        # Case A: old hidden (successor linked)
        case_a_page = uow.search_index.search(ResearchSearchQuery(subject_id=case_a.subject_id))
        case_a_ids = {h.entity_id for h in case_a_page.items}
        assert correction.evidence_id in case_a_ids
        assert old.evidence_id not in case_a_ids

        # Case B: old still visible (correction not linked to B)
        case_b_page = uow.search_index.search(ResearchSearchQuery(subject_id=case_b.subject_id))
        case_b_ids = {h.entity_id for h in case_b_page.items}
        assert old.evidence_id in case_b_ids
        assert correction.evidence_id not in case_b_ids

        # Historical as_of before correction: old visible globally
        hist = uow.search_index.search(
            ResearchSearchQuery(
                instrument_id=A_SHARE_INSTRUMENT,
                as_of=NOW,
                entity_types=(ResearchSearchEntityType.EVIDENCE,),
            )
        )
        hist_ids = {h.entity_id for h in hist.items}
        assert old.evidence_id in hist_ids
        assert correction.evidence_id not in hist_ids

        # include_superseded shows both
        both = uow.search_index.search(
            ResearchSearchQuery(
                instrument_id=A_SHARE_INSTRUMENT,
                include_superseded=True,
                entity_types=(ResearchSearchEntityType.EVIDENCE,),
            )
        )
        both_ids = {h.entity_id for h in both.items}
        assert old.evidence_id in both_ids
        assert correction.evidence_id in both_ids


def test_report_as_of_and_membership_refresh(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject = _bootstrap_case(uow_factory)
    ids = uow_factory.ids
    report_hash = compute_report_content_sha256(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Future report",
        summary="uses future as_of facts",
        content_markdown="body",
        as_of=LATER,
        evidence_ids=(),
        thesis_revision_ids=(),
    )
    report = ResearchReport(
        report_id=ids.new(EntityIdPrefix.REPORT),
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Future report",
        summary="uses future as_of facts",
        content_markdown="body",
        as_of=LATER,
        created_at=MUCH_LATER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        content_sha256=report_hash,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    evidence = _make_evidence(ids, instrument_ids=(US_INSTRUMENT,), title="base e")
    with uow_factory() as uow:
        uow.evidence.add(evidence)
        uow.reports.add(report)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence.evidence_id)
        uow.search_index.index(ResearchSearchEntityType.REPORT, report.report_id)
        uow.commit()

    with uow_factory() as uow:
        # Report created_at / as_of both in future relative to NOW
        page = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                as_of=NOW,
                entity_types=(ResearchSearchEntityType.REPORT,),
            )
        )
        assert page.total == 0

        # Membership refresh after linking evidence to case
        link = _make_link(
            uow_factory.ids,
            subject_id=subject.subject_id,
            evidence_id=evidence.evidence_id,
            linked_at=LATER,
        )
        uow.subject_evidence_links.add(link)
        uow.search_index.refresh_evidence_membership(evidence.evidence_id)
        uow.commit()

    with uow_factory() as uow:
        before_link_time = uow.search_index.search(
            ResearchSearchQuery(subject_id=subject.subject_id, as_of=NOW)
        )
        # membership_visible_at = LATER > NOW → evidence not visible under as_of=NOW
        assert all(h.entity_id != evidence.evidence_id for h in before_link_time.items)

        after = uow.search_index.search(
            ResearchSearchQuery(subject_id=subject.subject_id, as_of=MUCH_LATER)
        )
        assert any(h.entity_id == evidence.evidence_id for h in after.items)


def test_rebuild_repairs_fts_drift_and_probe_failure(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject = _bootstrap_case(uow_factory, primary_instrument_id=A_SHARE_INSTRUMENT)
    ids = uow_factory.ids
    evidence = _make_evidence(
        ids,
        title="贵州茅台发布业绩预告",
        summary="摘要",
        content_text="正文",
        instrument_ids=(A_SHARE_INSTRUMENT,),
    )
    link = _make_link(ids, subject_id=subject.subject_id, evidence_id=evidence.evidence_id)
    with uow_factory() as uow:
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence.evidence_id)
        uow.commit()

    # Create FTS drift: replace indexed body with unrelated tokens while
    # leaving research_search_documents content intact.
    with Session(uow_factory.engine) as session:
        row = session.execute(
            text(
                "SELECT rowid, title, body, topic_tags "
                "FROM research_search_documents WHERE entity_id = :eid"
            ),
            {"eid": evidence.evidence_id},
        ).one()
        rowid = int(row[0])
        title = str(row[1])
        body = str(row[2])
        topic_tags = str(row[3])
        session.execute(
            text(
                "INSERT INTO research_search_fts("
                "research_search_fts, rowid, title, body, topic_tags"
                ") VALUES ('delete', :rowid, :title, :body, :topic_tags)"
            ),
            {
                "rowid": rowid,
                "title": title,
                "body": body,
                "topic_tags": topic_tags,
            },
        )
        session.execute(
            text(
                "INSERT INTO research_search_fts(rowid, title, body, topic_tags) "
                "VALUES (:rowid, :title, :body, :topic_tags)"
            ),
            {
                "rowid": rowid,
                "title": "unrelated drift title",
                "body": "unrelated drift tokens only",
                "topic_tags": "drift-tag",
            },
        )
        session.commit()

    with uow_factory() as uow:
        broken = uow.search_index.search(
            ResearchSearchQuery(text="茅台", subject_id=subject.subject_id)
        )
        assert broken.total == 0
        count = uow.search_index.rebuild()
        assert count >= 1
        uow.commit()

    with uow_factory() as uow:
        fixed = uow.search_index.search(
            ResearchSearchQuery(text="茅台", subject_id=subject.subject_id)
        )
        assert fixed.total >= 1
        assert uow.search_index.probe() is True

    # probe failure path: rename FTS virtual table so MATCH fails cleanly
    with Session(uow_factory.engine) as session:
        session.execute(text("ALTER TABLE research_search_fts RENAME TO research_search_fts_gone"))
        session.commit()
    with uow_factory() as uow:
        assert uow.search_index.probe() is False
        with pytest.raises(SearchBackendUnavailable) as exc_info:
            uow.search_index.search(ResearchSearchQuery(text="茅台"))
        details = exc_info.value.details
        assert details.get("component") == "research_search"
        assert "error_type" in details
        assert "茅台" not in str(details)


def test_index_missing_entity(uow_factory) -> None:  # type: ignore[no-untyped-def]
    _bootstrap_case(uow_factory)
    with uow_factory() as uow, pytest.raises(ResearchMemoryNotFound):
        uow.search_index.index(
            ResearchSearchEntityType.EVIDENCE,
            "evidence_00000000-0000-7000-8000-000000009999",
        )


def test_uow_rollback_discards_partial_projection(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject = _bootstrap_case(uow_factory)
    ids = uow_factory.ids
    evidence = _make_evidence(ids, title="rollback me", summary="s")
    with uow_factory() as uow:
        uow.evidence.add(evidence)
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence.evidence_id)
        uow.rollback()

    with uow_factory() as uow:
        page = uow.search_index.search(
            ResearchSearchQuery(
                entity_types=(ResearchSearchEntityType.EVIDENCE,),
                text="rollback",
            )
        )
        assert page.total == 0
        # evidence itself rolled back too
        with pytest.raises(ResearchMemoryNotFound):
            uow.evidence.get(evidence.evidence_id)
    assert subject.subject_id


def test_evidence_types_restricts_to_matching_evidence_only(uow_factory) -> None:  # type: ignore[no-untyped-def]
    """Nonempty evidence_types must not let Report/Event/Decision/Journal pass through."""
    subject = _bootstrap_case(uow_factory, primary_instrument_id=A_SHARE_INSTRUMENT)
    ids = uow_factory.ids

    matching = _make_evidence(
        ids,
        evidence_type=EvidenceType.SEC_FILING,
        title="SEC 10-K",
        summary="Annual filing",
        content_text="Form 10-K",
        observed_at=EARLIER,
        published_at=EARLIER,
    )
    other_evidence = _make_evidence(
        ids,
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        title="Quote print",
        summary="Intraday quote",
        content_text="last=100",
        observed_at=EARLIER,
        published_at=EARLIER,
    )
    link_matching = _make_link(
        ids, subject_id=subject.subject_id, evidence_id=matching.evidence_id, linked_at=EARLIER
    )
    link_other = _make_link(
        ids,
        subject_id=subject.subject_id,
        evidence_id=other_evidence.evidence_id,
        linked_at=EARLIER,
    )
    report_hash = compute_report_content_sha256(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Deep dive",
        summary="Uses filing context",
        content_markdown="# Deep\nbody",
        as_of=NOW,
        evidence_ids=(matching.evidence_id,),
        thesis_revision_ids=(),
    )
    report = ResearchReport(
        report_id=ids.new(EntityIdPrefix.REPORT),
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Deep dive",
        summary="Uses filing context",
        content_markdown="# Deep\nbody",
        as_of=NOW,
        created_at=NOW,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(matching.evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        content_sha256=report_hash,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.COMPANY,
        title="Company event",
        summary="External event",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(A_SHARE_INSTRUMENT,),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="wire",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="Watch",
        rationale="Hold for filing clarity",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=A_SHARE_INSTRUMENT,
        thesis_revision_ids=(),
        evidence_ids=(matching.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    journal = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="Note",
        body_markdown="Filed under review",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE_INSTRUMENT,),
        topic_tags=("filing",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )

    with uow_factory() as uow:
        uow.evidence.add(matching)
        uow.evidence.add(other_evidence)
        uow.subject_evidence_links.add(link_matching)
        uow.subject_evidence_links.add(link_other)
        uow.reports.add(report)
        uow.events.add(event)
        uow.decisions.add(
            decision,
            idempotency_key="dec-evtype",
            idempotency_payload_sha256="c" * 64,
        )
        uow.journal.add(
            journal,
            idempotency_key="j-evtype",
            idempotency_payload_sha256="d" * 64,
        )
        for entity_type, entity_id in (
            (ResearchSearchEntityType.EVIDENCE, matching.evidence_id),
            (ResearchSearchEntityType.EVIDENCE, other_evidence.evidence_id),
            (ResearchSearchEntityType.REPORT, report.report_id),
            (ResearchSearchEntityType.EVENT, event.event_id),
            (ResearchSearchEntityType.DECISION, decision.decision_id),
            (ResearchSearchEntityType.JOURNAL, journal.journal_id),
        ):
            uow.search_index.index(entity_type, entity_id)
        uow.commit()

    with uow_factory() as uow:
        # evidence_types-only: default five entity types, but only matching Evidence.
        page = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                evidence_types=(EvidenceType.SEC_FILING,),
            )
        )
        assert page.total == 1
        assert len(page.items) == 1
        assert page.items[0].entity_type == ResearchSearchEntityType.EVIDENCE
        assert page.items[0].entity_id == matching.evidence_id
        non_evidence_ids = {
            report.report_id,
            event.event_id,
            decision.decision_id,
            journal.journal_id,
            other_evidence.evidence_id,
        }
        assert all(h.entity_id not in non_evidence_ids for h in page.items)

        # Explicit entity_types without Evidence + evidence_types => empty page.
        empty = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                entity_types=(
                    ResearchSearchEntityType.REPORT,
                    ResearchSearchEntityType.EVENT,
                    ResearchSearchEntityType.DECISION,
                    ResearchSearchEntityType.JOURNAL,
                ),
                evidence_types=(EvidenceType.SEC_FILING,),
            )
        )
        assert empty.total == 0
        assert empty.items == ()


def test_evidence_types_without_evidence_entity_returns_empty(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject = _bootstrap_case(uow_factory)
    ids = uow_factory.ids
    event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.MACRO,
        title="Macro",
        summary="CPI print",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="fred",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow:
        uow.events.add(event)
        uow.search_index.index(ResearchSearchEntityType.EVENT, event.event_id)
        uow.commit()

    with uow_factory() as uow:
        page = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                entity_types=(ResearchSearchEntityType.EVENT,),
                evidence_types=(EvidenceType.MARKET_SNAPSHOT,),
            )
        )
        assert page.total == 0
        assert page.items == ()


def test_journal_entry_types_sql_filter_pagination_and_total(uow_factory) -> None:  # type: ignore[no-untyped-def]
    """journal_entry_types must filter in SQL on count and page with accurate total."""
    subject = _bootstrap_case(uow_factory, primary_instrument_id=A_SHARE_INSTRUMENT)
    ids = uow_factory.ids

    note = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="Note A",
        body_markdown="channel note body",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE_INSTRUMENT,),
        topic_tags=("note-tag",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    postmortem = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.POSTMORTEM,
        title="Postmortem B",
        body_markdown="postmortem body",
        created_at=LATER,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE_INSTRUMENT,),
        topic_tags=("pm-tag",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    reflection = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.REFLECTION,
        title="Reflection C",
        body_markdown="reflection body",
        created_at=EARLIER,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE_INSTRUMENT,),
        topic_tags=("reflect-tag",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    # Non-journal document must not leak through journal_entry_types filter.
    evidence = _make_evidence(
        ids,
        title="unrelated evidence",
        summary="evidence summary",
        instrument_ids=(A_SHARE_INSTRUMENT,),
        observed_at=NOW,
    )
    link = _make_link(
        ids,
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=NOW,
    )

    with uow_factory() as uow:
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.journal.add(
            note,
            idempotency_key="j-note",
            idempotency_payload_sha256="c" * 64,
        )
        uow.journal.add(
            postmortem,
            idempotency_key="j-pm",
            idempotency_payload_sha256="d" * 64,
        )
        uow.journal.add(
            reflection,
            idempotency_key="j-reflect",
            idempotency_payload_sha256="e" * 64,
        )
        uow.search_index.index(ResearchSearchEntityType.EVIDENCE, evidence.evidence_id)
        for entry in (note, postmortem, reflection):
            uow.search_index.index(ResearchSearchEntityType.JOURNAL, entry.journal_id)
        uow.commit()

    with uow_factory() as uow:
        # Single type: total/page accurate.
        note_page = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                journal_entry_types=(JournalEntryType.NOTE,),
                limit=10,
                offset=0,
            )
        )
        assert note_page.total == 1
        assert note_page.has_more is False
        assert len(note_page.items) == 1
        assert note_page.items[0].entity_id == note.journal_id
        assert note_page.items[0].entity_type == ResearchSearchEntityType.JOURNAL.value

        # Multi-type OR + pagination across matching journals only.
        page0 = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                journal_entry_types=(
                    JournalEntryType.NOTE,
                    JournalEntryType.POSTMORTEM,
                ),
                limit=1,
                offset=0,
            )
        )
        page1 = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                journal_entry_types=(
                    JournalEntryType.NOTE,
                    JournalEntryType.POSTMORTEM,
                ),
                limit=1,
                offset=1,
            )
        )
        assert page0.total == 2
        assert page1.total == 2
        assert page0.has_more is True
        assert page1.has_more is False
        ids_page = {page0.items[0].entity_id, page1.items[0].entity_id}
        assert ids_page == {note.journal_id, postmortem.journal_id}
        assert reflection.journal_id not in ids_page
        assert evidence.evidence_id not in ids_page

        # Non-matching type yields empty with total=0.
        empty = uow.search_index.search(
            ResearchSearchQuery(
                subject_id=subject.subject_id,
                journal_entry_types=(JournalEntryType.QUESTION,),
            )
        )
        assert empty.total == 0
        assert empty.items == ()
        assert empty.has_more is False

        # journal_entry_types alone forces JOURNAL entity type even without subject_id.
        by_type_only = uow.search_index.search(
            ResearchSearchQuery(journal_entry_types=(JournalEntryType.REFLECTION,))
        )
        assert by_type_only.total == 1
        assert by_type_only.items[0].entity_id == reflection.journal_id
