"""Phase 1C C2b integration: research-memory repositories, validation, UoW."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

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
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
    ThesisStatus,
)
from domain.common.errors import (
    AppendOnlyViolation,
    DataContractError,
    ImmutableResearchRecord,
    InvalidResearchLink,
    PersistenceError,
    ResearchMemoryNotFound,
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
from infrastructure.persistence.orm import (
    DecisionRecordRow,
    EvidenceAssessmentRow,
    InstrumentRow,
    JournalEntryRow,
    ResearchEventRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    ResearchSubjectRow,
    SubjectEvidenceLinkRow,
    ThesisRevisionRow,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(hours=2)
LATER = NOW + timedelta(hours=1)
MUCH_LATER = NOW + timedelta(hours=5)

A_SHARE_INSTRUMENT = "equity:A_SHARE:600519.SH"
US_INSTRUMENT = "equity:US:NVDA"
MISSING_INSTRUMENT = "equity:US:MISSING"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine(orm_sqlite_url: str):  # type: ignore[no-untyped-def]
    eng = create_engine(orm_sqlite_url)
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


def _seed_instruments(session: Session, *, at: datetime = NOW) -> None:
    for instrument_id, symbol, name, market, exchange, currency, tz in (
        (
            A_SHARE_INSTRUMENT,
            "600519.SH",
            "Kweichow Moutai",
            "A_SHARE",
            "SSE",
            "CNY",
            "Asia/Shanghai",
        ),
        (US_INSTRUMENT, "NVDA", "NVIDIA", "US", "NASDAQ", "USD", "America/New_York"),
    ):
        session.add(
            InstrumentRow(
                instrument_id=instrument_id,
                symbol=symbol,
                name=name,
                market=market,
                exchange=exchange,
                currency=currency,
                timezone=tz,
                asset_type="equity",
                is_active=1,
                listing_status="listed",
                country=None,
                mic=None,
                underlying_instrument_id=None,
                multiplier=None,
                tick_size=None,
                lot_size=None,
                metadata_version=1,
                created_at=at.isoformat(),
                updated_at=at.isoformat(),
            )
        )
    session.flush()


def _make_case(ids: SequentialIdGenerator, clock: FixedClock, **overrides: Any) -> ResearchSubject:
    base: dict[str, Any] = {
        "subject_id": ids.new(EntityIdPrefix.SUBJECT),
        "subject_type": ResearchSubjectType.COMPANY,
        "title": "NVDA structural",
        "summary": "Long-horizon GPU demand",
        "status": ResearchSubjectStatus.ACTIVE,
        "primary_instrument_id": US_INSTRUMENT,
        "topic_tags": ("ai", "gpu"),
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
        "title": "Moutai quote",
        "summary": "Close snapshot",
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
    if "content_sha256" not in overrides or base["content_sha256"] == "":
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


def _make_thesis(
    ids: SequentialIdGenerator,
    *,
    subject_id: str,
    revision_id: str,
    created_at: datetime = NOW,
    role: ThesisRole = ThesisRole.PRIMARY,
) -> Thesis:
    return Thesis(
        thesis_id=ids.new(EntityIdPrefix.THESIS),
        subject_id=subject_id,
        title="Primary",
        role=role,
        status=ThesisStatus.ACTIVE,
        current_revision_no=1,
        latest_revision_id=revision_id,
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=created_at,
        updated_at=created_at,
        archived_at=None,
    )


def _make_revision(
    ids: SequentialIdGenerator,
    *,
    thesis_id: str,
    subject_id: str,
    revision_id: str | None = None,
    revision_no: int = 1,
    confirmed_at: datetime = NOW,
) -> ThesisRevision:
    return ThesisRevision(
        revision_id=revision_id or ids.new(EntityIdPrefix.REV),
        thesis_id=thesis_id,
        subject_id=subject_id,
        revision_no=revision_no,
        supersedes_revision_no=None if revision_no == 1 else revision_no - 1,
        statement="Demand is structural",
        rationale="Capex cycle",
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


def _report_hash(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "subject_id": "case_placeholder",
        "report_type": ResearchReportType.DEEP_DIVE,
        "title": "NVDA thesis review",
        "summary": "Structural demand intact",
        "content_markdown": "# Review\nDetails",
        "as_of": EARLIER,
        "evidence_ids": (),
        "thesis_revision_ids": (),
    }
    base.update(overrides)
    return compute_report_content_sha256(**base)


def _make_report(
    ids: SequentialIdGenerator,
    *,
    subject_id: str,
    evidence_ids: tuple[str, ...] = (),
    thesis_revision_ids: tuple[str, ...] = (),
    created_at: datetime = NOW,
    as_of: datetime = EARLIER,
    **overrides: Any,
) -> ResearchReport:
    base: dict[str, Any] = {
        "report_id": ids.new(EntityIdPrefix.REPORT),
        "subject_id": subject_id,
        "report_type": ResearchReportType.DEEP_DIVE,
        "title": "NVDA thesis review",
        "summary": "Structural demand intact",
        "content_markdown": "# Review\nDetails",
        "as_of": as_of,
        "created_at": created_at,
        "created_by": "codex",
        "research_run_id": None,
        "evidence_ids": evidence_ids,
        "thesis_revision_ids": thesis_revision_ids,
        "supersedes_report_id": None,
        "content_sha256": "",
        "model_name": None,
        "prompt_version": None,
        "schema_version": RESEARCH_SCHEMA_VERSION,
    }
    base.update(overrides)
    if "content_sha256" not in overrides or base["content_sha256"] == "":
        base["content_sha256"] = _report_hash(
            subject_id=base["subject_id"],
            report_type=base["report_type"],
            title=base["title"],
            summary=base["summary"],
            content_markdown=base["content_markdown"],
            as_of=base["as_of"],
            evidence_ids=base["evidence_ids"],
            thesis_revision_ids=base["thesis_revision_ids"],
        )
    return ResearchReport(**base)


def _bootstrap_case_with_evidence(
    uow_factory,  # type: ignore[no-untyped-def]
    *,
    instrument_ids: tuple[str, ...] = (A_SHARE_INSTRUMENT,),
    observed_at: datetime = NOW,
    linked_at: datetime = NOW,
) -> tuple[ResearchSubject, Evidence, SubjectEvidenceLink]:
    clock = uow_factory.clock
    ids = uow_factory.ids
    with Session(uow_factory.engine) as session:
        _seed_instruments(session)
        session.commit()

    subject = _make_case(ids, clock)
    seq = ids._n
    evidence = _make_evidence(
        ids,
        instrument_ids=instrument_ids,
        observed_at=observed_at,
        title=f"quote-{seq}",
        summary=f"summary-{seq}",
    )
    link = _make_link(
        ids, subject_id=subject.subject_id, evidence_id=evidence.evidence_id, linked_at=linked_at
    )
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(link)
        uow.commit()
    return subject, evidence, link


# --- Round-trips ---


def test_memory_round_trips_and_timeline_ordering(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, _link = _bootstrap_case_with_evidence(
        uow_factory,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=EARLIER,
        linked_at=EARLIER,
    )
    ids = uow_factory.ids
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = _make_thesis(ids, subject_id=subject.subject_id, revision_id=rev_id)
    revision = _make_revision(
        ids,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_id=rev_id,
        confirmed_at=EARLIER,
    )
    r_old = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        created_at=NOW,
        as_of=EARLIER,
        title="old-report",
        summary="old",
        content_markdown="# old",
    )
    r_new = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        created_at=LATER,
        as_of=NOW,
        title="new-report",
        summary="new",
        content_markdown="# new",
    )
    d_old = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="old",
        rationale="r",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    d_new = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="new",
        rationale="r",
        decided_at=NOW,
        recorded_at=LATER,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    j_old = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="old",
        body_markdown="b",
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
    j_new = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="new",
        body_markdown="b",
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
    e_mid = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.COMPANY,
        title="mid",
        summary="s",
        occurred_at=NOW,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    e_early = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.MACRO,
        title="early",
        summary="s",
        occurred_at=EARLIER,
        recorded_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.reports.add(r_new)
        uow.reports.add(r_old)
        uow.events.add(e_mid)
        uow.events.add(e_early)
        uow.decisions.add(d_new, idempotency_key="d-new", idempotency_payload_sha256=HASH_A)
        uow.decisions.add(d_old, idempotency_key="d-old", idempotency_payload_sha256=HASH_B)
        uow.journal.add(j_new, idempotency_key="j-new", idempotency_payload_sha256=HASH_A)
        uow.journal.add(j_old, idempotency_key="j-old", idempotency_payload_sha256=HASH_B)
        uow.commit()

    with uow_factory() as uow:
        loaded = uow.evidence.get(evidence.evidence_id)
        assert loaded == evidence
        by_hash = uow.evidence.get_by_content_sha256(evidence.content_sha256)
        assert by_hash is not None
        assert by_hash.evidence_id == evidence.evidence_id
        assert uow.evidence.get_by_content_sha256("0" * 64) is None
        assert subject.subject_id  # silence unused when only evidence path used

        ordered = uow.subject_evidence_links.list_evidence(subject.subject_id)
        assert [e.evidence_id for e in ordered] == [evidence.evidence_id]
        assert uow.subject_evidence_links.list_subjects(evidence.evidence_id) == (
            subject.subject_id,
        )
        assert uow.subject_evidence_links.exists(subject.subject_id, evidence.evidence_id) is True
        assert uow.subject_evidence_links.exists(subject.subject_id, "missing") is False

        e2 = _make_evidence(
            uow_factory.ids,
            observed_at=NOW,
            title="e2",
            summary="s2",
            instrument_ids=(A_SHARE_INSTRUMENT,),
        )
        e3 = _make_evidence(
            uow_factory.ids,
            observed_at=LATER,
            title="e3",
            summary="s3",
            instrument_ids=(A_SHARE_INSTRUMENT,),
        )
        for additional in (e2, e3):
            uow.evidence.add(additional)
        for link in (
            _make_link(
                uow_factory.ids,
                subject_id=subject.subject_id,
                evidence_id=e2.evidence_id,
                linked_at=NOW,
            ),
            _make_link(
                uow_factory.ids,
                subject_id=subject.subject_id,
                evidence_id=e3.evidence_id,
                linked_at=LATER,
            ),
        ):
            uow.subject_evidence_links.add(link)
        uow.commit()

    with uow_factory() as uow:
        ordered = uow.subject_evidence_links.list_evidence(subject.subject_id)
        assert [e.evidence_id for e in ordered] == [
            evidence.evidence_id,
            e2.evidence_id,
            e3.evidence_id,
        ]
        assert [
            e.evidence_id
            for e in uow.subject_evidence_links.list_evidence(subject.subject_id, as_of=NOW)
        ] == [evidence.evidence_id, e2.evidence_id]

        rev_id = uow_factory.ids.new(EntityIdPrefix.REV)
        thesis = _make_thesis(
            ids=uow_factory.ids,
            subject_id=subject.subject_id,
            revision_id=rev_id,
            role=ThesisRole.COMPETITOR,
        )
        revision = _make_revision(
            uow_factory.ids,
            thesis_id=thesis.thesis_id,
            subject_id=subject.subject_id,
            revision_id=rev_id,
            confirmed_at=EARLIER,
        )
        a1 = EvidenceAssessment(
            assessment_id=uow_factory.ids.new(EntityIdPrefix.REV),
            evidence_id=e2.evidence_id,
            subject_id=subject.subject_id,
            thesis_id=thesis.thesis_id,
            thesis_revision_id=rev_id,
            stance=EvidenceStance.SUPPORTS,
            materiality=Decimal("0.5"),
            rationale="early support",
            assessed_at=NOW,
            assessed_by="codex",
            confirmed_by="user",
            schema_version=RESEARCH_SCHEMA_VERSION,
        )
        a2 = EvidenceAssessment(
            assessment_id=uow_factory.ids.new(EntityIdPrefix.REV),
            evidence_id=e2.evidence_id,
            subject_id=subject.subject_id,
            thesis_id=thesis.thesis_id,
            thesis_revision_id=rev_id,
            stance=EvidenceStance.CONTRADICTS,
            materiality=Decimal("0.8"),
            rationale="later contradict",
            assessed_at=LATER,
            assessed_by="codex",
            confirmed_by="user",
            schema_version=RESEARCH_SCHEMA_VERSION,
        )
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.evidence_assessments.add(a2)
        uow.evidence_assessments.add(a1)
        uow.commit()

        listed = uow.evidence_assessments.list_for_evidence(e2.evidence_id)
        assert [a.assessment_id for a in listed] == [a1.assessment_id, a2.assessment_id]
        assert [
            a.assessment_id
            for a in uow.evidence_assessments.list_for_evidence(e2.evidence_id, as_of=NOW)
        ] == [a1.assessment_id]
        by_stance = uow.evidence_assessments.list_for_thesis(
            thesis.thesis_id, stance=EvidenceStance.CONTRADICTS
        )
        assert [a.assessment_id for a in by_stance] == [a2.assessment_id]


def test_report_event_decision_journal_round_trips_and_filters(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, _link = _bootstrap_case_with_evidence(
        uow_factory,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=EARLIER,
        linked_at=EARLIER,
    )
    ids = uow_factory.ids
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = _make_thesis(ids, subject_id=subject.subject_id, revision_id=rev_id)
    revision = _make_revision(
        ids,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_id=rev_id,
        confirmed_at=EARLIER,
    )
    r_old = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        created_at=NOW,
        as_of=EARLIER,
        title="old-report",
        summary="old",
        content_markdown="# old",
    )
    r_new = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        created_at=LATER,
        as_of=NOW,
        title="new-report",
        summary="new",
        content_markdown="# new",
    )
    d_old = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="old",
        rationale="r",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    d_new = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="new",
        rationale="r",
        decided_at=NOW,
        recorded_at=LATER,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    j_old = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="old",
        body_markdown="b",
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
    j_new = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="new",
        body_markdown="b",
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
    e_mid = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.COMPANY,
        title="mid",
        summary="s",
        occurred_at=NOW,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    e_early = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.MACRO,
        title="early",
        summary="s",
        occurred_at=EARLIER,
        recorded_at=EARLIER,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.EARNINGS,
        title="Q2",
        summary="beat",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=EARLIER,
        instrument_ids=(US_INSTRUMENT,),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(r_old.report_id,),
        related_entity_type=None,
        related_entity_id=None,
        source_name="sec_edgar",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="Watch",
        rationale="need more",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=US_INSTRUMENT,
        thesis_revision_ids=(rev_id,),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(r_old.report_id,),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    journal = JournalEntry(
        journal_id=ids.new(EntityIdPrefix.JOURNAL),
        subject_id=subject.subject_id,
        entry_type=JournalEntryType.NOTE,
        title="note",
        body_markdown="body",
        created_at=NOW,
        authored_by="codex",
        confirmed_by="user",
        instrument_ids=(US_INSTRUMENT,),
        topic_tags=("margin",),
        related_entity_type=None,
        related_entity_id=None,
        supersedes_journal_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.reports.add(r_new)
        uow.reports.add(r_old)
        uow.events.add(e_mid)
        uow.events.add(e_early)
        uow.events.add(event)
        uow.decisions.add(d_new, idempotency_key="d-new", idempotency_payload_sha256=HASH_A)
        uow.decisions.add(d_old, idempotency_key="d-old", idempotency_payload_sha256=HASH_B)
        uow.decisions.add(
            decision,
            idempotency_key="decision-key-1",
            idempotency_payload_sha256=HASH_A,
        )
        uow.journal.add(j_new, idempotency_key="j-new", idempotency_payload_sha256=HASH_A)
        uow.journal.add(j_old, idempotency_key="j-old", idempotency_payload_sha256=HASH_B)
        uow.journal.add(
            journal,
            idempotency_key="journal-1",
            idempotency_payload_sha256=HASH_B,
        )
        uow.commit()

    with uow_factory() as uow:
        assert uow.reports.get(r_new.report_id) == r_new
        assert uow.reports.get(r_old.report_id) == r_old
        assert uow.reports.get_by_content_sha256(r_old.content_sha256) is not None
        assert [r.report_id for r in uow.reports.list_by_subject(subject.subject_id)] == [
            r_new.report_id,
            r_old.report_id,
        ]
        assert [
            r.report_id for r in uow.reports.list_by_subject(subject.subject_id, as_of=NOW)
        ] == [r_old.report_id]

        assert uow.events.get(event.event_id) == event
        assert (
            len(
                uow.events.list_timeline(
                    subject.subject_id,
                    start=None,
                    end=None,
                    as_of=None,
                    event_types=(),
                )
            )
            == 3
        )
        assert {
            e.event_id
            for e in uow.events.list_timeline(
                subject.subject_id,
                start=EARLIER,
                end=NOW,
                as_of=None,
                event_types=(),
            )
        } == {e_mid.event_id, e_early.event_id, event.event_id}
        assert [
            e.event_id
            for e in uow.events.list_timeline(
                subject.subject_id,
                start=None,
                end=None,
                as_of=NOW,
                event_types=(ResearchEventType.MACRO,),
            )
        ] == [
            e_early.event_id,
        ]

        assert uow.decisions.get(decision.decision_id) == decision
        assert uow.decisions.get_by_idempotency_key("decision-key-1") is not None
        assert [d.decision_id for d in uow.decisions.list_by_subject(subject.subject_id)] == [
            d_new.decision_id,
            d_old.decision_id,
            decision.decision_id,
        ]
        assert [
            d.decision_id for d in uow.decisions.list_by_subject(subject.subject_id, as_of=NOW)
        ] == [
            d_old.decision_id,
            decision.decision_id,
        ]

        assert uow.journal.get(journal.journal_id) == journal
        assert uow.journal.get_by_idempotency_key("journal-1") is not None
        assert {
            j.journal_id
            for j in uow.journal.list(
                subject_id=subject.subject_id,
                as_of=None,
                limit=10,
                offset=0,
            )
        } == {j_new.journal_id, j_old.journal_id, journal.journal_id}
        assert {
            j.journal_id
            for j in uow.journal.list(
                subject_id=subject.subject_id,
                as_of=NOW,
                limit=10,
                offset=0,
            )
        } == {j_old.journal_id, journal.journal_id}
        assert [
            j.journal_id
            for j in uow.journal.list(subject_id=subject.subject_id, as_of=None, limit=1, offset=1)
        ] == [j_old.journal_id]


# --- Negative / validation ---


def test_get_not_found_details_are_minimal(uow_factory) -> None:  # type: ignore[no-untyped-def]
    with uow_factory() as uow, pytest.raises(ResearchMemoryNotFound) as exc:
        uow.evidence.get("evidence_00000000-0000-7000-8000-000000000099")
    assert set(exc.value.details.keys()) == {"entity_type", "evidence_id"}
    assert "body" not in str(exc.value.details).lower()


def test_event_get_not_found_details_are_minimal(uow_factory) -> None:  # type: ignore[no-untyped-def]
    missing = "event_00000000-0000-7000-8000-000000000099"
    with uow_factory() as uow, pytest.raises(ResearchMemoryNotFound) as exc:
        uow.events.get(missing)
    assert set(exc.value.details.keys()) == {"entity_type", "event_id"}
    assert exc.value.details["entity_type"] == "event"
    assert exc.value.details["event_id"] == missing


def test_reject_missing_instrument(uow_factory) -> None:  # type: ignore[no-untyped-def]
    clock = uow_factory.clock
    ids = uow_factory.ids
    with Session(uow_factory.engine) as session:
        _seed_instruments(session)
        session.commit()
    subject = _make_case(ids, clock)
    evidence = _make_evidence(
        ids,
        instrument_ids=(MISSING_INSTRUMENT,),
        title="missing-inst",
        summary="s",
    )
    with uow_factory() as uow:
        uow.subjects.add(subject)
        with pytest.raises(InvalidResearchLink, match="instrument"):
            uow.evidence.add(evidence)


def test_reject_duplicate_link_and_missing_link_for_assessment(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, link = _bootstrap_case_with_evidence(uow_factory)
    ids = uow_factory.ids
    dup = _make_link(
        ids, subject_id=subject.subject_id, evidence_id=evidence.evidence_id, linked_at=LATER
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="already exists"):
        uow.subject_evidence_links.add(dup)

    # Assessment without link (different subject)
    clock = uow_factory.clock
    other = _make_case(ids, clock, title="other case", summary="other")
    assessment = EvidenceAssessment(
        assessment_id=ids.new(EntityIdPrefix.REV),
        evidence_id=evidence.evidence_id,
        subject_id=other.subject_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.NEUTRAL,
        materiality=Decimal("0.1"),
        rationale="no link",
        assessed_at=LATER,
        assessed_by="codex",
        confirmed_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow:
        uow.subjects.add(other)
        with pytest.raises(InvalidResearchLink, match="link"):
            uow.evidence_assessments.add(assessment)
    assert link.link_id


def test_reject_cross_case_and_future_references(uow_factory) -> None:  # type: ignore[no-untyped-def]
    case_a, evidence_a, _ = _bootstrap_case_with_evidence(
        uow_factory, instrument_ids=(US_INSTRUMENT,)
    )
    clock = uow_factory.clock
    ids = uow_factory.ids
    case_b = _make_case(ids, clock, title="case b", summary="b")
    evidence_b = _make_evidence(
        ids,
        instrument_ids=(US_INSTRUMENT,),
        title="b-e",
        summary="b-s",
        observed_at=EARLIER,
    )
    link_b = _make_link(
        ids, subject_id=case_b.subject_id, evidence_id=evidence_b.evidence_id, linked_at=EARLIER
    )
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis_b = _make_thesis(ids, subject_id=case_b.subject_id, revision_id=rev_id)
    revision_b = _make_revision(
        ids,
        thesis_id=thesis_b.thesis_id,
        subject_id=case_b.subject_id,
        revision_id=rev_id,
        confirmed_at=LATER,  # not visible at NOW
    )
    with uow_factory() as uow:
        uow.subjects.add(case_b)
        uow.evidence.add(evidence_b)
        uow.subject_evidence_links.add(link_b)
        uow.theses.add(thesis_b)
        uow.revisions.append(revision_b)
        uow.commit()

    # Report on case_a referencing case_b evidence
    report = _make_report(
        ids,
        subject_id=case_a.subject_id,
        evidence_ids=(evidence_b.evidence_id,),
        thesis_revision_ids=(),
        title="cross",
        summary="cross",
        content_markdown="# cross",
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink):
        uow.reports.add(report)

    # Report referencing future thesis revision
    report2 = _make_report(
        ids,
        subject_id=case_b.subject_id,
        evidence_ids=(evidence_b.evidence_id,),
        thesis_revision_ids=(rev_id,),
        created_at=NOW,
        as_of=EARLIER,
        title="future-rev",
        summary="future-rev",
        content_markdown="# future",
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="visible"):
        uow.reports.add(report2)


def test_reject_assessment_before_observed_or_linked(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, _ = _bootstrap_case_with_evidence(
        uow_factory, observed_at=NOW, linked_at=NOW
    )
    ids = uow_factory.ids
    assessment = EvidenceAssessment(
        assessment_id=ids.new(EntityIdPrefix.REV),
        evidence_id=evidence.evidence_id,
        subject_id=subject.subject_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.SUPPORTS,
        materiality=Decimal("0.2"),
        rationale="too early",
        assessed_at=EARLIER,
        assessed_by="codex",
        confirmed_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="assessed_at"):
        uow.evidence_assessments.add(assessment)


def test_reject_link_before_evidence_observed_at(uow_factory) -> None:  # type: ignore[no-untyped-def]
    """SubjectEvidenceLink.add must reject linked_at before Evidence.observed_at."""
    clock = uow_factory.clock
    ids = uow_factory.ids
    with Session(uow_factory.engine) as session:
        _seed_instruments(session)
        session.commit()
    subject = _make_case(ids, clock)
    evidence = _make_evidence(
        ids,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=NOW,
        title="late-obs",
        summary="s",
    )
    too_early_link = _make_link(
        ids,
        subject_id=subject.subject_id,
        evidence_id=evidence.evidence_id,
        linked_at=EARLIER,
    )
    with uow_factory() as uow:
        uow.subjects.add(subject)
        uow.evidence.add(evidence)
        with pytest.raises(InvalidResearchLink, match="linked_at"):
            uow.subject_evidence_links.add(too_early_link)


def test_reject_report_citing_evidence_after_as_of(uow_factory) -> None:  # type: ignore[no-untyped-def]
    """Report created later still cannot cite evidence observed after as_of.

    Membership may already exist by created_at; observed_at must still be
    bounded by report.as_of (not merely link/created_at).
    """
    subject, evidence, _ = _bootstrap_case_with_evidence(
        uow_factory,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=NOW,
        linked_at=NOW,
    )
    ids = uow_factory.ids
    report = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(),
        created_at=LATER,
        as_of=EARLIER,
        title="as-of-leak-evidence",
        summary="hindsight",
        content_markdown="# leak",
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="observed_at"):
        uow.reports.add(report)


def test_reject_report_citing_revision_confirmed_after_as_of(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    """Report thesis revisions use confirmed_at <= as_of, not created_at."""
    subject, evidence, _ = _bootstrap_case_with_evidence(
        uow_factory,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=EARLIER,
        linked_at=EARLIER,
    )
    ids = uow_factory.ids
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = _make_thesis(ids, subject_id=subject.subject_id, revision_id=rev_id)
    # confirmed between as_of and created_at: passes created_at, fails as_of
    revision = _make_revision(
        ids,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_id=rev_id,
        confirmed_at=NOW,
    )
    with uow_factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.commit()

    report = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        created_at=LATER,
        as_of=EARLIER,
        title="as-of-leak-revision",
        summary="hindsight",
        content_markdown="# leak",
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="visible"):
        uow.reports.add(report)


def test_reject_assessment_revision_confirmed_after_assessed_at(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    """EvidenceAssessment optional revision must have confirmed_at <= assessed_at."""
    subject, evidence, _ = _bootstrap_case_with_evidence(
        uow_factory, observed_at=EARLIER, linked_at=EARLIER
    )
    ids = uow_factory.ids
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = _make_thesis(ids, subject_id=subject.subject_id, revision_id=rev_id)
    revision = _make_revision(
        ids,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_id=rev_id,
        confirmed_at=LATER,
    )
    with uow_factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.commit()

    assessment = EvidenceAssessment(
        assessment_id=ids.new(EntityIdPrefix.REV),
        evidence_id=evidence.evidence_id,
        subject_id=subject.subject_id,
        thesis_id=thesis.thesis_id,
        thesis_revision_id=rev_id,
        stance=EvidenceStance.SUPPORTS,
        materiality=Decimal("0.3"),
        rationale="revision not yet confirmed",
        assessed_at=NOW,
        assessed_by="codex",
        confirmed_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="confirmed_at"):
        uow.evidence_assessments.add(assessment)


def test_reject_event_decision_evidence_not_visible_at_recorded_at(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    """Event/Decision require Evidence.observed_at and Link.linked_at <= recorded_at."""
    subject, evidence, _ = _bootstrap_case_with_evidence(
        uow_factory,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=LATER,
        linked_at=LATER,
    )
    ids = uow_factory.ids
    event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.OTHER,
        title="future-evidence",
        summary="s",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="future-evidence",
        rationale="r",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="observed_at"):
        uow.events.add(event)
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="observed_at"):
        uow.decisions.add(
            decision,
            idempotency_key="future-ev-decision",
            idempotency_payload_sha256=HASH_A,
        )


def test_reject_evidence_supersedes_future_observed(uow_factory) -> None:  # type: ignore[no-untyped-def]
    _case, evidence, _ = _bootstrap_case_with_evidence(
        uow_factory, observed_at=NOW, instrument_ids=(US_INSTRUMENT,)
    )
    ids = uow_factory.ids
    correction = _make_evidence(
        ids,
        evidence_type=EvidenceType.CORRECTION,
        title="correction",
        summary="fix",
        observed_at=EARLIER,
        instrument_ids=(US_INSTRUMENT,),
        supersedes_evidence_id=evidence.evidence_id,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        recorded_by="provider:mock_a_share",
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="observed_at"):
        uow.evidence.add(correction)


def test_idempotency_key_storage_shape(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, _ = _bootstrap_case_with_evidence(uow_factory)
    ids = uow_factory.ids
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="w",
        rationale="r",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(evidence.evidence_id,),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow:
        with pytest.raises(DataContractError, match="strip\\+lowercase"):
            uow.decisions.add(
                decision,
                idempotency_key="Not-Lower",
                idempotency_payload_sha256=HASH_A,
            )
        with pytest.raises(DataContractError, match="64-character"):
            uow.decisions.add(
                decision,
                idempotency_key="ok-key",
                idempotency_payload_sha256="abc",
            )


def test_journal_limit_offset_bounds(uow_factory) -> None:  # type: ignore[no-untyped-def]
    with uow_factory() as uow:
        with pytest.raises(DataContractError, match="limit"):
            uow.journal.list(subject_id=None, as_of=None, limit=0, offset=0)
        with pytest.raises(DataContractError, match="limit"):
            uow.journal.list(subject_id=None, as_of=None, limit=101, offset=0)
        with pytest.raises(DataContractError, match="offset"):
            uow.journal.list(subject_id=None, as_of=None, limit=10, offset=-1)


def test_append_only_blocks_update_and_delete_for_seven_rows(
    uow_factory,
) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, link = _bootstrap_case_with_evidence(
        uow_factory,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=EARLIER,
        linked_at=EARLIER,
    )
    ids = uow_factory.ids
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = _make_thesis(ids, subject_id=subject.subject_id, revision_id=rev_id)
    revision = _make_revision(
        ids,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_id=rev_id,
        confirmed_at=EARLIER,
    )
    assessment = EvidenceAssessment(
        assessment_id=ids.new(EntityIdPrefix.REV),
        evidence_id=evidence.evidence_id,
        subject_id=subject.subject_id,
        thesis_id=None,
        thesis_revision_id=None,
        stance=EvidenceStance.SUPPORTS,
        materiality=Decimal("0.4"),
        rationale="ok",
        assessed_at=LATER,
        assessed_by="codex",
        confirmed_by="user",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    report = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        title="imm",
        summary="imm",
        content_markdown="# imm",
    )
    event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.OTHER,
        title="e",
        summary="s",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="d",
        rationale="r",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
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
        title="j",
        body_markdown="b",
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
    with uow_factory() as uow:
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.evidence_assessments.add(assessment)
        uow.reports.add(report)
        uow.events.add(event)
        uow.decisions.add(decision, idempotency_key="imm-d", idempotency_payload_sha256=HASH_A)
        uow.journal.add(journal, idempotency_key="imm-j", idempotency_payload_sha256=HASH_B)
        uow.commit()

    targets: list[tuple[type, str, str]] = [
        (ResearchEvidenceRow, evidence.evidence_id, "title"),
        (SubjectEvidenceLinkRow, link.link_id, "linked_by"),
        (EvidenceAssessmentRow, assessment.assessment_id, "rationale"),
        (ResearchReportRow, report.report_id, "title"),
        (ResearchEventRow, event.event_id, "title"),
        (DecisionRecordRow, decision.decision_id, "title"),
        (JournalEntryRow, journal.journal_id, "title"),
    ]
    engine: Engine = uow_factory.engine
    for row_cls, pk, attr in targets:
        with Session(engine) as session:
            row = session.get(row_cls, pk)
            assert row is not None
            setattr(row, attr, "mutated")
            with pytest.raises(ImmutableResearchRecord):
                session.flush()
            session.rollback()
            row = session.get(row_cls, pk)
            assert row is not None
            session.delete(row)
            with pytest.raises(ImmutableResearchRecord):
                session.flush()
            session.rollback()

    # Phase 1B wire preserved
    with Session(engine) as session:
        row = session.get(ThesisRevisionRow, rev_id)
        assert row is not None
        row.statement = "mutated"
        with pytest.raises(AppendOnlyViolation):
            session.flush()
        session.rollback()


def test_uow_atomic_commit_and_rollback(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, _ = _bootstrap_case_with_evidence(uow_factory)
    ids = uow_factory.ids
    good_event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.OTHER,
        title="good",
        summary="s",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=(),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    # second event references missing report -> fails validation after first add
    bad_event = ResearchEvent(
        event_id=ids.new(EntityIdPrefix.EVENT),
        subject_id=subject.subject_id,
        event_type=ResearchEventType.OTHER,
        title="bad",
        summary="s",
        occurred_at=EARLIER,
        recorded_at=NOW,
        published_at=None,
        instrument_ids=(),
        evidence_ids=(),
        report_ids=("report_00000000-0000-7000-8000-000000000099",),
        related_entity_type=None,
        related_entity_id=None,
        source_name="manual",
        schema_version=RESEARCH_SCHEMA_VERSION,
    )
    with uow_factory() as uow:
        uow.events.add(good_event)
        with pytest.raises(InvalidResearchLink):
            uow.events.add(bad_event)
        uow.rollback()

    with uow_factory() as uow:
        listed = uow.events.list_timeline(
            subject.subject_id,
            start=None,
            end=None,
            as_of=None,
            event_types=(),
        )
        assert listed == ()

    with uow_factory() as uow:
        uow.events.add(good_event)
        uow.commit()
    with uow_factory() as uow:
        listed = uow.events.list_timeline(
            subject.subject_id,
            start=None,
            end=None,
            as_of=None,
            event_types=(),
        )
        assert len(listed) == 1


def test_uow_not_entered_and_reentry(uow_factory) -> None:  # type: ignore[no-untyped-def]
    uow = uow_factory()
    with pytest.raises(PersistenceError, match="not active"):
        _ = uow.evidence
    with uow, pytest.raises(PersistenceError, match="already entered"):
        uow.__enter__()


def test_no_implicit_case_json_cache_mutation(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, _ = _bootstrap_case_with_evidence(uow_factory)
    with Session(uow_factory.engine) as session:
        row = session.get(ResearchSubjectRow, subject.subject_id)
        assert row is not None
        assert tuple(row.evidence_ids_json) == ()
        assert evidence.evidence_id  # evidence stored separately
        assert session.get(ResearchEvidenceRow, evidence.evidence_id) is not None


def test_supersedes_report_decision_journal_same_case(uow_factory) -> None:  # type: ignore[no-untyped-def]
    subject, evidence, _ = _bootstrap_case_with_evidence(
        uow_factory,
        instrument_ids=(US_INSTRUMENT,),
        observed_at=EARLIER,
        linked_at=EARLIER,
    )
    clock = uow_factory.clock
    ids = uow_factory.ids
    other = _make_case(ids, clock, title="other", summary="o")
    rev_id = ids.new(EntityIdPrefix.REV)
    thesis = _make_thesis(ids, subject_id=subject.subject_id, revision_id=rev_id)
    revision = _make_revision(
        ids,
        thesis_id=thesis.thesis_id,
        subject_id=subject.subject_id,
        revision_id=rev_id,
        confirmed_at=EARLIER,
    )
    report = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        title="base-r",
        summary="base-r",
        content_markdown="# base",
    )
    decision = DecisionRecord(
        decision_id=ids.new(EntityIdPrefix.DECISION),
        subject_id=subject.subject_id,
        decision_type=DecisionType.WATCH,
        title="base-d",
        rationale="r",
        decided_at=EARLIER,
        recorded_at=NOW,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
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
        title="base-j",
        body_markdown="b",
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
    with uow_factory() as uow:
        uow.subjects.add(other)
        uow.theses.add(thesis)
        uow.revisions.append(revision)
        uow.reports.add(report)
        uow.decisions.add(decision, idempotency_key="base-d", idempotency_payload_sha256=HASH_A)
        uow.journal.add(journal, idempotency_key="base-j", idempotency_payload_sha256=HASH_B)
        uow.commit()

    # Cross-case supersedes rejected
    cross_report = _make_report(
        ids,
        subject_id=other.subject_id,
        evidence_ids=(),
        thesis_revision_ids=(),
        created_at=LATER,
        as_of=NOW,
        title="cross-r",
        summary="cross-r",
        content_markdown="# cross",
        supersedes_report_id=report.report_id,
    )
    with uow_factory() as uow, pytest.raises(InvalidResearchLink, match="same research subject"):
        uow.reports.add(cross_report)

    # Valid same-case supersedes
    next_report = _make_report(
        ids,
        subject_id=subject.subject_id,
        evidence_ids=(evidence.evidence_id,),
        thesis_revision_ids=(rev_id,),
        created_at=LATER,
        as_of=NOW,
        title="next-r",
        summary="next-r",
        content_markdown="# next",
        supersedes_report_id=report.report_id,
    )
    with uow_factory() as uow:
        uow.reports.add(next_report)
        uow.commit()
    with uow_factory() as uow:
        assert uow.reports.get(next_report.report_id).supersedes_report_id == report.report_id
