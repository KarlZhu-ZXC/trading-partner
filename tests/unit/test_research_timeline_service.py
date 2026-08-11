"""Phase 1C C4b1 unit tests for ResearchTimelineService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.services.research_timeline_service import ResearchTimelineService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    CandidateKind,
    CandidateStatus,
    ConfidenceBand,
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    InvestmentRating,
    JournalEntryType,
    ReliabilityLevel,
    ResearchEventType,
    ResearchReportType,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ResearchTimelineEntityType,
    ThesisRole,
    ThesisStatus,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import (
    RESEARCH_SCHEMA_VERSION,
    CandidateThesisRevision,
    DecisionRecord,
    Evidence,
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
A_SHARE = "equity:A_SHARE:600519.SH"
US = "equity:US:NVDA"
HASH_A = "a" * 64
HASH_B = "b" * 64


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
        "title": "Evidence title",
        "summary": "Evidence summary",
        "content_text": "body",
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
def harness(migrated_sqlite_url: str):  # type: ignore[no-untyped-def]
    eng = create_engine(migrated_sqlite_url)
    _enable_fk(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    service = ResearchTimelineService(factory, clock, ids, redactor)
    yield service, factory, clock, ids, eng
    eng.dispose()


def _seed_full_timeline(
    factory,  # type: ignore[no-untyped-def]
    ids: SequentialIdGenerator,
    clock: FixedClock,
) -> dict[str, Any]:
    subject = _make_case(ids, clock, primary_instrument_id=US)
    evidence = _make_evidence(
        ids,
        title="Ev title",
        summary="Ev summary",
        published_at=EARLIER,
        observed_at=EARLIER,
        instrument_ids=(US, A_SHARE),
    )
    link = SubjectEvidenceLink(
        link_id=ids.new(EntityIdPrefix.REV),
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=EARLIER,
        linked_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = Thesis(
        thesis_id=ids.new(EntityIdPrefix.THESIS),
        subject_id=subject.subject_id,
        title="CURRENT THESIS TITLE MUST NOT LEAK",
        role=ThesisRole.PRIMARY,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=rev_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=EARLIER,
        updated_at=NOW,
        archived_at=None,
    )
    revision = ThesisRevision(
        revision_id=rev_id,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_no=1,
        supersedes_revision_no=None,
        statement="Historical statement only",
        rationale="r",
        confidence_band=ConfidenceBand.MEDIUM,
        rating=InvestmentRating.HOLD,
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        confirmed_by="user",
        proposed_at=EARLIER,
        confirmed_at=EARLIER,
        observation_window_start=None,
        observation_window_end=None,
        invalidation_check_note="ok",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    # Evidence observed_at/link and report as_of/created_at must satisfy repository
    # visibility: observed_at <= report.as_of, linked_at <= report.created_at.
    report_hash = compute_report_content_sha256(
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Report title",
        summary="Report summary",
        content_markdown="# r",
        as_of=EARLIER,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
    )
    report = ResearchReport(
        report_id=ids.new(EntityIdPrefix.REPORT),
        subject_id=subject.subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Report title",
        summary="Report summary",
        content_markdown="# r",
        as_of=EARLIER,
        created_at=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        supersedes_report_id=None,
        content_sha256=report_hash,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.EARNINGS,
        title="Event title",
        summary="Event summary",
        occurred_at=EARLIER,
        recorded_at=EARLIER,
        published_at=None,
        instrument_ids=(US,),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(report.report_id,),
        related_entity_type=None,
        related_entity_id=None,
        source_name="sec",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="Decision title",
        rationale="Decision rationale",
        decided_at=EARLIER,
        recorded_at=EARLIER,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=US,
        thesis_revision_ids=(rev_id,),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(report.report_id,),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    journal = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="Journal title",
        body_markdown="Journal body",
        created_at=NOW,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(A_SHARE,),
        topic_tags=("j",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    confirmed = CandidateThesisRevision(
        candidate_id=ids.new(EntityIdPrefix.RUN),
        subject_id=subject.subject_id,
        thesis_id=thesis.thesis_id,
        target_revision_no=2,
        payload_json='{"statement":"secret payload"}',
        kind=CandidateKind.THESIS_REVISION,
        confirmation_mode=ConfirmationMode.NORMAL,
        status=CandidateStatus.CONFIRMED,
        proposed_at=EARLIER,
        expires_at=MUCH_LATER,
        proposed_by="codex",
        proposed_by_rationale="proposed rationale",
        reviewed_at=NOW,
        reviewed_by="user",
        review_note="looks good",
        rejection_reason=None,
        idempotency_key="cand-confirmed",
    )
    proposed = CandidateThesisRevision(
        candidate_id=ids.new(EntityIdPrefix.RUN),
        subject_id=subject.subject_id,
        thesis_id=thesis.thesis_id,
        target_revision_no=3,
        payload_json='{"statement":"still open"}',
        kind=CandidateKind.THESIS_REVISION,
        confirmation_mode=ConfirmationMode.NORMAL,
        status=CandidateStatus.PROPOSED,
        proposed_at=NOW,
        expires_at=MUCH_LATER,
        proposed_by="codex",
        proposed_by_rationale="open",
        reviewed_at=None,
        reviewed_by=None,
        review_note=None,
        rejection_reason=None,
        idempotency_key="cand-proposed",
    )

    with factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.reports.add(report)
        uow.events.add(event)
        uow.decisions.add(
            decision,
            idempotency_key="dec-1",
            idempotency_payload_sha256=HASH_A,
        )
        uow.journal.add(
            journal,
            idempotency_key="j-1",
            idempotency_payload_sha256=HASH_B,
        )
        uow.candidates.add(confirmed)
        uow.candidates.add(proposed)
        uow.commit()

    return {
        "case": subject,
        "evidence": evidence,
        "report": report,
        "event": event,
        "decision": decision,
        "journal": journal,
        "revision": revision,
        "thesis": thesis,
        "confirmed": confirmed,
        "proposed": proposed,
    }


def test_timeline_projects_all_sources_with_frozen_mappings(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    data = _seed_full_timeline(factory, ids, clock)
    subject = data["case"]

    env = service.get_timeline(subject_id=subject.subject_id, as_of=NOW, limit=100)
    assert env.ok is True
    assert env.data is not None
    assert env.data.as_of == NOW
    assert env.data.total == 7  # evidence, report, event, decision, journal, revision, confirmed
    types = {item.entity_type for item in env.data.items}
    assert ResearchTimelineEntityType.EVIDENCE.value in types
    assert ResearchTimelineEntityType.REPORT.value in types
    assert ResearchTimelineEntityType.EVENT.value in types
    assert ResearchTimelineEntityType.DECISION.value in types
    assert ResearchTimelineEntityType.JOURNAL.value in types
    assert ResearchTimelineEntityType.THESIS_REVISION.value in types
    assert ResearchTimelineEntityType.CANDIDATE_RESOLUTION.value in types
    # PROPOSED candidates never enter resolution timeline.
    assert data["proposed"].candidate_id not in {i.entity_id for i in env.data.items}

    by_id = {i.entity_id: i for i in env.data.items}
    ev = by_id[data["evidence"].evidence_id]
    assert ev.occurred_at == EARLIER  # published_at
    assert ev.visible_at == EARLIER  # observed_at
    assert set(ev.instrument_ids) == {US, A_SHARE}

    report = by_id[data["report"].report_id]
    assert report.occurred_at == EARLIER  # as_of
    assert report.visible_at == EARLIER  # created_at
    assert US in report.instrument_ids
    assert A_SHARE in report.instrument_ids

    event = by_id[data["event"].event_id]
    assert event.occurred_at == EARLIER
    assert event.visible_at == EARLIER

    decision = by_id[data["decision"].decision_id]
    assert decision.occurred_at == EARLIER
    assert decision.visible_at == EARLIER
    assert decision.instrument_ids == (US,)

    journal = by_id[data["journal"].journal_id]
    assert journal.occurred_at == NOW
    assert journal.visible_at == NOW

    revision = by_id[data["revision"].revision_id]
    assert revision.title == "Thesis revision 1"
    assert revision.summary == "Historical statement only"
    assert "CURRENT THESIS TITLE" not in revision.title
    assert "CURRENT THESIS TITLE" not in revision.summary

    cand = by_id[data["confirmed"].candidate_id]
    assert cand.title == "thesis_revision confirmed"
    assert cand.summary == "looks good"
    assert "secret payload" not in cand.summary


def test_timeline_total_before_limit_and_sort_order(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    data = _seed_full_timeline(factory, ids, clock)
    env = service.get_timeline(subject_id=data["case"].subject_id, as_of=NOW, limit=2)
    assert env.ok is True
    assert env.data is not None
    assert env.data.total == 7
    assert len(env.data.items) == 2
    # occurred_at DESC, visible_at DESC, entity_id ASC
    items = env.data.items
    for i in range(len(items) - 1):
        a, b = items[i], items[i + 1]
        if a.occurred_at != b.occurred_at:
            assert a.occurred_at > b.occurred_at
        elif a.visible_at != b.visible_at:
            assert a.visible_at > b.visible_at
        else:
            assert a.entity_id <= b.entity_id


def test_timeline_as_of_none_uses_clock_and_hides_future(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    data = _seed_full_timeline(factory, ids, clock)
    # Add future journal visible after clock.now().
    future_journal = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=data["case"].subject_id,
        entry_type=JournalEntryType.NOTE,
        title="Future",
        body_markdown="future body",
        created_at=LATER,
        authored_by="user",
        confirmed_by="user",
        instrument_ids=(),
        topic_tags=(),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with factory() as uow:
        uow.journal.add(
            future_journal,
            idempotency_key="j-future",
            idempotency_payload_sha256="c" * 64,
        )
        uow.commit()

    env = service.get_timeline(subject_id=data["case"].subject_id, as_of=None)
    assert env.ok is True
    assert env.data is not None
    assert env.data.as_of == NOW
    assert future_journal.journal_id not in {i.entity_id for i in env.data.items}


def test_timeline_occurred_window_inclusive(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    data = _seed_full_timeline(factory, ids, clock)
    env = service.get_timeline(
        subject_id=data["case"].subject_id,
        occurred_from=EARLIER,
        occurred_to=EARLIER,
        as_of=NOW,
    )
    assert env.ok is True
    assert env.data is not None
    # Evidence/report/event/decision/revision occurred_at == EARLIER
    assert env.data.total >= 1
    assert all(i.occurred_at == EARLIER for i in env.data.items)
    # Journal/candidate at NOW must be excluded.
    ids_present = {i.entity_id for i in env.data.items}
    assert data["journal"].journal_id not in ids_present
    assert data["confirmed"].candidate_id not in ids_present


def test_timeline_entity_type_filter(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    data = _seed_full_timeline(factory, ids, clock)
    env = service.get_timeline(
        subject_id=data["case"].subject_id,
        entity_types=(ResearchTimelineEntityType.JOURNAL,),
        as_of=NOW,
    )
    assert env.ok is True
    assert env.data is not None
    assert env.data.total == 1
    assert env.data.items[0].entity_id == data["journal"].journal_id


def test_timeline_rejects_invalid_limit_and_window(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock)
    with factory() as uow:
        uow.subjects.add(subject)
        uow.commit()

    bad_limit = service.get_timeline(subject_id=subject.subject_id, limit=0)
    assert bad_limit.ok is False
    bad_window = service.get_timeline(
        subject_id=subject.subject_id,
        occurred_from=LATER,
        occurred_to=EARLIER,
    )
    assert bad_window.ok is False


def test_timeline_paginates_journal_to_exhaustion(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock)
    with factory() as uow:
        uow.subjects.add(subject)
        # > one page (page size 100) worth of journals.
        for i in range(105):
            entry = JournalEntry(
                journal_id=ids.new(EntityIdPrefix.JOURNAL),
                subject_id=subject.subject_id,
                entry_type=JournalEntryType.NOTE,
                title=f"J{i}",
                body_markdown=f"body {i}",
                created_at=NOW,
                authored_by="user",
                confirmed_by="user",
                instrument_ids=(),
                topic_tags=(),
                related_entity_type=None,
                related_entity_id=None,
                supersedes_journal_id=None,
                schema_version=RESEARCH_SCHEMA_VERSION,
            )
            uow.journal.add(
                entry,
                idempotency_key=f"j-{i}",
                idempotency_payload_sha256=f"{i:064x}",
            )
        uow.commit()

    env = service.get_timeline(
        subject_id=subject.subject_id,
        entity_types=(ResearchTimelineEntityType.JOURNAL,),
        as_of=NOW,
        limit=500,
    )
    assert env.ok is True
    assert env.data is not None
    assert env.data.total == 105
    assert len(env.data.items) == 105


def test_timeline_paginates_candidates_to_exhaustion_with_tied_proposed_at(
    harness,
) -> None:  # type: ignore[no-untyped-def]
    """>50 resolved candidates sharing proposed_at: exact total, unique IDs across pages."""
    service, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock)
    n = 55  # exceeds _CANDIDATE_PAGE_SIZE (50)
    shared_proposed_at = EARLIER
    candidate_ids: list[str] = []
    with factory() as uow:
        uow.subjects.add(subject)
        for i in range(n):
            cid = ids.new(EntityIdPrefix.RUN)
            candidate_ids.append(cid)
            cand = CandidateThesisRevision(
                candidate_id=cid,
                subject_id=subject.subject_id,
                thesis_id=None,
                target_revision_no=None,
                payload_json=f'{{"i":{i}}}',
                kind=CandidateKind.THESIS_REVISION,
                confirmation_mode=ConfirmationMode.NORMAL,
                status=CandidateStatus.CONFIRMED,
                proposed_at=shared_proposed_at,
                expires_at=MUCH_LATER,
                proposed_by="codex",
                proposed_by_rationale=f"r{i}",
                reviewed_at=NOW,
                reviewed_by="user",
                review_note=f"ok{i}",
                rejection_reason=None,
                idempotency_key=f"cand-exh-{i}",
            )
            uow.candidates.add(cand)
        uow.commit()

    env = service.get_timeline(
        subject_id=subject.subject_id,
        entity_types=(ResearchTimelineEntityType.CANDIDATE_RESOLUTION,),
        as_of=NOW,
        limit=500,
    )
    assert env.ok is True
    assert env.data is not None
    assert env.data.total == n
    assert len(env.data.items) == n
    returned_ids = [item.entity_id for item in env.data.items]
    assert len(returned_ids) == len(set(returned_ids))
    assert set(returned_ids) == set(candidate_ids)


def test_timeline_missing_cited_evidence_returns_failure() -> None:
    """Corrupt UoW: report cites missing Evidence → failure, not incomplete instruments."""
    from domain.common.errors import ResearchMemoryNotFound
    from domain.research.models import ResearchReport

    missing_evidence_id = "evidence_00000000-0000-7000-8000-000000009999"
    subject_id = "case_00000000-0000-7000-8000-000000000001"
    report_id = "report_00000000-0000-7000-8000-000000000001"
    report_hash = compute_report_content_sha256(
        subject_id=subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Orphan citation report",
        summary="cites missing evidence",
        content_markdown="# x",
        as_of=EARLIER,
        evidence_ids=(missing_evidence_id,),
        thesis_revision_ids=(),
    )
    report = ResearchReport(
        report_id=report_id,
        subject_id=subject_id,
        report_type=ResearchReportType.DEEP_DIVE,
        title="Orphan citation report",
        summary="cites missing evidence",
        content_markdown="# x",
        as_of=EARLIER,
        created_at=EARLIER,
        created_by="codex",
        research_run_id=None,
        evidence_ids=(missing_evidence_id,),
        thesis_revision_ids=(),
        supersedes_report_id=None,
        content_sha256=report_hash,
        model_name=None,
        prompt_version=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    subject = ResearchSubject(
        subject_id=subject_id,
        subject_type=ResearchSubjectType.COMPANY,
        title="Case",
        summary="s",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id=US,
        topic_tags=(),
        created_at=NOW,
        updated_at=NOW,
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

    class _FakeSubjects:
        def get(self, _subject_id: str) -> ResearchSubject:
            return subject

    class _FakeReports:
        def list_by_subject(
            self, _subject_id: str, *, as_of: datetime | None = None
        ) -> tuple[ResearchReport, ...]:
            return (report,)

    class _FakeEvidence:
        def get(self, evidence_id: str) -> Evidence:
            raise ResearchMemoryNotFound(
                f"Evidence not found: {evidence_id}",
                details={"entity_type": "evidence", "evidence_id": evidence_id},
            )

    class _FakeEmpty:
        def list_evidence(self, *_a: object, **_k: object) -> tuple[()]:
            return ()

        def list_timeline(self, *_a: object, **_k: object) -> tuple[()]:
            return ()

        def list_by_subject(self, *_a: object, **_k: object) -> tuple[()]:
            return ()

        def list(self, *_a: object, **_k: object) -> tuple[()]:
            return ()

        def list_by_thesis(self, *_a: object, **_k: object) -> tuple[()]:
            return ()

    class _FakeUow:
        def __enter__(self) -> _FakeUow:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        @property
        def subjects(self) -> _FakeSubjects:
            return _FakeSubjects()

        @property
        def reports(self) -> _FakeReports:
            return _FakeReports()

        @property
        def evidence(self) -> _FakeEvidence:
            return _FakeEvidence()

        @property
        def subject_evidence_links(self) -> _FakeEmpty:
            return _FakeEmpty()

        @property
        def events(self) -> _FakeEmpty:
            return _FakeEmpty()

        @property
        def decisions(self) -> _FakeEmpty:
            return _FakeEmpty()

        @property
        def journal(self) -> _FakeEmpty:
            return _FakeEmpty()

        @property
        def theses(self) -> _FakeEmpty:
            return _FakeEmpty()

        @property
        def revisions(self) -> _FakeEmpty:
            return _FakeEmpty()

        @property
        def candidates(self) -> _FakeEmpty:
            return _FakeEmpty()

    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    service = ResearchTimelineService(
        lambda: _FakeUow(),  # type: ignore[arg-type,return-value]
        clock,
        ids,
        DefaultSecretRedactor(),
    )
    env = service.get_timeline(
        subject_id=subject_id,
        entity_types=(ResearchTimelineEntityType.REPORT,),
        as_of=NOW,
    )
    assert env.ok is False
    assert env.data is None
    assert env.errors
    assert env.errors[0].code == "RESEARCH_MEMORY_NOT_FOUND"
    # Must not forge a successful report item with empty instrument_ids.
    assert not any(
        getattr(item, "entity_id", None) == report_id
        for item in (env.data.items if env.data is not None else ())
    )


def test_timeline_defensive_redaction(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    subject = _make_case(ids, clock)
    evidence = _make_evidence(
        ids,
        title="api_key=test-secret-value",
        summary="token=abc123xyz",
        source_name="Bearer abcd.efgh",
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
        uow.commit()

    env = service.get_timeline(
        subject_id=subject.subject_id,
        entity_types=(ResearchTimelineEntityType.EVIDENCE,),
        as_of=NOW,
    )
    assert env.ok is True
    assert env.data is not None
    item = env.data.items[0]
    assert "test-secret-value" not in item.title
    assert "abc123xyz" not in item.summary
    assert item.source_name is not None
    assert "abcd.efgh" not in item.source_name


def test_timeline_no_commit(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, clock, ids, _eng = harness
    data = _seed_full_timeline(factory, ids, clock)
    commits: list[str] = []

    def spying_factory() -> SqlAlchemyResearchUnitOfWork:
        uow = factory()
        original = uow.commit

        def tracked() -> None:
            commits.append("commit")
            original()

        uow.commit = tracked  # type: ignore[method-assign]
        return uow

    svc = ResearchTimelineService(spying_factory, clock, ids, DefaultSecretRedactor())
    env = svc.get_timeline(subject_id=data["case"].subject_id)
    assert env.ok is True
    assert commits == []
