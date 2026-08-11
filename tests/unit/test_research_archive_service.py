"""Phase 1C C4a unit tests for ResearchArchiveService."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research_memory import ResearchSearchQuery
from application.dto.tool_envelope import DUPLICATE_CONTENT
from application.services.evidence_service import EvidenceService
from application.services.research_archive_service import ResearchArchiveService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfidenceBand,
    ConfirmationMode,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    InvestmentRating,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
    ThesisStatus,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    ResearchSubject,
    Thesis,
    ThesisRevision,
)
from infrastructure.persistence.orm import (
    ResearchEventRow,
    ResearchReportRow,
    SystemAuditLogRow,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
FUTURE = NOW + timedelta(hours=5)
A_SHARE = "equity:A_SHARE:600519.SH"
US = "equity:US:NVDA"


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def harness(migrated_sqlite_url: str):  # type: ignore[no-untyped-def]
    eng = create_engine(migrated_sqlite_url)
    _enable_fk(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    # 0003 migration already seeds A_SHARE/US instruments.
    evidence_svc = EvidenceService(factory, clock, ids, redactor)
    archive_svc = ResearchArchiveService(factory, clock, ids, redactor)
    yield evidence_svc, archive_svc, factory, clock, ids, eng
    eng.dispose()


def _create_subject(factory, ids, clock) -> str:  # type: ignore[no-untyped-def]
    subject = ResearchSubject(
        subject_id=ids.new(EntityIdPrefix.SUBJECT),
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA case",
        summary="GPU demand",
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


def _record_linked_evidence(
    evidence_svc: EvidenceService,
    *,
    subject_id: str,
    title: str = "ev-1",
    instrument_ids: tuple[str, ...] = (US,),
) -> str:
    env = evidence_svc.record_evidence(
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title=title,
        summary=f"summary for {title}",
        content_text="body",
        structured_data_json=None,
        source_name="mock_us",
        source_vendor="mock_us",
        source_record_id=None,
        source_url=None,
        published_at=EARLIER,
        effective_from=None,
        effective_to=None,
        instrument_ids=instrument_ids,
        topic_tags=("gpu",),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("0.9"),
        supersedes_evidence_id=None,
        recorded_by="provider:mock_us",
        subject_ids=(subject_id,),
        observed_at=EARLIER,
    )
    assert env.ok and env.data is not None
    return env.data.evidence_id


def _add_thesis_revision(
    factory, ids, clock, *, subject_id: str, confirmed_at: datetime = EARLIER
) -> str:  # type: ignore[no-untyped-def]
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    thesis = Thesis(
        thesis_id=thesis_id,
        subject_id=subject_id,
        title="Primary",
        role=ThesisRole.PRIMARY,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=rev_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=confirmed_at,
        updated_at=confirmed_at,
        archived_at=None,
    )
    revision = ThesisRevision(
        revision_id=rev_id,
        thesis_id=thesis_id,
        subject_id=subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Demand structural",
        rationale="Capex",
        confidence_band=ConfidenceBand.HIGH,
        rating=InvestmentRating.BUY,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=confirmed_at,
        confirmed_at=confirmed_at,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="Watch GM",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.commit()
    return rev_id


def test_archive_report_happy_path_and_duplicate(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id)
    rev_id = _add_thesis_revision(factory, ids, clock, subject_id=subject_id)

    first = archive_svc.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Deep dive",
        summary="Structural demand intact",
        content_markdown="# Review\napi_key=should-redact-if-pattern",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(rev_id,),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert first.ok is True
    assert first.data is not None
    report_id = first.data.report_id

    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert report_id in subject.report_ids
        page = uow.search_index.search(
            ResearchSearchQuery(text="Structural", subject_id=subject_id)
        )
        assert page.total >= 1

    second = archive_svc.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Deep dive",
        summary="Structural demand intact",
        content_markdown="# Review\napi_key=should-redact-if-pattern",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(rev_id,),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert second.ok is True
    assert second.degraded is True
    assert DUPLICATE_CONTENT in second.warnings
    assert second.data is not None
    assert second.data.report_id == report_id


def test_report_as_of_future_leakage_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    # Evidence observed_at = NOW (default clock) while report as_of is EARLIER
    eid = evidence_svc.record_evidence(
        evidence_type=EvidenceType.MARKET_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title="future-ev",
        summary="observed later",
        content_text="x",
        structured_data_json=None,
        source_name="mock",
        source_vendor="mock_us",
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
        recorded_by="provider:mock_us",
        subject_ids=(subject_id,),
        observed_at=NOW,
    )
    assert eid.ok and eid.data
    env = archive_svc.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="leaky",
        summary="should fail",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid.data.evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert env.ok is False
    codes = {e.code for e in env.errors}
    assert "HISTORICAL_VISIBILITY_VIOLATION" in codes or "INVALID_RESEARCH_LINK" in codes


def test_cross_case_reference_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, _eng = harness
    case_a = _create_subject(factory, ids, clock)
    case_b = _create_subject(factory, ids, clock)
    eid_b = _record_linked_evidence(evidence_svc, subject_id=case_b, title="other-case-ev")
    env = archive_svc.archive_report(
        subject_id=case_a,
        report_type=ResearchReportType.AD_HOC,
        title="cross",
        summary="should fail",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid_b,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert env.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in env.errors)

    # Event cross-case report reference
    rep = archive_svc.archive_report(
        subject_id=case_b,
        report_type=ResearchReportType.AD_HOC,
        title="b-report",
        summary="ok",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid_b,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert rep.ok and rep.data
    bad_event = archive_svc.record_event(
        subject_id=case_a,
        event_type=ResearchEventType.COMPANY,
        title="cross event",
        summary="refs other case report",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(US,),
        evidence_ids=(),
        report_ids=(rep.data.report_id,),
        related_entity_type=None,
        related_entity_id=None,
        source_name="news",
        recorded_by="user",
    )
    assert bad_event.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in bad_event.errors)


def test_record_event_updates_case_cache_and_search(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id, title="ev-for-event")

    first = archive_svc.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.EARNINGS,
        title="Earnings beat",
        summary="Beat consensus",
        occurred_at=EARLIER,
        published_at=EARLIER,
        instrument_ids=(US,),
        evidence_ids=(eid,),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="company",
        recorded_by="user",
    )
    assert first.ok and first.data is not None
    event_id = first.data.event_id
    # recorded_by not on domain event
    assert not hasattr(first.data, "recorded_by") or not getattr(first.data, "recorded_by", None)

    # Events have no content hash — second call is a new event
    second = archive_svc.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.EARNINGS,
        title="Earnings beat",
        summary="Beat consensus",
        occurred_at=EARLIER,
        published_at=EARLIER,
        instrument_ids=(US,),
        evidence_ids=(eid,),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="company",
        recorded_by="user",
    )
    assert second.ok and second.data is not None
    assert second.data.event_id != event_id

    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert event_id in subject.event_ids
        assert second.data.event_id in subject.event_ids
        page = uow.search_index.search(ResearchSearchQuery(text="Earnings", subject_id=subject_id))
        assert page.total >= 2

    with Session(eng) as session:
        rows = session.scalars(select(ResearchEventRow)).all()
        assert len(rows) == 2


def test_audit_excludes_report_markdown_and_event_summary(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id, title="audit-ev")
    rep = archive_svc.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.US_MARKET_REVIEW,
        title="US review",
        summary="short",
        content_markdown="MARKDOWN_BODY_MUST_NOT_AUDIT",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert rep.ok
    ev = archive_svc.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.MACRO,
        title="Macro",
        summary="EVENT_SUMMARY_MUST_NOT_AUDIT",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="fed",
        recorded_by="user",
    )
    assert ev.ok

    with Session(eng) as session:
        payloads = [r.payload_json for r in session.scalars(select(SystemAuditLogRow)).all()]
        joined = "\n".join(payloads)
        assert "MARKDOWN_BODY_MUST_NOT_AUDIT" not in joined
        assert "EVENT_SUMMARY_MUST_NOT_AUDIT" not in joined
        for p in payloads:
            data = json.loads(p)
            assert "idempotency_key" in data
            assert data["idempotency_key"] is None


def test_projection_failure_rolls_back_report(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id, title="rb-ev")

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

    def boom_factory() -> BoomUow:
        return BoomUow(real_factory())

    boom = ResearchArchiveService(
        boom_factory, clock, SequentialIdGenerator(start=8000), DefaultSecretRedactor()
    )
    env = boom.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="rollback-report",
        summary="should vanish",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert env.ok is False

    with Session(eng) as session:
        titles = session.scalars(select(ResearchReportRow.title)).all()
        assert "rollback-report" not in titles
        audits = session.scalars(select(SystemAuditLogRow)).all()
        # Pre-existing evidence audits may exist; no report audit title payload.
        for row in audits:
            assert "rollback-report" not in row.payload_json

    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert subject.report_ids == ()


def test_report_case_cache_updated_at_matches_created_at(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, _eng = harness
    old = NOW - timedelta(days=5)
    subject_id = ids.new(EntityIdPrefix.SUBJECT)
    with factory() as uow:
        uow.subjects.add(
            ResearchSubject(
                subject_id=subject_id,
                subject_type=ResearchSubjectType.COMPANY,
                title="old-case",
                summary="s",
                status=ResearchSubjectStatus.ACTIVE,
                primary_instrument_id=US,
                topic_tags=("ai",),
                created_at=old,
                updated_at=old,
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
        )
        uow.commit()

    clock.set(NOW)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id, title="cache-rep-ev")
    env = archive_svc.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="cache-rep",
        summary="s",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert env.ok and env.data is not None
    assert env.data.created_at == NOW
    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert subject.updated_at == NOW
        assert env.data.report_id in subject.report_ids
        assert subject.updated_at == env.data.created_at


def test_event_related_entity_valid_case_and_evidence(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id, title="rel-ev")

    ok_case = archive_svc.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.COMPANY,
        title="rel-case",
        summary="ok",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(US,),
        evidence_ids=(),
        report_ids=(),
        related_entity_type="case",
        related_entity_id=subject_id,
        source_name="news",
        recorded_by="user",
    )
    assert ok_case.ok is True
    assert ok_case.data is not None
    assert ok_case.data.related_entity_type == "case"
    assert ok_case.data.related_entity_id == subject_id

    ok_ev = archive_svc.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.COMPANY,
        title="rel-evidence",
        summary="ok",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(US,),
        evidence_ids=(),
        report_ids=(),
        related_entity_type="evidence",
        related_entity_id=eid,
        source_name="news",
        recorded_by="user",
    )
    assert ok_ev.ok is True
    assert ok_ev.data is not None
    assert ok_ev.data.related_entity_id == eid


def test_event_related_entity_rejects_unknown_event_cross_case_future(
    harness,
) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, _eng = harness
    case_a = _create_subject(factory, ids, clock)
    case_b = _create_subject(factory, ids, clock)
    eid_b = _record_linked_evidence(evidence_svc, subject_id=case_b, title="other-case-related")
    rev_future = _add_thesis_revision(factory, ids, clock, subject_id=case_a, confirmed_at=FUTURE)

    unknown = archive_svc.record_event(
        subject_id=case_a,
        event_type=ResearchEventType.MACRO,
        title="unknown-rel",
        summary="s",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type="provider",
        related_entity_id="provider_x",
        source_name="news",
        recorded_by="user",
    )
    assert unknown.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in unknown.errors)

    event_type = archive_svc.record_event(
        subject_id=case_a,
        event_type=ResearchEventType.MACRO,
        title="event-rel",
        summary="s",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type="event",
        related_entity_id=ids.new(EntityIdPrefix.EVENT),
        source_name="news",
        recorded_by="user",
    )
    assert event_type.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in event_type.errors)

    cross = archive_svc.record_event(
        subject_id=case_a,
        event_type=ResearchEventType.MACRO,
        title="cross-rel",
        summary="s",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type="evidence",
        related_entity_id=eid_b,
        source_name="news",
        recorded_by="user",
    )
    assert cross.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in cross.errors)

    future = archive_svc.record_event(
        subject_id=case_a,
        event_type=ResearchEventType.MACRO,
        title="future-rel",
        summary="s",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type="thesis_revision",
        related_entity_id=rev_future,
        source_name="news",
        recorded_by="user",
    )
    assert future.ok is False
    codes = {e.code for e in future.errors}
    assert "HISTORICAL_VISIBILITY_VIOLATION" in codes


def test_event_related_entity_report_decision_journal(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, _eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id, title="rdj-ev")
    rep = archive_svc.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="rdj-report",
        summary="s",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert rep.ok and rep.data

    from domain.common.enums import (
        ConfirmationMode,
        DecisionType,
        JournalEntryType,
    )
    from domain.research.models import DecisionRecord, JournalEntry

    decision_id = ids.new(EntityIdPrefix.DECISION)
    journal_id = ids.new(EntityIdPrefix.JOURNAL)
    global_journal_id = ids.new(EntityIdPrefix.JOURNAL)
    with factory() as uow:
        uow.decisions.add(
            DecisionRecord(
                decision_id=decision_id,
                subject_id=subject_id,
                decision_type=DecisionType.WATCH,
                title="Watch",
                rationale="need more",
                decided_at=EARLIER,
                recorded_at=EARLIER,
                decided_by="user",
                confirmation_mode=ConfirmationMode.NORMAL,
                primary_instrument_id=US,
                thesis_revision_ids=(),
                evidence_ids=(),
                report_ids=(),
                supersedes_decision_id=None,
                position_context_snapshot_id=None,
                schema_version=RESEARCH_SCHEMA_VERSION,
            ),
            idempotency_key="dec-rel-1",
            idempotency_payload_sha256="a" * 64,
        )
        uow.journal.add(
            JournalEntry(
                journal_id=journal_id,
                subject_id=subject_id,
                entry_type=JournalEntryType.NOTE,
                title="note",
                body_markdown="body",
                created_at=EARLIER,
                authored_by="codex",
                confirmed_by="user",
                instrument_ids=(US,),
                topic_tags=("x",),
                related_entity_type=None,
                related_entity_id=None,
                supersedes_journal_id=None,
                schema_version=RESEARCH_SCHEMA_VERSION,
            ),
            idempotency_key="j-rel-1",
            idempotency_payload_sha256="b" * 64,
        )
        uow.journal.add(
            JournalEntry(
                journal_id=global_journal_id,
                subject_id=None,
                entry_type=JournalEntryType.NOTE,
                title="global",
                body_markdown="body",
                created_at=EARLIER,
                authored_by="codex",
                confirmed_by="user",
                instrument_ids=(),
                topic_tags=(),
                related_entity_type=None,
                related_entity_id=None,
                supersedes_journal_id=None,
                schema_version=RESEARCH_SCHEMA_VERSION,
            ),
            idempotency_key="j-global-1",
            idempotency_payload_sha256="c" * 64,
        )
        uow.commit()

    for rel_type, rel_id in (
        ("report", rep.data.report_id),
        ("decision", decision_id),
        ("journal", journal_id),
    ):
        env = archive_svc.record_event(
            subject_id=subject_id,
            event_type=ResearchEventType.COMPANY,
            title=f"rel-{rel_type}",
            summary="ok",
            occurred_at=EARLIER,
            published_at=None,
            instrument_ids=(),
            evidence_ids=(),
            report_ids=(),
            related_entity_type=rel_type,
            related_entity_id=rel_id,
            source_name="news",
            recorded_by="user",
        )
        assert env.ok is True, (rel_type, env.errors)

    bad_global = archive_svc.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.COMPANY,
        title="rel-global-j",
        summary="fail",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type="journal",
        related_entity_id=global_journal_id,
        source_name="news",
        recorded_by="user",
    )
    assert bad_global.ok is False
    assert any(e.code == "INVALID_RESEARCH_LINK" for e in bad_global.errors)


def test_audit_writer_failure_rolls_back_report_and_event(harness) -> None:  # type: ignore[no-untyped-def]
    evidence_svc, archive_svc, factory, clock, ids, eng = harness
    subject_id = _create_subject(factory, ids, clock)
    eid = _record_linked_evidence(evidence_svc, subject_id=subject_id, title="audit-rb-ev")

    # Snapshot residue counters before failed writes.
    with Session(eng) as session:
        report_count_before = len(session.scalars(select(ResearchReportRow)).all())
        event_count_before = len(session.scalars(select(ResearchEventRow)).all())
        audit_count_before = len(session.scalars(select(SystemAuditLogRow)).all())
        proj_before = session.execute(
            text("SELECT COUNT(*) FROM research_search_documents")
        ).scalar_one()

    real_factory = factory

    class BoomAuditUow:
        def __init__(self, inner: SqlAlchemyResearchUnitOfWork) -> None:
            self._inner = inner

        def __enter__(self) -> BoomAuditUow:
            self._inner.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            return self._inner.__exit__(*args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        @property
        def audit(self) -> Any:
            mock = MagicMock()
            mock.append.side_effect = RuntimeError("audit writer boom")
            return mock

    def boom_factory() -> BoomAuditUow:
        return BoomAuditUow(real_factory())

    boom = ResearchArchiveService(
        boom_factory, clock, SequentialIdGenerator(start=8200), DefaultSecretRedactor()
    )
    rep = boom.archive_report(
        subject_id=subject_id,
        report_type=ResearchReportType.AD_HOC,
        title="audit-fail-report",
        summary="should vanish",
        content_markdown="body",
        as_of=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(eid,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        model_name=None,
        prompt_version=None,
    )
    assert rep.ok is False

    evt = boom.record_event(
        subject_id=subject_id,
        event_type=ResearchEventType.MACRO,
        title="audit-fail-event",
        summary="should vanish",
        occurred_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="fed",
        recorded_by="user",
    )
    assert evt.ok is False

    with Session(eng) as session:
        titles = session.scalars(select(ResearchReportRow.title)).all()
        assert "audit-fail-report" not in titles
        event_titles = session.scalars(select(ResearchEventRow.title)).all()
        assert "audit-fail-event" not in event_titles
        assert len(session.scalars(select(ResearchReportRow)).all()) == report_count_before
        assert len(session.scalars(select(ResearchEventRow)).all()) == event_count_before
        assert len(session.scalars(select(SystemAuditLogRow)).all()) == audit_count_before
        proj_after = session.execute(
            text("SELECT COUNT(*) FROM research_search_documents")
        ).scalar_one()
        assert proj_after == proj_before

    with factory() as uow:
        subject = uow.subjects.get(subject_id)
        assert subject.report_ids == ()
        assert subject.event_ids == ()
