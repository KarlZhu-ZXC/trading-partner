"""Integration: candidate propose → reject/confirm/withdraw lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.dto.research import ThesisRevisionCandidatePayload
from application.services.investment_case_service import InvestmentCaseService
from application.services.thesis_revision_service import ThesisRevisionService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfidenceBand,
    ConfirmationMode,
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
def services(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "lifecycle.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(eng, clock, ids, redactor)

    yield (
        InvestmentCaseService(factory, clock, ids, redactor),
        ThesisRevisionService(factory, clock, ids, redactor),
        factory,
    )
    eng.dispose()


def _payload(title: str = "Primary") -> ThesisRevisionCandidatePayload:
    return ThesisRevisionCandidatePayload(
        kind="thesis_revision",
        title=title,
        statement="Demand is structural over multi-year horizon",
        rationale="Hyperscaler capex and CUDA lock-in",
        confidence_band=ConfidenceBand.HIGH,
        rating=InvestmentRating.BUY,
        invalidation_check_note="Monitor GM and export controls",
        thesis_role=ThesisRole.PRIMARY,
    )


def test_propose_reject_then_same_idempotency_returns_duplicate(services) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, _factory = services
    created = cases.create_case(
        case_type=InvestmentCaseType.COMPANY,
        title="NVDA",
        summary="GPU",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="lc-case",
    )
    assert created.data is not None
    case_id = created.data.case_id

    p = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="first",
        idempotency_key="lc-key",
    )
    assert p.data is not None
    rejected = thesis.reject_candidate(
        p.data.candidate_id,
        reviewed_by="user",
        rejection_reason="not yet",
    )
    assert rejected.ok

    # Same key after reject still returns original candidate (unique key) + warning
    again = thesis.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="first",
        idempotency_key="lc-key",
    )
    assert again.ok is True
    assert again.data is not None
    assert again.data.candidate_id == p.data.candidate_id
    assert any(w.code == "DUPLICATE_IDEMPOTENCY_KEY" for w in again.warnings)


def test_strict_review_user_confirm_lands_revision(services) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, factory = services
    created = cases.create_case(
        case_type=InvestmentCaseType.COMPANY,
        title="NVDA",
        summary="GPU",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="external_agent",
        idempotency_key="lc-case2",
    )
    assert created.data is not None
    p = thesis.propose_revision(
        case_id=created.data.case_id,
        thesis_id=None,
        payload=_payload("Strict"),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="strict",
        idempotency_key="lc-strict",
    )
    assert p.data is not None
    conf = thesis.confirm_candidate(p.data.candidate_id, reviewed_by="user")
    assert conf.ok is True
    assert conf.data is not None
    assert conf.data.research_state is not None
    assert len(conf.data.research_state.theses) == 1

    with factory() as uow:
        theses = uow.theses.list_by_case(created.data.case_id)
        revs = uow.revisions.list_by_thesis(theses[0].thesis_id)
        assert len(revs) == 1
        assert revs[0].confirmation_mode.value == "strict_review"


def test_codex_self_confirm_strict_review_forbidden(services) -> None:  # type: ignore[no-untyped-def]
    cases, thesis, _ = services
    created = cases.create_case(
        case_type=InvestmentCaseType.THEME,
        title="T",
        summary="S",
        primary_instrument_id=None,
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="lc-case3",
    )
    assert created.data is not None
    p = thesis.propose_revision(
        case_id=created.data.case_id,
        thesis_id=None,
        payload=_payload("Self"),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="x",
        idempotency_key="lc-self",
    )
    assert p.data is not None
    conf = thesis.confirm_candidate(p.data.candidate_id, reviewed_by="codex")
    assert conf.ok is False
    assert conf.errors[0].code == "UNAUTHORIZED_REVIEWER"
