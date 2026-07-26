"""Focused Phase 3D Trade Plan lifecycle acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.dto.monitoring import MonitorCreateInput
from application.dto.research import (
    ThesisRevisionCandidatePayload,
    TradePlanCandidatePayload,
    TradePlanConditionPayload,
)
from application.services.investment_case_service import InvestmentCaseService
from application.services.monitor_service import MonitorService
from application.services.thesis_revision_service import ThesisRevisionService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    ConfidenceBand,
    ConfirmationMode,
    InvestmentCaseType,
    InvestmentRating,
    ThesisRole,
)
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def services(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path / 'phase3d.db'}")
    _enable_fk(engine)
    Base.metadata.create_all(engine)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    yield (
        InvestmentCaseService(factory, clock, ids, redactor),
        ThesisRevisionService(factory, clock, ids, redactor),
        factory,
        engine,
        clock,
        ids,
    )
    engine.dispose()


def _thesis_payload() -> ThesisRevisionCandidatePayload:
    return ThesisRevisionCandidatePayload(
        kind="thesis_revision",
        title="Primary",
        statement="Cash flow compounds while valuation remains acceptable.",
        rationale="Confirmed research basis for a controlled plan.",
        confidence_band=ConfidenceBand.MEDIUM,
        rating=InvestmentRating.BUY,
        invalidation_check_note="Revisit if the operating thesis breaks.",
        thesis_role=ThesisRole.PRIMARY,
    )


def _plan_payload(
    thesis_id: str,
    *,
    status: TradePlanStatus,
    plan_id: str | None = None,
    expected_version: int | None = None,
) -> TradePlanCandidatePayload:
    return TradePlanCandidatePayload(
        plan_id=plan_id,
        expected_version=expected_version,
        thesis_id=thesis_id,
        instrument_id="equity:US:NVDA",
        status=status,
        valid_from=NOW - timedelta(minutes=1),
        valid_until=NOW + timedelta(days=7),
        currency="USD",
        reference_price=Decimal("100"),
        reference_price_at=NOW,
        target_position_percent=Decimal("8"),
        max_position_percent=Decimal("12"),
        risk_budget_percent=Decimal("1"),
        stop_price=Decimal("90"),
        conditions=(
            TradePlanConditionPayload(
                condition_code="PRICE_ENTRY",
                phase=TradePlanConditionPhase.ENTRY,
                mode=TradePlanConditionMode.MONITORABLE,
                description="Price confirms the entry range.",
                severity="MEDIUM",
                fact_type=TradePlanFactType.PRICE,
                metric_key="last",
                comparator=TradePlanComparator.LTE,
                threshold=Decimal("100"),
                unit="USD",
                instrument_id="equity:US:NVDA",
                max_fact_age_seconds=7200,
            ),
            TradePlanConditionPayload(
                condition_code="MANUAL_REVIEW",
                phase=TradePlanConditionPhase.REVIEW,
                mode=TradePlanConditionMode.MANUAL,
                description="Review management guidance qualitatively.",
                severity="INFO",
            ),
        ),
        notes="Research plan only; no execution authority.",
    )


def test_trade_plan_propose_confirm_version_pause_and_archive(services) -> None:  # type: ignore[no-untyped-def]
    cases, revisions, factory, engine, clock, ids = services
    created = cases.create_case(
        case_type=InvestmentCaseType.COMPANY,
        title="NVDA",
        summary="Phase 3D lifecycle fixture",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=(),
        linked_case_ids=(),
        confirmed_by="user",
        idempotency_key="phase3d-case",
    )
    assert created.ok and created.data is not None
    case_id = created.data.case_id

    thesis_candidate = revisions.propose_revision(
        case_id=case_id,
        thesis_id=None,
        payload=_thesis_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Create the confirmed judgment anchor.",
        idempotency_key="phase3d-thesis",
    )
    assert thesis_candidate.ok and thesis_candidate.data is not None
    thesis_confirmed = revisions.confirm_candidate(
        thesis_candidate.data.candidate_id, reviewed_by="user"
    )
    assert thesis_confirmed.ok and thesis_confirmed.data is not None
    assert thesis_confirmed.data.research_state is not None
    thesis_id = thesis_confirmed.data.research_state.theses[0].thesis_id

    proposed = revisions.propose_state_update(
        case_id=case_id,
        payload=_plan_payload(thesis_id, status=TradePlanStatus.ACTIVE),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Translate the judgment into reviewable controls.",
        idempotency_key="test",
    )
    assert proposed.ok and proposed.data is not None
    forbidden = revisions.confirm_candidate(
        proposed.data.candidate_id, reviewed_by="codex"
    )
    assert not forbidden.ok
    assert forbidden.errors[0].code == "UNAUTHORIZED_REVIEWER"

    confirmed = revisions.confirm_candidate(
        proposed.data.candidate_id, reviewed_by="user"
    )
    assert confirmed.ok and confirmed.data is not None
    assert confirmed.data.affected_entity_type == "trade_plan"
    assert confirmed.data.research_state is not None
    plan = confirmed.data.research_state.current_trade_plan
    assert plan is not None
    assert plan.version == 1
    assert plan.status is TradePlanStatus.ACTIVE
    assert plan.execution_effect is False

    monitor = MonitorService(
        SqlAlchemyMonitorRepository(engine), factory, clock, ids
    ).create(
        MonitorCreateInput(
            name="NVDA plan controls",
            trade_plan_id=plan.plan_id,
            trade_plan_version=plan.version,
            compile_trade_plan_conditions=True,
            confirmed_by="user",
            idempotency_key="phase3d-monitor-v1",
        )
    )
    assert monitor.monitor.trade_plan_id == plan.plan_id
    assert monitor.monitor.trade_plan_version == 1
    assert monitor.monitor.valid_until == NOW + timedelta(days=7)
    assert [rule.rule_code for rule in monitor.monitor.rules] == ["PRICE_ENTRY"]

    for version, status in ((1, TradePlanStatus.PAUSED), (2, TradePlanStatus.ARCHIVED)):
        update = revisions.propose_state_update(
            case_id=case_id,
            payload=_plan_payload(
                thesis_id,
                status=status,
                plan_id=plan.plan_id,
                expected_version=version,
            ),
            confirmation_mode=ConfirmationMode.STRICT_REVIEW,
            proposed_by="codex",
            proposed_by_rationale=f"Move plan to {status.value} after review.",
            idempotency_key=f"phase3d-plan-v{version + 1}",
        )
        assert update.ok and update.data is not None
        landed = revisions.confirm_candidate(
            update.data.candidate_id, reviewed_by="external_agent"
        )
        assert landed.ok and landed.data is not None
        assert landed.data.research_state is not None
        plan = landed.data.research_state.current_trade_plan
        assert plan is not None
        assert plan.version == version + 1
        assert plan.status is status

    with factory() as uow:
        versions = uow.trade_plans.list_versions(plan.plan_id)
        assert [item.status for item in versions] == [
            TradePlanStatus.ACTIVE,
            TradePlanStatus.PAUSED,
            TradePlanStatus.ARCHIVED,
        ]
