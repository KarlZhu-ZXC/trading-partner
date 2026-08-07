"""ResearchStateQueryService unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.dto.research import (
    AssumptionPayload,
    SubjectUpdateCandidatePayload,
    ThesisRevisionCandidatePayload,
)
from application.services.research_state_query_service import ResearchStateQueryService
from application.services.research_subject_service import ResearchSubjectService
from application.services.thesis_revision_service import ThesisRevisionService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfidenceBand,
    ConfirmationMode,
    InvestmentRating,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
)
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
    path = tmp_path / "rsq.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    yield (
        ResearchStateQueryService(factory, clock, ids, redactor),
        ResearchSubjectService(factory, clock, ids, redactor),
        ThesisRevisionService(factory, clock, ids, redactor),
    )
    eng.dispose()


def test_get_state_not_found(harness) -> None:  # type: ignore[no-untyped-def]
    query, *_ = harness
    env = query.get_state("case_missing")
    assert env.ok is False
    assert env.errors[0].code == "INVESTMENT_CASE_NOT_FOUND"


def test_get_state_includes_children(harness) -> None:  # type: ignore[no-untyped-def]
    query, subjects, thesis = harness
    created = subjects.create_subject(
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA",
        summary="GPU",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=("ai",),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="qs-1",
    )
    assert created.data is not None
    subject_id = created.data.subject_id

    activate = thesis.propose_state_update(
        subject_id=subject_id,
        payload=SubjectUpdateCandidatePayload(
            action="update",
            new_status=ResearchSubjectStatus.ACTIVE,
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Activate the Case before confirming live judgment.",
        idempotency_key="qs-activate",
    )
    assert activate.ok and activate.data is not None
    assert thesis.confirm_candidate(activate.data.candidate_id, reviewed_by="user").ok

    proposed = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=ThesisRevisionCandidatePayload(
            kind="thesis_revision",
            title="T",
            statement="Statement body",
            rationale="Rationale body",
            confidence_band=ConfidenceBand.MEDIUM,
            rating=InvestmentRating.WATCH,
            invalidation_check_note="note",
            assumptions=(
                AssumptionPayload(
                    statement="A",
                    basis="B",
                    falsifiability="F",
                ),
            ),
            thesis_role=ThesisRole.PRIMARY,
        ),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="qs-prop",
    )
    assert proposed.data is not None
    assert thesis.confirm_candidate(proposed.data.candidate_id, reviewed_by="user").ok

    # pending candidate still visible
    pending = thesis.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=ThesisRevisionCandidatePayload(
            kind="thesis_revision",
            title="Bear",
            statement="Bear statement body",
            rationale="Bear rationale body",
            confidence_band=ConfidenceBand.LOW,
            rating=InvestmentRating.AVOID,
            invalidation_check_note="note2",
            thesis_role=ThesisRole.BEAR,
        ),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="pending",
        idempotency_key="qs-pending",
    )
    assert pending.ok

    state = query.get_state(subject_id)
    assert state.ok is True
    assert state.data is not None
    assert state.data.subject.subject_id == subject_id
    assert len(state.data.theses) == 1
    assert len(state.data.latest_revisions) == 1
    assert len(state.data.assumptions) == 1
    assert len(state.data.pending_candidates) >= 1
