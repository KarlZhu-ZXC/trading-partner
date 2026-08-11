"""OpenQuestionService unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.services.open_question_service import OpenQuestionService
from application.services.research_state_query_service import ResearchStateQueryService
from application.services.research_subject_service import ResearchSubjectService
from application.services.thesis_revision_service import ThesisRevisionService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import OpenQuestionStatus, ResearchSubjectType
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def harness(orm_sqlite_url: str):  # type: ignore[no-untyped-def]
    eng = create_engine(orm_sqlite_url)
    _enable_fk(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    yield (
        OpenQuestionService(factory, clock, ids, redactor),
        ThesisRevisionService(factory, clock, ids, redactor),
        ResearchSubjectService(factory, clock, ids, redactor),
        ResearchStateQueryService(factory, clock, ids, redactor),
    )
    eng.dispose()


def test_propose_question_and_answer_via_confirm(harness) -> None:  # type: ignore[no-untyped-def]
    oq, thesis, subjects, query = harness
    created = subjects.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="Theme",
        summary="Summary",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="oq-case",
    )
    assert created.data is not None
    subject_id = created.data.subject_id

    proposed = oq.propose_question(
        subject_id=subject_id,
        text="What is competitive moat durability?",
        proposed_by="codex",
        proposed_by_rationale="need clarity",
        idempotency_key="oq-1",
    )
    assert proposed.ok is True
    assert proposed.data is not None
    # Still only candidate — no formal question until confirm
    listed = query.list_open_questions(subject_id)
    assert listed.ok and listed.data is not None
    assert listed.data.items == ()

    conf = thesis.confirm_candidate(proposed.data.candidate_id, reviewed_by="user")
    assert conf.ok is True
    listed2 = query.list_open_questions(subject_id)
    assert listed2.data is not None
    assert len(listed2.data.items) == 1
    qid = listed2.data.items[0].question_id
    assert listed2.data.items[0].status in {OpenQuestionStatus.OPEN, "open"}

    ans = oq.propose_answer(
        subject_id=subject_id,
        question_id=qid,
        answer_summary="Strong ecosystem lock-in",
        proposed_by="codex",
        proposed_by_rationale="answered",
        idempotency_key="oq-ans",
    )
    assert ans.ok and ans.data is not None
    conf2 = thesis.confirm_candidate(ans.data.candidate_id, reviewed_by="user")
    assert conf2.ok is True

    listed3 = query.list_open_questions(subject_id)
    assert listed3.data is not None
    assert listed3.data.items[0].status in {OpenQuestionStatus.ANSWERED, "answered"}
    assert listed3.data.items[0].answer_summary == "Strong ecosystem lock-in"


@pytest.mark.parametrize(
    ("operation", "extra"),
    [
        ("answer", {"answer_summary": "Foreign answer"}),
        ("mark_stale", {}),
        ("close", {"closed_reason": "Foreign close"}),
    ],
)
def test_question_mutation_proposals_reject_cross_subject_question(
    harness,
    operation: str,
    extra: dict[str, str],
) -> None:  # type: ignore[no-untyped-def]
    oq, _thesis, subjects, query = harness
    first = subjects.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="First subject",
        summary="First scope",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key=f"oq-scope-first-{operation}",
    )
    second = subjects.create_subject(
        subject_type=ResearchSubjectType.THEME,
        title="Second subject",
        summary="Second scope",
        primary_instrument_id=None,
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key=f"oq-scope-second-{operation}",
    )
    assert first.ok and first.data is not None
    assert second.ok and second.data is not None
    created = oq.propose_question(
        subject_id=first.data.subject_id,
        text="Question owned by first subject",
        proposed_by="codex",
        proposed_by_rationale="Create scope fixture",
        idempotency_key=f"oq-scope-question-{operation}",
    )
    assert created.ok and created.data is not None
    assert _thesis.confirm_candidate(created.data.candidate_id, reviewed_by="user").ok
    listed = query.list_open_questions(first.data.subject_id)
    assert listed.ok and listed.data is not None
    question_id = listed.data.items[0].question_id

    if operation == "answer":
        rejected = oq.propose_answer(
            subject_id=second.data.subject_id,
            question_id=question_id,
            proposed_by="codex",
            proposed_by_rationale="Reject cross-subject answer",
            idempotency_key=f"oq-scope-answer-{operation}",
            **extra,
        )
    elif operation == "mark_stale":
        rejected = oq.propose_mark_stale(
            subject_id=second.data.subject_id,
            question_id=question_id,
            proposed_by="codex",
            proposed_by_rationale="Reject cross-subject stale transition",
            idempotency_key=f"oq-scope-stale-{operation}",
        )
    else:
        rejected = oq.propose_close(
            subject_id=second.data.subject_id,
            question_id=question_id,
            proposed_by="codex",
            proposed_by_rationale="Reject cross-subject close",
            idempotency_key=f"oq-scope-close-{operation}",
            **extra,
        )
    assert not rejected.ok
    assert rejected.errors[0].code == "INPUT_VALIDATION_ERROR"
    assert rejected.errors[0].retryable is False
