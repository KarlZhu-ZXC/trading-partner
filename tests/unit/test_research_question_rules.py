"""Focused application-rule tests for versioned Research Questions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from application.dto.research import OpenQuestionCandidatePayload
from application.dto.research_links import ResearchSourcePayload
from application.services.research_question_rules import (
    apply_question,
    prepare_question,
    validate_question,
)
from domain.common.enums import OpenQuestionStatus
from domain.common.errors import DataContractError
from domain.research.evidence_impact import ResearchSourceRef
from domain.research.models import OpenQuestion

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
SUBJECT_ID = "case_00000000-0000-7000-8000-000000000001"
OTHER_SUBJECT_ID = "case_00000000-0000-7000-8000-000000000002"
QUESTION_ID = "rev_00000000-0000-7000-8000-000000000010"
DECISION_ID = "decision_00000000-0000-7000-8000-000000000001"
SOURCE_ID = "evidence_00000000-0000-7000-8000-000000000001"
RUN_ID = "run_00000000-0000-7000-8000-000000000001"


def _source() -> ResearchSourcePayload:
    return ResearchSourcePayload(kind="EVIDENCE", source_id=SOURCE_ID)


class _Repo:
    def __init__(self, value: object | None = None) -> None:
        self.value = value
        self.calls: list[tuple[object, ...]] = []

    def get(self, *args: object, **kwargs: object) -> object | None:
        self.calls.append((*args, *kwargs.values()))
        return self.value


class _QuestionRepo(_Repo):
    def add(self, question: object) -> None:
        self.calls.append(("add", question))

    def update_research(self, question_id: str, **kwargs: object) -> None:
        self.calls.append(("update_research", question_id, kwargs))

    def answer(self, question_id: str, **kwargs: object) -> None:
        self.calls.append(("answer", question_id, kwargs))

    def close_without_answer(self, question_id: str, **kwargs: object) -> None:
        self.calls.append(("close", question_id, kwargs))

    def mark_stale(self, question_id: str, **kwargs: object) -> None:
        self.calls.append(("stale", question_id, kwargs))

    def link_decisions(self, question_id: str, **kwargs: object) -> None:
        self.calls.append(("link", question_id, kwargs))


class _Ids:
    def new(self, _prefix: object) -> str:
        return QUESTION_ID


def _question(
    *,
    version: int = 1,
    status: OpenQuestionStatus = OpenQuestionStatus.OPEN,
    requirements: tuple[str, ...] = (),
    source_refs: tuple[ResearchSourceRef, ...] = (),
    decision_ids: tuple[str, ...] = (),
    answered_at: datetime | None = None,
) -> OpenQuestion:
    if status is OpenQuestionStatus.ANSWERED and answered_at is None:
        answered_at = NOW - timedelta(hours=2)
    return OpenQuestion(
        question_id=QUESTION_ID,
        subject_id=SUBJECT_ID,
        text="Which margin assumption needs new evidence?",
        status=status,
        asked_at=NOW - timedelta(days=1),
        answered_at=answered_at,
        answer_summary="Observed margin trend remains mixed."
        if status is OpenQuestionStatus.ANSWERED
        else None,
        closed_without_answer_reason=None,
        proposed_by="user",
        version=version,
        evidence_requirements=requirements,
        due_at=None,
        progress_note="Initial research note" if requirements else None,
        source_refs=source_refs,
        decision_ids=decision_ids,
    )


def _uow(
    question: OpenQuestion | None = None,
    *,
    source: object | None = object(),
    decision: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        subjects=_Repo(SimpleNamespace(subject_id=SUBJECT_ID)),
        questions=_QuestionRepo(question),
        research_sources=_Repo(source),
        decisions=_Repo(decision),
    )


def test_prepare_pins_legacy_version_one_answer() -> None:
    uow = _uow(_question())
    payload = OpenQuestionCandidatePayload(
        action="answer",
        question_id=QUESTION_ID,
        answer_summary="A plain legacy answer.",
    )

    prepared = prepare_question(uow, SUBJECT_ID, payload, NOW)

    assert prepared.expected_version == 1


def test_version_two_requires_explicit_expected_version() -> None:
    uow = _uow(_question(version=2))
    payload = OpenQuestionCandidatePayload(
        action="answer",
        question_id=QUESTION_ID,
        answer_summary="An answer without a pinned version.",
    )

    with pytest.raises(DataContractError, match="version is required"):
        validate_question(uow, SUBJECT_ID, payload, NOW)


def test_summary_only_source_repository_is_rejected_for_new_question_metadata() -> None:
    uow = _uow(source=None)
    payload = OpenQuestionCandidatePayload(
        action="create",
        text="What evidence should be collected next?",
        source_refs=(_source(),),
    )

    with pytest.raises(DataContractError, match="source reference"):
        validate_question(uow, SUBJECT_ID, payload, NOW)


def test_update_research_requires_progress_and_evidence_requirements() -> None:
    uow = _uow(_question())
    payload = OpenQuestionCandidatePayload(
        action="update_research",
        question_id=QUESTION_ID,
        expected_version=1,
        progress_note="Only a note, without a criterion.",
    )

    with pytest.raises(DataContractError, match="evidence_requirements"):
        validate_question(uow, SUBJECT_ID, payload, NOW)


def test_answer_requires_criteria_and_an_exact_source() -> None:
    question = _question(requirements=("gross margin guide",))
    uow = _uow(question)
    payload = OpenQuestionCandidatePayload(
        action="answer",
        question_id=QUESTION_ID,
        answer_summary="The criterion is met.",
    )

    with pytest.raises(DataContractError, match="criteria_met"):
        validate_question(uow, SUBJECT_ID, payload, NOW)

    criteria_payload = OpenQuestionCandidatePayload(
        action="answer",
        question_id=QUESTION_ID,
        answer_summary="The criterion is met.",
        criteria_met=True,
    )
    with pytest.raises(DataContractError, match="source reference"):
        validate_question(uow, SUBJECT_ID, criteria_payload, NOW)


def test_answer_keeps_existing_source_when_payload_omits_source_refs() -> None:
    question = _question(
        requirements=("gross margin guide",),
        source_refs=(_source().to_domain(),),
    )
    uow = _uow(question)
    payload = OpenQuestionCandidatePayload(
        action="answer",
        question_id=QUESTION_ID,
        answer_summary="The criterion is met.",
        criteria_met=True,
    )

    kind, question_id = apply_question(
        uow,
        SimpleNamespace(subject_id=SUBJECT_ID, proposed_by="user"),
        payload,
        "user",
        NOW,
        _Ids(),
    )

    assert (kind, question_id) == ("open_question", QUESTION_ID)
    _name, _qid, kwargs = uow.questions.calls[-1]
    assert kwargs["source_refs"] == (_source().to_domain(),)
    assert kwargs["expected_version"] == 1


def test_link_decision_requires_post_answer_times_and_dedupes_ids() -> None:
    answered_at = NOW - timedelta(hours=2)
    question = _question(status=OpenQuestionStatus.ANSWERED, answered_at=answered_at)
    decision = SimpleNamespace(
        decision_id=DECISION_ID,
        subject_id=SUBJECT_ID,
        decided_at=answered_at + timedelta(minutes=5),
        recorded_at=answered_at + timedelta(minutes=6),
    )
    uow = _uow(question, decision=decision)
    # Pydantic rejects duplicate IDs, so use a direct object-shaped payload for
    # this rule-level dedupe check while retaining the same DTO attributes.
    payload = SimpleNamespace(
        action="link_decision",
        question_id=QUESTION_ID,
        expected_version=1,
        decision_ids=(DECISION_ID, DECISION_ID),
        source_refs=(),
        evidence_requirements=(),
        due_at=None,
        progress_note=None,
        criteria_met=False,
        model_fields_set=frozenset(),
    )
    apply_question(
        uow,
        SimpleNamespace(subject_id=SUBJECT_ID, proposed_by="user"),
        payload,  # type: ignore[arg-type]
        "user",
        NOW,
        _Ids(),
    )

    _name, _qid, kwargs = uow.questions.calls[-1]
    assert kwargs["decision_ids"] == (DECISION_ID,)


def test_link_decision_preserves_existing_links() -> None:
    answered_at = NOW - timedelta(hours=2)
    prior_id = "decision_00000000-0000-7000-8000-000000000002"
    question = _question(
        status=OpenQuestionStatus.ANSWERED,
        answered_at=answered_at,
        decision_ids=(prior_id,),
    )
    decision = SimpleNamespace(
        decision_id=DECISION_ID,
        subject_id=SUBJECT_ID,
        decided_at=answered_at + timedelta(minutes=5),
        recorded_at=answered_at + timedelta(minutes=6),
    )
    uow = _uow(question, decision=decision)
    payload = OpenQuestionCandidatePayload(
        action="link_decision",
        question_id=QUESTION_ID,
        expected_version=1,
        decision_ids=(DECISION_ID,),
    )

    apply_question(
        uow,
        SimpleNamespace(subject_id=SUBJECT_ID, proposed_by="user"),
        payload,
        "user",
        NOW,
        _Ids(),
    )

    _name, _qid, kwargs = uow.questions.calls[-1]
    assert kwargs["decision_ids"] == (prior_id, DECISION_ID)


def test_link_decision_rejects_future_decision() -> None:
    question = _question(status=OpenQuestionStatus.ANSWERED)
    future = SimpleNamespace(
        decision_id=DECISION_ID,
        subject_id=SUBJECT_ID,
        decided_at=NOW + timedelta(minutes=1),
        recorded_at=NOW + timedelta(minutes=2),
    )
    uow = _uow(question, decision=future)
    payload = OpenQuestionCandidatePayload(
        action="link_decision",
        question_id=QUESTION_ID,
        expected_version=1,
        decision_ids=(DECISION_ID,),
    )

    with pytest.raises(DataContractError, match="between question answer"):
        validate_question(uow, SUBJECT_ID, payload, NOW)
