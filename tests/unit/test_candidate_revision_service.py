"""ThesisRevisionService / candidate lifecycle unit tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.research import (
    AssumptionPayload,
    InvalidationPayload,
    ThesisRevisionCandidatePayload,
)
from application.services.investment_case_service import InvestmentCaseService
from application.services.thesis_revision_service import ThesisRevisionService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.actor import ActorContext
from domain.common.enums import (
    CandidateStatus,
    ConfidenceBand,
    ConfirmationMode,
    InvalidationSeverity,
    InvestmentCaseType,
    InvestmentRating,
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
    path = tmp_path / "cand.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    cases = InvestmentCaseService(factory, clock, ids, redactor)
    thesis = ThesisRevisionService(factory, clock, ids, redactor)
    yield cases, thesis, factory, clock, ids, eng
    eng.dispose()


def _create_case(cases: InvestmentCaseService, key: str = "case-1") -> str:
    env = cases.create_case(
        case_type=InvestmentCaseType.COMPANY,
        title="NVDA",
        summary="GPU demand",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=("ai",),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key=key,
    )
    assert env.ok and env.data is not None
    return env.data.case_id


def _revision_payload() -> ThesisRevisionCandidatePayload:
    return ThesisRevisionCandidatePayload(
        kind="thesis_revision",
        title="Primary demand thesis",
        statement="Demand is structural",
        rationale="Capex cycle continues",
        confidence_band=ConfidenceBand.HIGH,
        rating=InvestmentRating.BUY,
        invalidation_check_note="Watch gross margin",
        assumptions=(
            AssumptionPayload(
                statement="AI spend continues",
                basis="Hyperscaler guidance",
                falsifiability="Capex cuts 2 quarters",
            ),
        ),
        invalidations=(
            InvalidationPayload(
                description="GM collapse",
                observable="Gross margin < 50%",
                severity=InvalidationSeverity.HARD,
            ),
        ),
        thesis_role=ThesisRole.PRIMARY,
    )


def test_propose_idempotent_same_payload_warning(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_ = harness
    case_id = _create_case(cases)
    payload = _revision_payload()
    first = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=payload,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="initial",
        idempotency_key="prop-1",
    )
    second = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=payload,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="initial",
        idempotency_key="prop-1",
    )
    assert first.ok and second.ok
    assert first.data is not None and second.data is not None
    assert first.data.candidate_id == second.data.candidate_id
    assert any(w.code == "DUPLICATE_IDEMPOTENCY_KEY" for w in second.warnings)


def test_propose_idempotent_different_payload_conflict(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_ = harness
    case_id = _create_case(cases)
    payload = _revision_payload()
    first = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=payload,
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="a",
        idempotency_key="prop-diff",
    )
    assert first.ok
    other = ThesisRevisionCandidatePayload(
        kind="thesis_revision",
        title="Different",
        statement="Different statement long enough",
        rationale="Different rationale long enough",
        confidence_band=ConfidenceBand.LOW,
        rating=InvestmentRating.HOLD,
        invalidation_check_note="Other note",
        thesis_role=ThesisRole.PRIMARY,
    )
    second = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=other,
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="b",
        idempotency_key="prop-diff",
    )
    assert second.ok is False
    assert second.errors[0].code == "DUPLICATE_IDEMPOTENCY_KEY"


def test_codex_cannot_confirm(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_ = harness
    case_id = _create_case(cases)
    proposed = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="no-self",
    )
    assert proposed.data is not None
    env = thesis.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="codex",
        review_note="self",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_confirm_writes_revision_assumptions_invalidations(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, factory, *_ = harness
    case_id = _create_case(cases)
    proposed = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="confirm-ok",
    )
    assert proposed.data is not None
    confirmed = thesis.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
        review_note="looks good",
    )
    assert confirmed.ok is True
    assert confirmed.data is not None
    assert confirmed.data.affected_entity_type == "thesis_revision"
    assert confirmed.data.research_state is not None
    assert len(confirmed.data.research_state.theses) == 1
    assert confirmed.data.research_state.theses[0].current_revision_no == 1
    assert len(confirmed.data.research_state.assumptions) == 1
    assert len(confirmed.data.research_state.invalidations) == 1
    inv = confirmed.data.research_state.invalidations[0]
    assert inv.severity in {"hard", InvalidationSeverity.HARD}
    assert inv.status in {"armed", "ARMED"} or str(inv.status) == "armed"

    with factory() as uow:
        theses = uow.theses.list_by_case(case_id)
        assert len(theses) == 1
        revs = uow.revisions.list_by_thesis(theses[0].thesis_id)
        assert len(revs) == 1
        assert theses[0].current_revision_no == revs[0].revision_no


def test_chat_authorized_confirm_persists_user_decision_and_relay_provenance(
    harness,
) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_, eng = harness
    case_id = _create_case(cases, key="case-chat-auth")
    proposed = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="chat-auth-confirm",
    )
    assert proposed.data is not None
    authorization = "我确认采用这个候选"

    confirmed = thesis.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
        review_note="explicit chat authorization",
        actor_context=ActorContext.codex_chat_authorized(
            request_id="req_chat_confirm",
            authorization_note=authorization,
        ),
    )

    assert confirmed.ok is True
    with Session(eng) as session:
        payload_json = session.execute(
            text(
                "SELECT payload_json FROM system_audit_log "
                "WHERE event_type = 'phase1b.candidate.confirmed'"
            )
        ).scalar_one()
    payload = json.loads(payload_json)
    assert payload["reviewed_by"] == "user"
    assert payload["actor_type"] == "user"
    assert payload["actor_assurance"] == "caller_asserted"
    assert payload["submitted_via"] == "codex_chat"
    assert payload["user_instruction"] == authorization


def test_confirm_advances_revision_no(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_ = harness
    case_id = _create_case(cases)
    p1 = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p1",
        idempotency_key="rev-1",
    )
    assert p1.data is not None
    c1 = thesis.confirm_candidate(p1.data.candidate_id, reviewed_by="user")
    assert c1.ok and c1.data is not None
    thesis_id = c1.data.research_state.theses[0].thesis_id  # type: ignore[union-attr]

    p2 = thesis.propose_revision(
        case_id=case_id,
        thesis_id=thesis_id,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p2",
        idempotency_key="rev-2",
    )
    assert p2.data is not None
    c2 = thesis.confirm_candidate(p2.data.candidate_id, reviewed_by="user")
    assert c2.ok and c2.data is not None
    assert c2.data.research_state is not None
    assert c2.data.research_state.theses[0].current_revision_no == 2


def test_reject_does_not_write_formal_rows(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, factory, *_ = harness
    case_id = _create_case(cases)
    proposed = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="rej-1",
    )
    assert proposed.data is not None
    rejected = thesis.reject_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
        rejection_reason="not ready",
    )
    assert rejected.ok is True
    assert rejected.data is not None
    assert rejected.data.status in {CandidateStatus.REJECTED, "rejected"}

    with factory() as uow:
        assert uow.theses.list_by_case(case_id) == ()
        assert uow.candidates.get(proposed.data.candidate_id).payload_json


def test_withdraw_only_by_proposer_or_user(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_ = harness
    case_id = _create_case(cases)
    proposed = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="wd-1",
    )
    assert proposed.data is not None
    # other agent cannot withdraw
    bad = thesis.withdraw_candidate(
        proposed.data.candidate_id,
        reviewed_by="other_agent",
        review_note="nope",
    )
    assert bad.ok is False
    assert bad.errors[0].code == "UNAUTHORIZED_REVIEWER"

    ok = thesis.withdraw_candidate(
        proposed.data.candidate_id,
        reviewed_by="codex",
        review_note="changed mind",
    )
    assert ok.ok is True
    assert ok.data is not None
    assert ok.data.status in {CandidateStatus.WITHDRAWN, "withdrawn"}


def test_codex_cannot_withdraw_strict_review(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_ = harness
    case_id = _create_case(cases)
    proposed = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="p",
        idempotency_key="wd-strict",
    )
    assert proposed.data is not None
    env = thesis.withdraw_candidate(
        proposed.data.candidate_id,
        reviewed_by="codex",
        review_note="try",
    )
    assert env.ok is False
    assert env.errors[0].code == "UNAUTHORIZED_REVIEWER"


def test_active_primary_unique(harness) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, *_ = harness
    case_id = _create_case(cases)
    p1 = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p1",
        idempotency_key="pri-1",
    )
    assert p1.data is not None
    assert thesis.confirm_candidate(p1.data.candidate_id, reviewed_by="user").ok

    p2 = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_revision_payload(),
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="p2",
        idempotency_key="pri-2",
    )
    assert p2.data is not None
    c2 = thesis.confirm_candidate(p2.data.candidate_id, reviewed_by="user")
    assert c2.ok is False
    assert c2.errors[0].code == "DATA_CONTRACT_ERROR"
