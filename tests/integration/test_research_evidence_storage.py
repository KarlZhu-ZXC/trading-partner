"""Durable source and evidence-impact storage regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from domain.common.enums import OpenQuestionStatus
from domain.common.errors import DataContractError, ImmutableResearchRecord
from domain.research.evidence_impact import EvidenceImpact, ResearchSourceRef
from domain.research.subject_models import OpenQuestion
from infrastructure.persistence.orm import (
    AssumptionRow,
    CandidateThesisRevisionRow,
    EvidenceImpactRow,
    ExternalNoteIdentityRow,
    ExternalNoteRevisionRow,
    MonitorIdentityRow,
    MonitorRunObservationRow,
    MonitorRunRow,
    MonitorVersionRow,
    ResearchEvidenceRow,
    ResearchSubjectRow,
    SubjectEvidenceLinkRow,
    ThesisRevisionRow,
    ThesisRow,
)
from infrastructure.persistence.repositories.open_question import (
    SqlAlchemyOpenQuestionRepository,
)
from infrastructure.persistence.repositories.research_links import (
    SqlAlchemyEvidenceImpactRepository,
    SqlAlchemyResearchSourceRepository,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)
PAST = datetime(2026, 9, 20, 15, tzinfo=timezone(timedelta(hours=8)))
FUTURE = datetime(2026, 9, 20, 7, tzinfo=timezone(timedelta(hours=-7)))

SUBJECT = "case_00000000-0000-7000-8000-000000000001"
OTHER_SUBJECT = "case_00000000-0000-7000-8000-000000000002"
THESIS = "thesis_00000000-0000-7000-8000-000000000001"
REVISION = "rev_00000000-0000-7000-8000-000000000001"
ASSUMPTION = "rev_00000000-0000-7000-8000-000000000011"
MONITOR = "monitor_00000000-0000-7000-8000-000000000001"
NOTE = "external_note_00000000-0000-7000-8000-000000000001"
INSTRUMENT = "equity:US:NVDA"


def _subject(subject_id: str, *, primary_instrument_id: str | None = None) -> ResearchSubjectRow:
    timestamp = NOW.isoformat()
    return ResearchSubjectRow(
        subject_id=subject_id,
        subject_type="theme",
        title=subject_id,
        summary="storage fixture",
        status="active",
        primary_instrument_id=primary_instrument_id,
        topic_tags_json=(),
        created_at=timestamp,
        updated_at=timestamp,
        created_by="user",
        archived_at=None,
        archived_reason=None,
        linked_subject_ids_json=(),
        evidence_ids_json=(),
        report_ids_json=(),
        event_ids_json=(),
        decision_ids_json=(),
        schema_version=1,
    )


def _evidence_row(evidence_id: str, when: datetime, *, title: str) -> ResearchEvidenceRow:
    return ResearchEvidenceRow(
        evidence_id=evidence_id,
        evidence_type="fundamental_snapshot",
        origin="external_fact",
        title=title,
        summary="must never be returned by source snapshots",
        content_text="private body must never be returned by source snapshots",
        structured_data_json=None,
        source_name="fixture",
        source_vendor=None,
        source_record_id=evidence_id,
        source_url="https://example.test/filing",
        published_at=when.isoformat(),
        observed_at=when.isoformat(),
        effective_from=None,
        effective_to=None,
        instrument_ids_json=(),
        topic_tags_json=(),
        quality="primary",
        reliability="high",
        confidence_decimal="1",
        content_sha256=(evidence_id[-1] * 64),
        supersedes_evidence_id=None,
        recorded_by="user",
        schema_version=1,
    )


def _observation_rows(
    note_id: str,
    revision_id: str,
    when: datetime,
    *,
    coverage: str,
) -> tuple[ExternalNoteIdentityRow, ExternalNoteRevisionRow]:
    identity = ExternalNoteIdentityRow(
        note_id=note_id,
        source="FIXTURE",
        external_id=note_id,
        title="fixture note",
        primary_instrument_id=INSTRUMENT,
        created_at=when.isoformat(),
        last_seen_at=when.isoformat(),
    )
    revision = ExternalNoteRevisionRow(
        note_revision_id=revision_id,
        note_id=note_id,
        version=1,
        content_sha256=revision_id[-1] * 64,
        source_revision_key=revision_id,
        title="Historical Observation",
        summary="private summary",
        full_body="private body",
        coverage=coverage,
        source_timestamp=when.isoformat(),
        observed_at=when.isoformat(),
        visibility="owner",
        related_stock_ids=(),
        related_codes=(),
        blocks_json="[]",
    )
    return identity, revision


def _monitor_rows(
    run_id: str,
    when: datetime,
    *,
    rule_code: str,
    state: str = "TRIGGERED",
    monitor_version: int = 1,
) -> tuple[MonitorRunRow, MonitorRunObservationRow]:
    return (
        MonitorRunRow(
            run_id=run_id,
            requested_monitor_ids=(MONITOR,),
            selected_monitor_ids=(MONITOR,),
            cadence="ON_DEMAND",
            as_of=when.isoformat(),
            started_at=when.isoformat(),
            completed_at=when.isoformat(),
            status="SUCCEEDED",
            monitors_evaluated=1,
            rules_evaluated=1,
            events_created=1 if state == "TRIGGERED" else 0,
            warning_codes=(),
            error_codes=(),
            observation_history_complete=True,
        ),
        MonitorRunObservationRow(
            run_id=run_id,
            monitor_id=MONITOR,
            rule_code=rule_code,
            monitor_version=monitor_version,
            instrument_id=INSTRUMENT,
            severity="INFO",
            state=state,
            observed_value="1",
            threshold_value=None,
            distance_value=None,
            distance_percent=None,
            fact_as_of=when.isoformat(),
            fact_age_seconds=0,
            warning_codes=("FIXTURE_WARNING",),
            error_codes=(),
            message="fixture",
            diagnostics_json="[]",
        ),
    )


def test_sources_are_point_in_time_scoped_and_metadata_only(migrated_sqlite_url: str) -> None:
    engine = create_engine(migrated_sqlite_url)
    with Session(engine) as session:
        session.add_all(
            (
                _subject(SUBJECT, primary_instrument_id=INSTRUMENT),
                _subject(OTHER_SUBJECT),
                _evidence_row(
                    "evidence_00000000-0000-7000-8000-000000000001",
                    PAST,
                    title="Past evidence",
                ),
                _evidence_row(
                    "evidence_00000000-0000-7000-8000-000000000002",
                    FUTURE,
                    title="Future evidence",
                ),
                _evidence_row(
                    "evidence_00000000-0000-7000-8000-000000000003",
                    PAST,
                    title="Other subject evidence",
                ),
                SubjectEvidenceLinkRow(
                    link_id="rev_00000000-0000-7000-8000-000000000101",
                    subject_id=SUBJECT,
                    evidence_id="evidence_00000000-0000-7000-8000-000000000001",
                    linked_at=PAST.isoformat(),
                    linked_by="user",
                    schema_version=1,
                ),
                SubjectEvidenceLinkRow(
                    link_id="rev_00000000-0000-7000-8000-000000000102",
                    subject_id=SUBJECT,
                    evidence_id="evidence_00000000-0000-7000-8000-000000000002",
                    linked_at=FUTURE.isoformat(),
                    linked_by="user",
                    schema_version=1,
                ),
                SubjectEvidenceLinkRow(
                    link_id="rev_00000000-0000-7000-8000-000000000103",
                    subject_id=OTHER_SUBJECT,
                    evidence_id="evidence_00000000-0000-7000-8000-000000000003",
                    linked_at=PAST.isoformat(),
                    linked_by="user",
                    schema_version=1,
                ),
            )
        )
        full_identity, full_revision = _observation_rows(
            NOTE,
            "external_note_revision_00000000-0000-7000-8000-000000000001",
            PAST,
            coverage="FULL",
        )
        summary_identity, summary_revision = _observation_rows(
            "external_note_00000000-0000-7000-8000-000000000002",
            "external_note_revision_00000000-0000-7000-8000-000000000002",
            PAST,
            coverage="SUMMARY_ONLY",
        )
        future_identity, future_revision = _observation_rows(
            "external_note_00000000-0000-7000-8000-000000000003",
            "external_note_revision_00000000-0000-7000-8000-000000000003",
            FUTURE,
            coverage="FULL",
        )
        session.add_all(
            (
                full_identity,
                full_revision,
                summary_identity,
                summary_revision,
                future_identity,
                future_revision,
                MonitorIdentityRow(monitor_id=MONITOR, created_at=PAST.isoformat()),
                MonitorVersionRow(
                    monitor_id=MONITOR,
                    version=1,
                    name="Historical monitor definition",
                    subject_id=SUBJECT,
                    primary_instrument_id=INSTRUMENT,
                    trade_plan_id=None,
                    trade_plan_version=None,
                    cadence="ON_DEMAND",
                    interval_minutes=None,
                    status="ACTIVE",
                    rules_json="[]",
                    judgment_policy_json=None,
                    valid_until=None,
                    confirmed_by="user",
                    idempotency_key="monitor-version-1",
                    created_at=PAST.isoformat(),
                    schema_version=1,
                ),
                # This is deliberately the wrong current scope.  A source
                # read must use the observation's historical monitor_version.
                MonitorVersionRow(
                    monitor_id=MONITOR,
                    version=2,
                    name="Wrong current definition",
                    subject_id=OTHER_SUBJECT,
                    primary_instrument_id=None,
                    trade_plan_id=None,
                    trade_plan_version=None,
                    cadence="ON_DEMAND",
                    interval_minutes=None,
                    status="ACTIVE",
                    rules_json="[]",
                    judgment_policy_json=None,
                    valid_until=None,
                    confirmed_by="user",
                    idempotency_key="monitor-version-2",
                    created_at=PAST.isoformat(),
                    schema_version=1,
                ),
                *_monitor_rows(
                    "monitor_run_00000000-0000-7000-8000-000000000001",
                    PAST,
                    rule_code="margin",
                ),
                *_monitor_rows(
                    "monitor_run_00000000-0000-7000-8000-000000000002",
                    FUTURE,
                    rule_code="future",
                ),
                *_monitor_rows(
                    "monitor_run_00000000-0000-7000-8000-000000000003",
                    PAST,
                    rule_code="not-evaluated",
                    state="NOT_EVALUATED",
                ),
            )
        )
        session.commit()

        repository = SqlAlchemyResearchSourceRepository(session)
        past_evidence = ResearchSourceRef(
            "EVIDENCE", "evidence_00000000-0000-7000-8000-000000000001"
        )
        future_evidence = ResearchSourceRef(
            "EVIDENCE", "evidence_00000000-0000-7000-8000-000000000002"
        )
        full_observation = ResearchSourceRef(
            "OBSERVATION", "external_note_revision_00000000-0000-7000-8000-000000000001"
        )
        summary_observation = ResearchSourceRef(
            "OBSERVATION", "external_note_revision_00000000-0000-7000-8000-000000000002"
        )
        future_observation = ResearchSourceRef(
            "OBSERVATION", "external_note_revision_00000000-0000-7000-8000-000000000003"
        )
        past_monitor = ResearchSourceRef(
            "MONITOR",
            "monitor_run_00000000-0000-7000-8000-000000000001",
            MONITOR,
            "margin",
        )
        future_monitor = ResearchSourceRef(
            "MONITOR",
            "monitor_run_00000000-0000-7000-8000-000000000002",
            MONITOR,
            "future",
        )
        not_evaluated_monitor = ResearchSourceRef(
            "MONITOR",
            "monitor_run_00000000-0000-7000-8000-000000000003",
            MONITOR,
            "not-evaluated",
        )

        evidence = repository.get(SUBJECT, past_evidence, as_of=NOW)
        assert evidence is not None and evidence.title == "Past evidence"
        assert evidence.source_name == "fixture"
        assert evidence.source_url == "https://example.test/filing"
        assert not hasattr(evidence, "summary") and not hasattr(evidence, "content_text")
        assert repository.get(SUBJECT, future_evidence, as_of=NOW) is None
        observation = repository.get(SUBJECT, full_observation, as_of=NOW)
        assert observation is not None
        assert observation.origin == "EXTERNAL_OBSERVATION"
        assert observation.warning_codes == ("SOURCE_ATTRIBUTION_REQUIRES_REVIEW",)
        assert not hasattr(observation, "summary") and not hasattr(observation, "full_body")
        assert repository.get(SUBJECT, summary_observation, as_of=NOW) is None
        assert repository.get(SUBJECT, future_observation, as_of=NOW) is None
        monitor = repository.get(SUBJECT, past_monitor, as_of=NOW)
        assert monitor is not None and monitor.title == "Historical monitor definition · margin"
        assert monitor.origin == "SYSTEM_DERIVED"
        assert monitor.warning_codes == ("FIXTURE_WARNING",)
        assert repository.get(SUBJECT, future_monitor, as_of=NOW) is None
        assert repository.get(SUBJECT, not_evaluated_monitor, as_of=NOW) is None

        bounded = repository.list_for_subject(SUBJECT, as_of=NOW, limit=1)
        assert len(bounded) == 2
        assert {item.source.kind for item in bounded} <= {"EVIDENCE", "OBSERVATION", "MONITOR"}
        assert all(item.source.source_id != future_evidence.source_id for item in bounded)
        assert all(item.source.source_id != future_observation.source_id for item in bounded)
        assert all(item.source.source_id != future_monitor.source_id for item in bounded)
        assert all(
            item.source.source_id != "evidence_00000000-0000-7000-8000-000000000003"
            for item in bounded
        )
    engine.dispose()


def test_open_question_due_precision_and_version_cas(migrated_sqlite_url: str) -> None:
    engine = create_engine(migrated_sqlite_url)
    question_id = "rev_00000000-0000-7000-8000-000000000201"
    with Session(engine) as session:
        session.add(_subject(SUBJECT))
        repository = SqlAlchemyOpenQuestionRepository(session)
        repository.add(
            OpenQuestion(
                question_id=question_id,
                subject_id=SUBJECT,
                text="What evidence remains missing?",
                status=OpenQuestionStatus.OPEN,
                asked_at=datetime(2026, 1, 1, tzinfo=UTC),
                answered_at=None,
                answer_summary=None,
                closed_without_answer_reason=None,
                proposed_by="user",
                due_at=PAST,
            )
        )
        repository.add(
            OpenQuestion(
                question_id="rev_00000000-0000-7000-8000-000000000202",
                subject_id=SUBJECT,
                text="Future question",
                status=OpenQuestionStatus.OPEN,
                asked_at=datetime(2026, 1, 1, tzinfo=UTC),
                answered_at=None,
                answer_summary=None,
                closed_without_answer_reason=None,
                proposed_by="user",
                due_at=FUTURE,
            )
        )
        session.commit()
        due = repository.list_due(now=NOW)
        assert [item.question_id for item in due] == [question_id]
        repository.update_research(
            question_id,
            expected_version=1,
            evidence_requirements=("latest report",),
            due_at=PAST,
            progress_note="one update",
            source_refs=(),
        )
        session.commit()

    with Session(engine) as session:
        repository = SqlAlchemyOpenQuestionRepository(session)
        with pytest.raises(DataContractError, match="expected_version"):
            repository.update_research(
                question_id,
                expected_version=1,
                evidence_requirements=("stale update",),
                due_at=PAST,
                progress_note="must fail",
                source_refs=(),
            )
        current = repository.get(question_id)
        assert current.version == 2
        assert current.progress_note == "one update"
    engine.dispose()


def _seed_impact_fixture(session: Session) -> None:
    timestamp = PAST.isoformat()
    session.add_all(
        (
            _subject(SUBJECT),
            ThesisRow(
                thesis_id=THESIS,
                subject_id=SUBJECT,
                title="Thesis",
                role="primary",
                status="active",
                current_revision_no=1,
                latest_revision_id=REVISION,
                parent_thesis_id=None,
                rival_thesis_ids_json=(),
                created_at=timestamp,
                updated_at=timestamp,
                archived_at=None,
            ),
            ThesisRevisionRow(
                revision_id=REVISION,
                thesis_id=THESIS,
                subject_id=SUBJECT,
                revision_no=1,
                supersedes_revision_no=None,
                statement="statement",
                rationale="rationale",
                confidence_band="medium",
                rating="watch",
                confirmation_mode="normal",
                proposed_by="user",
                confirmed_by="user",
                proposed_at=timestamp,
                confirmed_at=timestamp,
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="note",
                schema_version=1,
            ),
            AssumptionRow(
                assumption_id=ASSUMPTION,
                thesis_id=THESIS,
                subject_id=SUBJECT,
                revision_no=1,
                statement="assumption",
                basis="basis",
                falsifiability="falsifier",
                status="accepted",
                proposed_at=timestamp,
                confirmed_at=timestamp,
                proposed_by="user",
                confirmed_by="user",
                retired_at=None,
                retired_reason=None,
            ),
            _evidence_row(
                "evidence_00000000-0000-7000-8000-000000000021",
                PAST,
                title="Impact source",
            ),
            SubjectEvidenceLinkRow(
                link_id="rev_00000000-0000-7000-8000-000000000221",
                subject_id=SUBJECT,
                evidence_id="evidence_00000000-0000-7000-8000-000000000021",
                linked_at=timestamp,
                linked_by="user",
                schema_version=1,
            ),
        )
    )


def _candidate(candidate_id: str, *, idempotency_key: str) -> CandidateThesisRevisionRow:
    return CandidateThesisRevisionRow(
        candidate_id=candidate_id,
        subject_id=SUBJECT,
        thesis_id=THESIS,
        target_revision_no=1,
        payload_json="{}",
        kind="evidence_impact",
        confirmation_mode="normal",
        status="proposed",
        proposed_at=PAST.isoformat(),
        expires_at=(NOW + timedelta(days=1)).isoformat(),
        proposed_by="codex",
        proposed_by_rationale="fixture",
        reviewed_at=None,
        reviewed_by=None,
        review_note=None,
        rejection_reason=None,
        idempotency_key=idempotency_key,
    )


def _confirm_candidate(session: Session, candidate_id: str) -> None:
    candidate = session.get(CandidateThesisRevisionRow, candidate_id)
    assert candidate is not None
    candidate.status = "confirmed"
    candidate.reviewed_at = NOW.isoformat()
    candidate.reviewed_by = "user"
    candidate.review_note = "fixture confirmed"


def test_impact_pair_uniqueness_version_cas_and_append_only_guard(
    migrated_sqlite_url: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    source = ResearchSourceRef(
        "EVIDENCE", "evidence_00000000-0000-7000-8000-000000000021"
    )
    candidate_one = "run_00000000-0000-7000-8000-000000000201"
    candidate_two = "run_00000000-0000-7000-8000-000000000202"
    candidate_three = "run_00000000-0000-7000-8000-000000000203"
    impact_one = "impact_00000000-0000-7000-8000-000000000201"
    with Session(engine) as session:
        _seed_impact_fixture(session)
        session.add_all(
            (
                _candidate(candidate_one, idempotency_key="impact-candidate-1"),
                _candidate(candidate_two, idempotency_key="impact-candidate-2"),
                _candidate(candidate_three, idempotency_key="impact-candidate-3"),
            )
        )
        session.commit()
        repository = SqlAlchemyEvidenceImpactRepository(session)
        repository.append(
            EvidenceImpact(
                impact_id=impact_one,
                version=1,
                subject_id=SUBJECT,
                thesis_id=THESIS,
                thesis_revision_id=REVISION,
                assumption_id=ASSUMPTION,
                source=source,
                stance="supports",
                rationale="first",
                state="ACTIVE",
                confirmed_at=NOW,
                confirmed_by="user",
                candidate_id=candidate_one,
            ),
            expected_version=None,
        )
        _confirm_candidate(session, candidate_one)
        session.commit()

    # A second Session observes the committed active pair and cannot create a
    # second impact identity for the same exact source and assumption.
    with Session(engine) as session:
        repository = SqlAlchemyEvidenceImpactRepository(session)
        with pytest.raises(DataContractError, match="active EvidenceImpact"):
            repository.append(
                EvidenceImpact(
                    impact_id="impact_00000000-0000-7000-8000-000000000202",
                    version=1,
                    subject_id=SUBJECT,
                    thesis_id=THESIS,
                    thesis_revision_id=REVISION,
                    assumption_id=ASSUMPTION,
                    source=source,
                    stance="supports",
                    rationale="duplicate",
                    state="ACTIVE",
                    confirmed_at=NOW,
                    confirmed_by="user",
                    candidate_id=candidate_two,
                ),
                expected_version=None,
            )

        revised = EvidenceImpact(
            impact_id=impact_one,
            version=2,
            subject_id=SUBJECT,
            thesis_id=THESIS,
            thesis_revision_id=REVISION,
            assumption_id=ASSUMPTION,
            source=source,
            stance="supports",
            rationale="valid revision",
            state="ACTIVE",
            confirmed_at=NOW,
            confirmed_by="user",
            candidate_id=candidate_two,
        )
        repository.append(revised, expected_version=1)
        _confirm_candidate(session, candidate_two)
        session.commit()
        latest = repository.get(impact_one)
        assert latest is not None and latest.version == 2

        with pytest.raises(DataContractError, match="expected_version"):
            repository.append(
                EvidenceImpact(
                    impact_id=impact_one,
                    version=3,
                    subject_id=SUBJECT,
                    thesis_id=THESIS,
                    thesis_revision_id=REVISION,
                    assumption_id=ASSUMPTION,
                    source=source,
                    stance="supports",
                    rationale="stale",
                    state="ACTIVE",
                    confirmed_at=NOW,
                    confirmed_by="user",
                    candidate_id=candidate_three,
                ),
                expected_version=1,
            )
    engine.dispose()


@pytest.mark.parametrize("method", ["update", "delete"])
def test_impact_rows_are_immutable_after_insert(
    migrated_sqlite_url: str,
    method: str,
) -> None:
    engine = create_engine(migrated_sqlite_url)
    with Session(engine) as session:
        _seed_impact_fixture(session)
        candidate_id = "run_00000000-0000-7000-8000-000000000211"
        session.add(_candidate(candidate_id, idempotency_key="impact-candidate-immutable"))
        session.commit()
        repository = SqlAlchemyEvidenceImpactRepository(session)
        repository.append(
            EvidenceImpact(
                impact_id="impact_00000000-0000-7000-8000-000000000211",
                version=1,
                subject_id=SUBJECT,
                thesis_id=THESIS,
                thesis_revision_id=REVISION,
                assumption_id=ASSUMPTION,
                source=ResearchSourceRef(
                    "EVIDENCE", "evidence_00000000-0000-7000-8000-000000000021"
                ),
                stance="supports",
                rationale="immutable",
                state="ACTIVE",
                confirmed_at=NOW,
                confirmed_by="user",
                candidate_id=candidate_id,
            ),
            expected_version=None,
        )
        _confirm_candidate(session, candidate_id)
        session.commit()
        row = session.get(
            EvidenceImpactRow,
            ("impact_00000000-0000-7000-8000-000000000211", 1),
        )
        assert row is not None
        if method == "update":
            row.rationale = "mutation"
        else:
            session.delete(row)
        with pytest.raises(ImmutableResearchRecord):
            session.commit()
        session.rollback()
    engine.dispose()
