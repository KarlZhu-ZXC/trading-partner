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
from infrastructure.persistence.metadata import Base
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
def harness(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "oq.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
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
