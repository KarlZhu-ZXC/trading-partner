"""Focused Phase 4A structured Decision Record acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.services.decision_record_service import DecisionRecordService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfirmationMode,
    DecisionScenario,
    DecisionType,
    ResearchSubjectStatus,
    ResearchSubjectType,
    ThesisRole,
    ThesisStatus,
)
from domain.common.ids import EntityIdPrefix
from domain.research.models import RESEARCH_SCHEMA_VERSION, ResearchSubject, Thesis
from domain.trade_plan.enums import TradePlanStatus
from domain.trade_plan.models import TradePlan
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.mcp.schemas import DecisionRecordAppendInput

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


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

    subject_id = ids.new(EntityIdPrefix.SUBJECT)
    thesis_id = ids.new(EntityIdPrefix.THESIS)
    plan_id = ids.new(EntityIdPrefix.TRADE_PLAN)
    subject = ResearchSubject(
        subject_id=subject_id,
        subject_type=ResearchSubjectType.THEME,
        title="Phase 4A Decision fixture",
        summary="Structured Decision Record fixture",
        status=ResearchSubjectStatus.ACTIVE,
        primary_instrument_id=None,
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
    thesis = Thesis(
        thesis_id=thesis_id,
        subject_id=subject_id,
        title="Fixture Thesis",
        role=ThesisRole.PRIMARY,
        status=ThesisStatus.DRAFT,
        current_revision_no=1,
        latest_revision_id=ids.new(EntityIdPrefix.REV),
        parent_thesis_id=None,
        rival_thesis_ids=(),
        created_at=NOW,
        updated_at=NOW,
        archived_at=None,
    )
    plan = TradePlan(
        plan_id=plan_id,
        version=1,
        subject_id=subject_id,
        thesis_id=thesis_id,
        instrument_id="equity:US:NVDA",
        status=TradePlanStatus.DRAFT,
        valid_from=NOW - timedelta(days=1),
        valid_until=None,
        currency="USD",
        reference_price=Decimal("100"),
        reference_price_at=NOW,
        target_position_percent=Decimal("5"),
        max_position_percent=Decimal("10"),
        risk_budget_percent=Decimal("1"),
        stop_price=Decimal("90"),
        conditions=(),
        notes="Fixture plan",
        confirmed_by="user",
        created_at=NOW,
        idempotency_key="phase4a-plan-fixture",  # gitleaks:allow - synthetic fixture
    )
    with factory() as uow:
        uow.subjects.add(subject)
        uow.theses.add(thesis)
        uow.trade_plans.append(plan)
        uow.commit()

    yield DecisionRecordService(factory, clock, ids, redactor), factory, subject_id, plan_id
    engine.dispose()


def _append_kwargs(subject_id: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "subject_id": subject_id,
        "decision_type": DecisionType.NO_ACTION,
        "title": "Wait for confirmation",
        "rationale": "No new structure; wait for the planned condition.",
        "decided_at": NOW,
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "primary_instrument_id": None,
        "thesis_revision_ids": (),
        "evidence_ids": (),
        "report_ids": (),
        "supersedes_decision_id": None,
        "position_context_snapshot_id": None,
        "idempotency_key": "phase4a-decision",
    }
    value.update(overrides)
    return value


def test_structured_decision_roundtrip_and_plan_scope(harness) -> None:  # type: ignore[no-untyped-def]
    service, factory, subject_id, plan_id = harness
    due = NOW + timedelta(days=2)
    result = service.append(
        **_append_kwargs(
            subject_id,
            strategy_code="strategy_v1",
            strategy_version="1",
            scenario=DecisionScenario.SIDEWAYS,
            trade_plan_id=plan_id,
            trade_plan_version=1,
            review_due_at=due,
        )
    )
    assert result.ok and result.data is not None
    assert result.data.strategy_code == "strategy_v1"
    assert result.data.strategy_version == "1"
    assert result.data.scenario == DecisionScenario.SIDEWAYS
    assert result.data.trade_plan_id == plan_id
    assert result.data.trade_plan_version == 1
    assert result.data.review_due_at == due

    with factory() as uow:
        stored = uow.decisions.get(result.data.decision_id)
    assert stored.strategy_code == "strategy_v1"
    assert stored.scenario is DecisionScenario.SIDEWAYS
    assert stored.trade_plan_id == plan_id
    assert stored.review_due_at == due


def test_structured_fields_participate_in_idempotency(harness) -> None:  # type: ignore[no-untyped-def]
    service, _factory, subject_id, _plan_id = harness
    first = service.append(
        **_append_kwargs(
            subject_id,
            strategy_code="strategy_v1",
            strategy_version="1",
            scenario=DecisionScenario.SIDEWAYS,
        )
    )
    assert first.ok
    conflict = service.append(
        **_append_kwargs(
            subject_id,
            strategy_code="strategy_v1",
            strategy_version="1",
            scenario=DecisionScenario.PULLBACK,
        )
    )
    assert not conflict.ok
    assert conflict.errors[0].code == "DUPLICATE_IDEMPOTENCY_KEY"


def test_normal_no_action_and_strict_intent_are_accepted(harness) -> None:  # type: ignore[no-untyped-def]
    service, _factory, subject_id, _plan_id = harness
    normal = service.append(**_append_kwargs(subject_id, idempotency_key="normal-no-action"))
    assert normal.ok
    strict = service.append(
        **_append_kwargs(
            subject_id,
            decision_type=DecisionType.INITIATE_INTENT,
            confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            idempotency_key="strict-intent",
        )
    )
    assert strict.ok


def test_invalid_scenario_and_plan_pair_fail_at_schema_boundary() -> None:
    base = {
        "case_id": "case_00000000-0000-7000-8000-000000000001",
        "decision_type": DecisionType.NO_ACTION,
        "title": "Wait",
        "rationale": "Wait",
        "decided_at": NOW,
        "decided_by": "user",
        "confirmation_mode": ConfirmationMode.NORMAL,
        "idempotency_key": "schema-decision",
    }
    with pytest.raises(ValueError):
        DecisionRecordAppendInput.model_validate({**base, "scenario": "UNKNOWN"})
    with pytest.raises(ValueError):
        DecisionRecordAppendInput.model_validate(
            {**base, "trade_plan_id": "trade_plan_00000000-0000-7000-8000-000000000001"}
        )
    with pytest.raises(ValueError):
        DecisionRecordAppendInput.model_validate(
            {**base, "review_due_at": datetime(2026, 8, 21, 12)}
        )


def test_plan_from_another_subject_fails_closed(harness) -> None:  # type: ignore[no-untyped-def]
    service, _factory, _subject_id, plan_id = harness
    other_subject_id = "case_00000000-0000-7000-8000-000000000999"
    # The domain service reaches the versioned plan repository before writing;
    # use a missing Subject only to prove that an unrelated plan is not guessed.
    result = service.append(
        **_append_kwargs(
            other_subject_id,
            trade_plan_id=plan_id,
            trade_plan_version=1,
            idempotency_key="cross-subject-plan",
        )
    )
    assert not result.ok
    assert result.errors[0].code in {"INVALID_RESEARCH_LINK", "INVESTMENT_CASE_NOT_FOUND"}
