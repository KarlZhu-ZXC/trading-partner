"""Integration coverage for reviewed evidence links and research questions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research import (
    AssumptionPayload,
    OpenQuestionCandidatePayload,
    SubjectUpdateCandidatePayload,
    ThesisRevisionCandidatePayload,
)
from application.dto.research_links import EvidenceImpactCandidatePayload, ResearchSourcePayload
from application.services.decision_record_service import DecisionRecordService
from application.services.decision_replay_service import DecisionReplayService
from application.services.research_links_query_service import ResearchLinksQueryService
from application.services.research_subject_service import ResearchSubjectService
from application.services.review_item_refresh_service import ReviewItemRefreshService
from application.services.review_item_service import ReviewItemService
from application.services.thesis_revision_service import ThesisRevisionService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    CandidateStatus,
    ConfidenceBand,
    ConfirmationMode,
    DecisionType,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    InvestmentRating,
    ReliabilityLevel,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
)
from domain.common.ids import EntityIdPrefix
from domain.research.memory_models import Evidence, SubjectEvidenceLink
from domain.research.validation import compute_evidence_content_sha256
from infrastructure.persistence.orm import ResearchEvidenceRow, SubjectEvidenceLinkRow
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def harness(migrated_sqlite_url: str):  # type: ignore[no-untyped-def]
    engine = create_engine(migrated_sqlite_url)
    _enable_fk(engine)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    yield {
        "engine": engine,
        "clock": clock,
        "ids": ids,
        "factory": factory,
        "subjects": ResearchSubjectService(factory, clock, ids, redactor),
        "thesis": ThesisRevisionService(factory, clock, ids, redactor),
        "decisions": DecisionRecordService(factory, clock, ids, redactor),
        "links": ResearchLinksQueryService(factory, clock),
    }
    engine.dispose()


def _revision_payload(title: str = "Demand remains durable") -> ThesisRevisionCandidatePayload:
    return ThesisRevisionCandidatePayload(
        kind="thesis_revision",
        title=title,
        statement="Demand remains durable over the reviewed horizon.",
        rationale="Capacity investment and contracted demand support the view.",
        confidence_band=ConfidenceBand.MEDIUM,
        rating=InvestmentRating.WATCH,
        invalidation_check_note="Retire the view if demand and margin both deteriorate.",
        thesis_role=ThesisRole.PRIMARY,
        assumptions=(
            AssumptionPayload(
                statement="Gross margin remains above the planning threshold.",
                basis="Current reported margin and guidance.",
                falsifiability="A reported margin below the threshold.",
            ),
        ),
    )


def _create_subject(harness: dict[str, object], key: str) -> str:
    subjects = harness["subjects"]  # type: ignore[assignment]
    thesis = harness["thesis"]  # type: ignore[assignment]
    created = subjects.create_subject(  # type: ignore[attr-defined]
        subject_type=ResearchSubjectType.THEME,
        title=f"Synthetic subject {key}",
        summary="Synthetic scope for reviewed research workflow tests.",
        primary_instrument_id=None,
        topic_tags=("synthetic",),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key=f"subject-{key}",
    )
    assert created.ok and created.data is not None
    subject_id = created.data.subject_id
    activation = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=SubjectUpdateCandidatePayload(
            action="update",
            new_status=ResearchSubjectStatus.ACTIVE,
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Activate synthetic scope for integration coverage.",
        idempotency_key=f"activate-{key}",
    )
    assert activation.ok and activation.data is not None
    assert thesis.confirm_candidate(activation.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    return subject_id


def _create_thesis(harness: dict[str, object], key: str) -> tuple[str, str, str, str]:
    subject_id = _create_subject(harness, key)
    thesis_service = harness["thesis"]  # type: ignore[assignment]
    proposed = thesis_service.propose_revision(  # type: ignore[attr-defined]
        subject_id=subject_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Create a scoped synthetic Thesis fixture.",
        idempotency_key=f"thesis-{key}",
    )
    assert proposed.ok and proposed.data is not None
    confirmed = thesis_service.confirm_candidate(  # type: ignore[attr-defined]
        proposed.data.candidate_id,
        reviewed_by="user",
    )
    assert confirmed.ok and confirmed.data is not None
    with harness["factory"]() as uow:  # type: ignore[operator]
        theses = uow.theses.list_by_subject(subject_id)
        assert len(theses) == 1
        thesis = theses[0]
        revision = uow.revisions.get(thesis.latest_revision_id)
        assumptions = uow.assumptions.list_by_revision(thesis.thesis_id, revision.revision_no)
        assert len(assumptions) == 1
    return subject_id, thesis.thesis_id, revision.revision_id, assumptions[0].assumption_id


def _add_evidence(
    harness: dict[str, object],
    subject_id: str,
    *,
    observed_at: datetime = NOW,
    title: str = "Synthetic margin report",
) -> str:
    ids = harness["ids"]  # type: ignore[assignment]
    evidence_id = ids.new(EntityIdPrefix.EVIDENCE)  # type: ignore[attr-defined]
    content_text = "Reported gross margin remains above the planning threshold."
    content_hash = compute_evidence_content_sha256(
        evidence_type=EvidenceType.FUNDAMENTAL_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title=title,
        summary="Synthetic source summary.",
        content_text=content_text,
        structured_data_json=None,
        source_name="synthetic-report",
        source_vendor=None,
        source_record_id=evidence_id,
        published_at=observed_at,
        effective_from=None,
        effective_to=None,
        instrument_ids=(),
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        evidence_type=EvidenceType.FUNDAMENTAL_SNAPSHOT,
        origin=EvidenceOrigin.EXTERNAL_FACT,
        title=title,
        summary="Synthetic source summary.",
        content_text=content_text,
        structured_data_json=None,
        source_name="synthetic-report",
        source_vendor=None,
        source_record_id=evidence_id,
        source_url=None,
        published_at=observed_at,
        observed_at=observed_at,
        effective_from=None,
        effective_to=None,
        instrument_ids=(),
        topic_tags=("synthetic",),
        quality=EvidenceQuality.PRIMARY,
        reliability=ReliabilityLevel.HIGH,
        confidence=Decimal("1"),
        content_sha256=content_hash,
        supersedes_evidence_id=None,
        recorded_by="user",
        schema_version=1,
    )
    with harness["factory"]() as uow:  # type: ignore[operator]
        uow.evidence.add(evidence)
        uow.subject_evidence_links.add(
            SubjectEvidenceLink(
                link_id=ids.new(EntityIdPrefix.REV),  # type: ignore[attr-defined]
                subject_id=subject_id,
                evidence_id=evidence_id,
                linked_at=observed_at,
                linked_by="user",
                schema_version=1,
            )
        )
        uow.commit()
    return evidence_id


def _impact_payload(
    revision_id: str,
    assumption_id: str,
    source_id: str,
    *,
    action: str = "create",
    impact_id: str | None = None,
    expected_version: int | None = None,
    rationale: str = "The exact source supports this assumption.",
) -> EvidenceImpactCandidatePayload:
    return EvidenceImpactCandidatePayload(
        action=action,  # type: ignore[arg-type]
        impact_id=impact_id,
        expected_version=expected_version,
        thesis_revision_id=revision_id if action != "retract" else None,
        assumption_id=assumption_id if action != "retract" else None,
        source=ResearchSourcePayload(kind="EVIDENCE", source_id=source_id)
        if action != "retract"
        else None,
        stance="supports" if action != "retract" else "uncertain",
        rationale=rationale,
    )


def _propose_impact(
    harness: dict[str, object],
    subject_id: str,
    payload: EvidenceImpactCandidatePayload,
    key: str,
    *,
    proposed_by: str = "codex",
):
    thesis = harness["thesis"]  # type: ignore[assignment]
    return thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=payload,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by=proposed_by,
        proposed_by_rationale="Synthetic evidence-link proposal.",
        idempotency_key=key,
    )


def _question_payload(
    action: str,
    *,
    question_id: str | None = None,
    expected_version: int | None = None,
    source_refs: tuple[ResearchSourcePayload, ...] = (),
    evidence_requirements: tuple[str, ...] = (),
    due_at: datetime | None = None,
    progress_note: str | None = None,
    answer_summary: str | None = None,
    criteria_met: bool = False,
    decision_ids: tuple[str, ...] = (),
    closed_reason: str | None = None,
) -> OpenQuestionCandidatePayload:
    return OpenQuestionCandidatePayload(
        action=action,  # type: ignore[arg-type]
        question_id=question_id,
        expected_version=expected_version,
        text="What will confirm the margin premise?" if action == "create" else None,
        evidence_requirements=evidence_requirements,
        due_at=due_at,
        progress_note=progress_note,
        source_refs=source_refs,
        answer_summary=answer_summary,
        criteria_met=criteria_met,
        decision_ids=decision_ids,
        closed_reason=closed_reason,
    )


def test_evidence_impact_candidate_is_review_gated_and_append_only(harness) -> None:  # type: ignore[no-untyped-def]
    subject_id, thesis_id, revision_id, assumption_id = _create_thesis(harness, "impact-gate")
    source_id = _add_evidence(harness, subject_id)
    payload = _impact_payload(revision_id, assumption_id, source_id)
    thesis = harness["thesis"]  # type: ignore[assignment]

    with harness["factory"]() as uow:  # type: ignore[operator]
        before = (
            len(uow.theses.list_by_subject(subject_id)),
            len(uow.decisions.list_by_subject(subject_id)),
            len(uow.evidence_impacts.list_by_subject(subject_id)),
        )
    proposed = _propose_impact(harness, subject_id, payload, "impact-gate-propose")
    assert proposed.ok and proposed.data is not None
    with harness["factory"]() as uow:  # type: ignore[operator]
        assert (
            len(uow.theses.list_by_subject(subject_id)),
            len(uow.decisions.list_by_subject(subject_id)),
            len(uow.evidence_impacts.list_by_subject(subject_id)),
        ) == before
        assert uow.candidates.get(proposed.data.candidate_id).status is CandidateStatus.PROPOSED

    self_confirm = thesis.confirm_candidate(proposed.data.candidate_id, reviewed_by="codex")  # type: ignore[attr-defined]
    assert not self_confirm.ok
    assert self_confirm.errors[0].code == "UNAUTHORIZED_REVIEWER"
    confirmed = thesis.confirm_candidate(proposed.data.candidate_id, reviewed_by="user")  # type: ignore[attr-defined]
    assert confirmed.ok
    with harness["factory"]() as uow:  # type: ignore[operator]
        impacts = uow.evidence_impacts.list_by_subject(subject_id)
        assert len(impacts) == 1
        assert impacts[0].version == 1
        assert impacts[0].state == "ACTIVE"
        assert impacts[0].candidate_id == proposed.data.candidate_id
        assert len(uow.theses.list_by_subject(subject_id)) == before[0]
        assert len(uow.decisions.list_by_subject(subject_id)) == before[1]

    replay = _propose_impact(harness, subject_id, payload, "impact-gate-propose")
    assert replay.ok and replay.data is not None
    assert replay.data.candidate_id == proposed.data.candidate_id
    with harness["factory"]() as uow:  # type: ignore[operator]
        assert len(uow.evidence_impacts.list_by_subject(subject_id)) == 1


def test_evidence_impact_scope_version_and_missing_source_retraction(harness) -> None:  # type: ignore[no-untyped-def]
    subject_id, _thesis_id, revision_id, assumption_id = _create_thesis(harness, "impact-scope")
    other_subject, _other_thesis, _other_revision, other_assumption = _create_thesis(
        harness, "impact-other"
    )
    source_id = _add_evidence(harness, subject_id)
    other_source_id = _add_evidence(harness, other_subject, title="Other subject report")
    future_source_id = _add_evidence(
        harness, subject_id, observed_at=NOW + timedelta(days=1), title="Future report"
    )

    cross_source = _propose_impact(
        harness,
        subject_id,
        _impact_payload(revision_id, assumption_id, other_source_id),
        "impact-cross-source",
    )
    assert not cross_source.ok
    assert cross_source.errors[0].code == "INPUT_VALIDATION_ERROR"
    cross_assumption = _propose_impact(
        harness,
        subject_id,
        _impact_payload(revision_id, other_assumption, source_id),
        "impact-cross-assumption",
    )
    assert not cross_assumption.ok
    assert cross_assumption.errors[0].code == "INPUT_VALIDATION_ERROR"
    future = _propose_impact(
        harness,
        subject_id,
        _impact_payload(revision_id, assumption_id, future_source_id),
        "impact-future-source",
    )
    assert not future.ok
    assert future.errors[0].code == "INPUT_VALIDATION_ERROR"

    thesis = harness["thesis"]  # type: ignore[assignment]
    first = _propose_impact(
        harness,
        subject_id,
        _impact_payload(revision_id, assumption_id, source_id),
        "impact-revise-first",
    )
    second = _propose_impact(
        harness,
        subject_id,
        _impact_payload(revision_id, assumption_id, source_id),
        "impact-revise-second",
    )
    assert first.ok and first.data is not None
    assert second.ok and second.data is not None
    assert thesis.confirm_candidate(first.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    with harness["factory"]() as uow:  # type: ignore[operator]
        impact = uow.evidence_impacts.list_by_subject(subject_id)[0]
        impact_id = impact.impact_id
    stale = thesis.confirm_candidate(second.data.candidate_id, reviewed_by="user")  # type: ignore[attr-defined]
    assert not stale.ok
    assert stale.errors[0].code == "DATA_CONTRACT_ERROR"

    revised = _propose_impact(
        harness,
        subject_id,
        _impact_payload(
            revision_id,
            assumption_id,
            source_id,
            action="revise",
            impact_id=impact_id,
            expected_version=1,
            rationale="The second review still supports the premise.",
        ),
        "impact-revise-valid",
    )
    assert revised.ok and revised.data is not None
    other_revision = _propose_impact(
        harness,
        subject_id,
        _impact_payload(
            revision_id,
            assumption_id,
            source_id,
            action="revise",
            impact_id=impact_id,
            expected_version=1,
            rationale="Concurrent review of the same version.",
        ),
        "impact-revise-concurrent",
    )
    assert other_revision.ok and other_revision.data is not None
    assert thesis.confirm_candidate(revised.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    with harness["factory"]() as uow:  # type: ignore[operator]
        latest = uow.evidence_impacts.get(impact_id)
        assert latest is not None and latest.version == 2
    stale_revision = thesis.confirm_candidate(other_revision.data.candidate_id, reviewed_by="user")
    assert not stale_revision.ok
    assert "expected_version" in stale_revision.errors[0].message

    with Session(harness["engine"]) as session:  # type: ignore[index]
        session.execute(
            delete(SubjectEvidenceLinkRow).where(SubjectEvidenceLinkRow.evidence_id == source_id)
        )
        session.execute(
            delete(ResearchEvidenceRow).where(ResearchEvidenceRow.evidence_id == source_id)
        )
        session.commit()
    retract = _propose_impact(
        harness,
        subject_id,
        _impact_payload(
            revision_id,
            assumption_id,
            source_id,
            action="retract",
            impact_id=impact_id,
            expected_version=2,
            rationale="The source was withdrawn from the visible scope.",
        ),
        "impact-retract-missing-source",
    )
    assert retract.ok and retract.data is not None
    assert thesis.confirm_candidate(retract.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    with harness["factory"]() as uow:  # type: ignore[operator]
        latest = uow.evidence_impacts.get(impact_id)
        assert latest is not None and latest.version == 3 and latest.state == "RETRACTED"
    links = harness["links"].get(subject_id)  # type: ignore[attr-defined]
    assert any(item["assumption_id"] == assumption_id for item in links["targets"])
    rendered = next(item for item in links["impacts"] if item["impact_id"] == impact_id)
    assert rendered["source_available"] is False


def test_research_question_metadata_answer_and_decision_link_are_review_gated(harness) -> None:  # type: ignore[no-untyped-def]
    subject_id, _thesis_id, _revision_id, _assumption_id = _create_thesis(
        harness, "question-workflow"
    )
    source_id = _add_evidence(harness, subject_id)
    source_ref = ResearchSourcePayload(kind="EVIDENCE", source_id=source_id)
    thesis = harness["thesis"]  # type: ignore[assignment]
    due_at = NOW + timedelta(days=5)
    created = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=_question_payload(
            "create",
            evidence_requirements=("Reported margin exceeds 40%",),
            due_at=due_at,
            progress_note="Waiting for the next report.",
            source_refs=(source_ref,),
        ),
        proposed_by="codex",
        proposed_by_rationale="Create an explicit evidence question.",
        idempotency_key="question-create",
    )
    assert created.ok and created.data is not None
    assert thesis.confirm_candidate(created.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    with harness["factory"]() as uow:  # type: ignore[operator]
        question = uow.questions.list_by_subject(subject_id)[0]
        assert question.version == 1
        assert question.evidence_requirements == ("Reported margin exceeds 40%",)
        assert question.due_at == due_at
        assert question.source_refs[0].source_id == source_id

    update = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=_question_payload(
            "update_research",
            question_id=question.question_id,
            expected_version=1,
            evidence_requirements=("Reported margin exceeds 42%",),
            due_at=due_at + timedelta(days=1),
            progress_note="The threshold was refined after reviewing the first report.",
            source_refs=(),
        ),
        proposed_by="codex",
        proposed_by_rationale="Update the explicit research plan.",
        idempotency_key="question-update",
    )
    assert update.ok and update.data is not None
    assert thesis.confirm_candidate(update.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    with harness["factory"]() as uow:  # type: ignore[operator]
        question = uow.questions.get(question.question_id)
        assert question.version == 2 and question.source_refs == ()

    missing_criteria = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=_question_payload(
            "answer",
            question_id=question.question_id,
            expected_version=2,
            answer_summary="The criterion is met.",
        ),
        proposed_by="codex",
        proposed_by_rationale="Try to answer without explicit criteria.",
        idempotency_key="question-answer-no-criteria",
    )
    assert not missing_criteria.ok
    assert missing_criteria.errors[0].code == "INPUT_VALIDATION_ERROR"
    missing_source = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=_question_payload(
            "answer",
            question_id=question.question_id,
            expected_version=2,
            answer_summary="The criterion is met.",
            criteria_met=True,
            source_refs=(),
        ),
        proposed_by="codex",
        proposed_by_rationale="Try to answer without exact evidence.",
        idempotency_key="question-answer-no-source",
    )
    assert not missing_source.ok
    assert missing_source.errors[0].code == "INPUT_VALIDATION_ERROR"
    answered = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=_question_payload(
            "answer",
            question_id=question.question_id,
            expected_version=2,
            answer_summary="The criterion is met by the report.",
            criteria_met=True,
            source_refs=(source_ref,),
        ),
        proposed_by="codex",
        proposed_by_rationale="Answer from an exact source after checking the criterion.",
        idempotency_key="question-answer",
    )
    assert answered.ok and answered.data is not None
    assert thesis.confirm_candidate(answered.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    with harness["factory"]() as uow:  # type: ignore[operator]
        question = uow.questions.get(question.question_id)
        assert question.status.value == "answered"
        assert question.version == 3
        answered_at = question.answered_at
        assert answered_at is not None
        assert question.source_refs[0].source_id == source_id

    prior_decision = harness["decisions"].append(  # type: ignore[attr-defined]
        subject_id=subject_id,
        decision_type=DecisionType.NO_ACTION,
        title="Recorded before answer",
        rationale="Synthetic prior decision for time-bound link rejection.",
        decided_at=answered_at - timedelta(minutes=1),
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        idempotency_key="decision-before-answer",
    )
    assert prior_decision.ok and prior_decision.data is not None
    invalid_link = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=_question_payload(
            "link_decision",
            question_id=question.question_id,
            expected_version=3,
            decision_ids=(prior_decision.data.decision_id,),
        ),
        proposed_by="codex",
        proposed_by_rationale="Try to link a Decision outside the answer window.",
        idempotency_key="question-link-before-answer",
    )
    assert not invalid_link.ok
    assert invalid_link.errors[0].code == "INPUT_VALIDATION_ERROR"

    valid_decision = harness["decisions"].append(  # type: ignore[attr-defined]
        subject_id=subject_id,
        decision_type=DecisionType.NO_ACTION,
        title="Reviewed answer",
        rationale="Decision recorded after the answered question.",
        decided_at=answered_at,
        decided_by="user",
        confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None,
        thesis_revision_ids=(),
        evidence_ids=(),
        report_ids=(),
        supersedes_decision_id=None,
        position_context_snapshot_id=None,
        idempotency_key="decision-after-answer",
    )
    assert valid_decision.ok and valid_decision.data is not None
    linked = thesis.propose_state_update(  # type: ignore[attr-defined]
        subject_id=subject_id,
        payload=_question_payload(
            "link_decision",
            question_id=question.question_id,
            expected_version=3,
            decision_ids=(valid_decision.data.decision_id,),
        ),
        proposed_by="codex",
        proposed_by_rationale="Link the reviewed Decision to the answered question.",
        idempotency_key="question-link-decision",
    )
    assert linked.ok and linked.data is not None
    assert thesis.confirm_candidate(linked.data.candidate_id, reviewed_by="user").ok  # type: ignore[attr-defined]
    with harness["factory"]() as uow:  # type: ignore[operator]
        question = uow.questions.get(question.question_id)
        decisions = uow.decisions.list_by_subject(subject_id)
        assert question.decision_ids == (valid_decision.data.decision_id,)
        assert len(decisions) == 2


def test_research_links_read_is_durable_only_and_idempotency_is_scoped(harness) -> None:  # type: ignore[no-untyped-def]
    subject_id, _thesis_id, revision_id, assumption_id = _create_thesis(harness, "query-read")
    other_subject, _other_thesis, _other_revision, _other_assumption = _create_thesis(
        harness, "query-other"
    )
    source_id = _add_evidence(harness, subject_id)
    payload = _impact_payload(revision_id, assumption_id, source_id)
    first = _propose_impact(harness, subject_id, payload, "same-key")
    assert first.ok and first.data is not None
    same = _propose_impact(harness, subject_id, payload, "same-key")
    assert same.ok and same.data is not None and same.data.candidate_id == first.data.candidate_id
    foreign = _propose_impact(harness, other_subject, payload, "same-key")
    assert not foreign.ok
    assert foreign.errors[0].code == "DUPLICATE_IDEMPOTENCY_KEY"
    different_payload = _impact_payload(
        revision_id, assumption_id, source_id, rationale="Changed rationale"
    )
    different = _propose_impact(harness, subject_id, different_payload, "same-key")
    assert not different.ok
    assert different.errors[0].code == "DUPLICATE_IDEMPOTENCY_KEY"

    with harness["factory"]() as uow:  # type: ignore[operator]
        before = (
            len(uow.candidates.list(subject_id=subject_id)),
            len(uow.evidence_impacts.list_by_subject(subject_id)),
            len(uow.decisions.list_by_subject(subject_id)),
        )
    result = harness["links"].get(subject_id)  # type: ignore[attr-defined]
    assert result["subject_id"] == subject_id
    assert result["coverage"]["sources"] == "COMPLETE"
    with harness["factory"]() as uow:  # type: ignore[operator]
        after = (
            len(uow.candidates.list(subject_id=subject_id)),
            len(uow.evidence_impacts.list_by_subject(subject_id)),
            len(uow.decisions.list_by_subject(subject_id)),
        )
    assert after == before


def test_due_question_queue_changes_only_after_complete_explicit_refresh(harness) -> None:  # type: ignore[no-untyped-def]
    subject_id, *_ = _create_thesis(harness, "question-due")
    thesis = harness["thesis"]
    proposal = thesis.propose_state_update(
        subject_id=subject_id,
        payload=OpenQuestionCandidatePayload(
            action="create",
            text="Which evidence is still missing?",
            due_at=NOW - timedelta(hours=1),
        ),
        proposed_by="user",
        proposed_by_rationale="Review the known research gap.",
        idempotency_key="due-question-create",
    )
    assert proposal.ok and proposal.data is not None
    assert thesis.confirm_candidate(proposal.data.candidate_id, reviewed_by="user").ok
    with harness["factory"]() as uow:
        question = uow.questions.list_by_subject(subject_id)[0]
    queue = ReviewItemService(
        SqlAlchemyReviewItemRepository(harness["engine"]),
        harness["clock"],
        harness["ids"],
    )
    refresh = ReviewItemRefreshService(
        harness["clock"],
        harness["decisions"],
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        queue,
        research_uow_factory=harness["factory"],
    )
    empty = {"ok": True, "data": {"items": [], "runs": [], "has_more": False}}
    batch = refresh.collect(empty, empty, empty, subject_id=subject_id)
    assert queue.list_open(subject_id=subject_id) == ()

    refresh.apply(batch)
    assert len(queue.list_open(subject_id=subject_id)) == 1
    answer = thesis.propose_state_update(
        subject_id=subject_id,
        payload=OpenQuestionCandidatePayload(
            action="answer",
            question_id=question.question_id,
            expected_version=1,
            answer_summary="This untracked legacy-style question is now answered.",
        ),
        proposed_by="user",
        proposed_by_rationale="Record the reviewed answer.",
        idempotency_key="due-question-answer",
    )
    assert answer.ok and answer.data is not None
    assert thesis.confirm_candidate(answer.data.candidate_id, reviewed_by="user").ok
    assert len(queue.list_open(subject_id=subject_id)) == 1

    def unavailable():  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic unavailable source")

    failed_refresh = ReviewItemRefreshService(
        harness["clock"],
        harness["decisions"],
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        queue,
        research_uow_factory=unavailable,
    )
    failed_refresh.apply(failed_refresh.collect(empty, empty, empty, subject_id=subject_id))
    assert len(queue.list_open(subject_id=subject_id)) == 1
    refresh.apply(refresh.collect(empty, empty, empty, subject_id=subject_id))
    assert queue.list_open(subject_id=subject_id) == ()


def test_decision_replay_reads_real_history_without_later_answers(harness) -> None:  # type: ignore[no-untyped-def]
    subject_id, _thesis_id, revision_id, assumption_id = _create_thesis(harness, "replay")
    source_id = _add_evidence(harness, subject_id)
    thesis = harness["thesis"]
    proposal = _propose_impact(
        harness, subject_id, _impact_payload(revision_id, assumption_id, source_id), "replay-link"
    )
    assert proposal.ok and proposal.data is not None
    assert thesis.confirm_candidate(proposal.data.candidate_id, reviewed_by="user").ok
    question_candidate = thesis.propose_state_update(
        subject_id=subject_id,
        payload=_question_payload("create"),
        proposed_by="codex",
        proposed_by_rationale="Synthetic historical question",
        idempotency_key="replay-question",
    )
    assert question_candidate.ok and question_candidate.data is not None
    assert thesis.confirm_candidate(question_candidate.data.candidate_id, reviewed_by="user").ok
    with harness["factory"]() as uow:
        question = uow.questions.list_by_subject(subject_id)[0]
        impact = uow.evidence_impacts.list_by_subject(subject_id)[0]

    harness["clock"].set(NOW + timedelta(days=1))
    late_source = _add_evidence(
        harness, subject_id, observed_at=NOW + timedelta(days=1),
        title="Late source must stay hidden",
    )
    answer = thesis.propose_state_update(
        subject_id=subject_id,
        payload=_question_payload(
            "answer", question_id=question.question_id, expected_version=1,
            answer_summary="Later answer must stay hidden",
        ),
        proposed_by="codex", proposed_by_rationale="Synthetic later answer",
        idempotency_key="replay-answer",
    )
    assert answer.ok and answer.data is not None
    assert thesis.confirm_candidate(answer.data.candidate_id, reviewed_by="user").ok
    retraction = _propose_impact(
        harness, subject_id,
        EvidenceImpactCandidatePayload(
            action="retract", impact_id=impact.impact_id, expected_version=1,
            rationale="Later retraction must stay hidden",
        ), "replay-retract",
    )
    assert retraction.ok and retraction.data is not None
    assert thesis.confirm_candidate(retraction.data.candidate_id, reviewed_by="user").ok
    decision = harness["decisions"].append(
        subject_id=subject_id, decision_type=DecisionType.NO_ACTION,
        title="Backdated synthetic review", rationale="Synthetic Decision context",
        decided_at=NOW, decided_by="user", confirmation_mode=ConfirmationMode.NORMAL,
        primary_instrument_id=None, thesis_revision_ids=(revision_id,),
        evidence_ids=(source_id, late_source), report_ids=(), supersedes_decision_id=None,
        position_context_snapshot_id=None, idempotency_key="replay-decision",
    )
    assert decision.ok and decision.data is not None
    writes: list[str] = []

    def observe_sql(_conn, _cursor, statement, _parameters, _context, _many):  # type: ignore[no-untyped-def]
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement.split()[0])

    event.listen(harness["engine"], "before_cursor_execute", observe_sql)
    try:
        result = DecisionReplayService(harness["factory"], harness["clock"]).get(
            subject_id, decision.data.decision_id
        )
    finally:
        event.remove(harness["engine"], "before_cursor_execute", observe_sql)
    assert result.cutoff == NOW
    assert result.recorded_at > result.cutoff
    assert result.revisions[0].assumptions[0].assumption_id == assumption_id
    assert [row.version for row in result.impacts] == [1]
    assert [row.source.source_id for row in result.sources] == [source_id]
    assert result.questions[0].text == question.text
    assert result.questions[0].history_availability == "UNAVAILABLE"
    assert "Later answer must stay hidden" not in result.model_dump_json()
    assert "Later retraction must stay hidden" not in result.model_dump_json()
    assert "Late source must stay hidden" not in result.model_dump_json()
    assert writes == []


@pytest.mark.parametrize("action", ["create", "retract"])
def test_research_links_omit_post_cutoff_current_impact_without_reverting_history(
    harness, action: str,
) -> None:  # type: ignore[no-untyped-def]
    subject_id, _, revision_id, assumption_id = _create_thesis(harness, f"cutoff-race-{action}")
    source_id = _add_evidence(harness, subject_id)
    proposal = _propose_impact(
        harness, subject_id, _impact_payload(revision_id, assumption_id, source_id),
        f"cutoff-race-{action}-create",
    )
    assert proposal.ok and proposal.data is not None
    candidate_id = proposal.data.candidate_id
    if action == "retract":
        assert harness["thesis"].confirm_candidate(candidate_id, reviewed_by="user").ok
        with harness["factory"]() as uow:
            current = uow.evidence_impacts.list_by_subject(subject_id)[0]
        retraction = _propose_impact(
            harness, subject_id,
            EvidenceImpactCandidatePayload(
                action="retract", impact_id=current.impact_id, expected_version=1,
                rationale="Reviewed retraction committed after the read cutoff.",
            ), f"cutoff-race-{action}-retract",
        )
        assert retraction.ok and retraction.data is not None
        candidate_id = retraction.data.candidate_id

    original_factory = harness["factory"]
    first_read = True

    def read_factory():  # type: ignore[no-untyped-def]
        nonlocal first_read
        if first_read:
            first_read = False
            # A legitimate review commits after get() samples its cutoff and
            # before its first DB read. No timestamps or stored rows are forged.
            harness["clock"].set(NOW + timedelta(seconds=1))
            assert harness["thesis"].confirm_candidate(candidate_id, reviewed_by="user").ok
        return original_factory()

    result = ResearchLinksQueryService(read_factory, harness["clock"]).get(subject_id)
    assert result["as_of"] == NOW
    assert result["impacts"] == []
    assert result["coverage"]["impacts"] == "PARTIAL"
    assert result["impacts_has_more"] is False
    assert result["coverage"]["impact_history"] == "COMPLETE"
    # History keeps the existing cutoff projection; the current view must not
    # replace a later current version with its obsolete earlier active version.
    assert [row["version"] for row in result["impact_history"]] == (
        [1] if action == "retract" else []
    )
    with original_factory() as uow:
        persisted = uow.evidence_impacts.list_by_subject(subject_id)
        assert len(persisted) == 1
        assert persisted[0].confirmed_at == NOW + timedelta(seconds=1)
        assert persisted[0].version == (2 if action == "retract" else 1)
