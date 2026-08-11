"""Focused Phase 3D Trade Plan lifecycle acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.dto.monitoring import (
    MonitorArchiveInput,
    MonitorCreateInput,
    MonitorRuleInput,
)
from application.dto.research import (
    SubjectUpdateCandidatePayload,
    ThesisRevisionCandidatePayload,
    TradePlanCandidatePayload,
    TradePlanConditionPayload,
)
from application.services.monitor_service import MonitorService
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
    ThesisStatus,
)
from domain.common.errors import ResearchStateConflict
from domain.monitoring.enums import MonitorRuleType
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
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
def services(orm_sqlite_url: str):  # type: ignore[no-untyped-def]
    engine = create_engine(orm_sqlite_url)
    _enable_fk(engine)
    clock = FixedClock(NOW)
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    yield (
        ResearchSubjectService(factory, clock, ids, redactor),
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


def _create_subject(subjects: ResearchSubjectService, key: str) -> str:
    created = subjects.create_subject(
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA",
        summary="Phase 3D invariant fixture",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key=key,
    )
    assert created.ok and created.data is not None
    return created.data.subject_id


def _activate_case(
    revisions: ThesisRevisionService,
    subject_id: str,
    key: str,
) -> None:
    proposed = revisions.propose_state_update(
        subject_id=subject_id,
        payload=SubjectUpdateCandidatePayload(
            action="update",
            new_status=ResearchSubjectStatus.ACTIVE,
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Activate the Case for live judgment.",
        idempotency_key=key,
    )
    assert proposed.ok and proposed.data is not None
    confirmed = revisions.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
    )
    assert confirmed.ok


def _confirm_thesis(
    revisions: ThesisRevisionService,
    subject_id: str,
    *,
    key: str,
    status: ThesisStatus | None = None,
) -> str:
    payload = _thesis_payload()
    if status is not None:
        payload = payload.model_copy(update={"thesis_status": status})
    proposed = revisions.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=payload,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Create a test Thesis.",
        idempotency_key=key,
    )
    assert proposed.ok and proposed.data is not None
    confirmed = revisions.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
    )
    assert confirmed.ok and confirmed.data is not None
    state = confirmed.data.research_state
    assert state is not None
    return state.theses[0].thesis_id


def test_trade_plan_propose_confirm_version_pause_and_archive(services) -> None:  # type: ignore[no-untyped-def]
    subjects, revisions, factory, engine, clock, ids = services
    created = subjects.create_subject(
        subject_type=ResearchSubjectType.COMPANY,
        title="NVDA",
        summary="Phase 3D lifecycle fixture",
        primary_instrument_id="equity:US:NVDA",
        topic_tags=(),
        linked_subject_ids=(),
        confirmed_by="user",
        idempotency_key="phase3d-case",
    )
    assert created.ok and created.data is not None
    subject_id = created.data.subject_id

    activate_case = revisions.propose_state_update(
        subject_id=subject_id,
        payload=SubjectUpdateCandidatePayload(
            action="update",
            new_status=ResearchSubjectStatus.ACTIVE,
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Activate the Case before confirming live judgment.",
        idempotency_key="phase3d-case-activate",
    )
    assert activate_case.ok and activate_case.data is not None
    activated = revisions.confirm_candidate(
        activate_case.data.candidate_id,
        reviewed_by="user",
    )
    assert activated.ok and activated.data is not None

    thesis_candidate = revisions.propose_revision(
        subject_id=subject_id,
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
        subject_id=subject_id,
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

    monitor_repository = SqlAlchemyMonitorRepository(engine)
    monitor_service = MonitorService(monitor_repository, factory, clock, ids)
    monitor = monitor_service.create(
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

    for version, status in ((1, TradePlanStatus.PAUSED),):
        update = revisions.propose_state_update(
            subject_id=subject_id,
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

    archive_attempt = revisions.propose_state_update(
        subject_id=subject_id,
        payload=_plan_payload(
            thesis_id,
            status=TradePlanStatus.ARCHIVED,
            plan_id=plan.plan_id,
            expected_version=2,
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Attempt retirement while a linked Monitor remains live.",
        idempotency_key="phase3d-plan-archive-blocked",
    )
    assert archive_attempt.ok and archive_attempt.data is not None
    blocked = revisions.confirm_candidate(
        archive_attempt.data.candidate_id, reviewed_by="external_agent"
    )
    assert not blocked.ok
    assert blocked.errors[0].code == "RESEARCH_STATE_CONFLICT"
    assert blocked.errors[0].details["live_monitor_ids"] == [monitor.monitor.monitor_id]

    monitor_service.archive(
        MonitorArchiveInput(
            monitor_id=monitor.monitor.monitor_id,
            expected_version=monitor.monitor.version,
            confirmed_by="user",
            idempotency_key="phase3d-monitor-archive",
        )
    )
    archive_retry = revisions.propose_state_update(
        subject_id=subject_id,
        payload=_plan_payload(
            thesis_id,
            status=TradePlanStatus.ARCHIVED,
            plan_id=plan.plan_id,
            expected_version=2,
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Retire after the linked Monitor is archived.",
        idempotency_key="phase3d-plan-archive-after-monitor",
    )
    assert archive_retry.ok and archive_retry.data is not None
    archived = revisions.confirm_candidate(
        archive_retry.data.candidate_id, reviewed_by="external_agent"
    )
    assert archived.ok and archived.data is not None
    assert archived.data.research_state is not None
    plan = archived.data.research_state.current_trade_plan
    assert plan is not None and plan.status is TradePlanStatus.ARCHIVED

    with factory() as uow:
        versions = uow.trade_plans.list_versions(plan.plan_id)
        assert [item.status for item in versions] == [
            TradePlanStatus.ACTIVE,
            TradePlanStatus.PAUSED,
            TradePlanStatus.ARCHIVED,
        ]


def test_live_thesis_confirmation_rejected_until_case_is_active(services) -> None:  # type: ignore[no-untyped-def]
    subjects, revisions, factory, *_ = services
    subject_id = _create_subject(subjects, "phase3d-draft-thesis-case")
    proposed = revisions.propose_revision(
        subject_id=subject_id,
        thesis_id=None,
        payload=_thesis_payload(),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Attempt a live Thesis in a Draft Case.",
        idempotency_key="phase3d-draft-thesis",
    )
    assert proposed.ok and proposed.data is not None

    confirmed = revisions.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
    )

    assert not confirmed.ok
    assert confirmed.errors[0].code == "RESEARCH_STATE_CONFLICT"
    assert confirmed.errors[0].details == {
        "subject_id": subject_id,
        "subject_status": "draft",
        "attempted_child_status": "active",
    }
    with factory() as uow:
        assert uow.theses.list_by_subject(subject_id) == ()


def test_monitor_requires_active_subject_and_blocks_subject_retirement(services) -> None:  # type: ignore[no-untyped-def]
    subjects, revisions, factory, engine, clock, ids = services
    subject_id = _create_subject(subjects, "phase3d-monitor-subject")
    repository = SqlAlchemyMonitorRepository(engine)
    service = MonitorService(repository, factory, clock, ids)
    request = MonitorCreateInput(
        name="NVDA subject lifecycle",
        subject_id=subject_id,
        primary_instrument_id="equity:US:NVDA",
        rules=(
            MonitorRuleInput(
                rule_code="NVDA_FLOOR",
                description="NVDA falls below the reviewed floor.",
                rule_type=MonitorRuleType.PRICE_BELOW,
                instrument_id="equity:US:NVDA",
                price_threshold=Decimal("100"),
            ),
        ),
        confirmed_by="user",
        idempotency_key="phase3d-draft-monitor",
    )

    with pytest.raises(ResearchStateConflict):
        service.create(request)

    _activate_case(revisions, subject_id, "phase3d-monitor-subject-activate")
    monitor = service.create(request)
    archive = revisions.propose_state_update(
        subject_id=subject_id,
        payload=SubjectUpdateCandidatePayload(
            action="archive",
            new_status=ResearchSubjectStatus.ARCHIVED,
            archived_reason="Lifecycle test",
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Attempt retirement while Monitor is live.",
        idempotency_key="phase3d-monitor-subject-archive",
    )
    assert archive.ok and archive.data is not None
    blocked = revisions.confirm_candidate(archive.data.candidate_id, reviewed_by="user")
    assert not blocked.ok
    assert blocked.errors[0].code == "RESEARCH_STATE_CONFLICT"
    assert blocked.errors[0].details["live_monitor_ids"] == [monitor.monitor.monitor_id]


def test_active_trade_plan_requires_live_thesis(services) -> None:  # type: ignore[no-untyped-def]
    subjects, revisions, factory, *_ = services
    subject_id = _create_subject(subjects, "phase3d-draft-plan-case")
    _activate_case(revisions, subject_id, "phase3d-draft-plan-case-activate")
    thesis_id = _confirm_thesis(
        revisions,
        subject_id,
        key="phase3d-draft-thesis-confirmed",
        status=ThesisStatus.DRAFT,
    )

    proposed = revisions.propose_state_update(
        subject_id=subject_id,
        payload=_plan_payload(thesis_id, status=TradePlanStatus.ACTIVE),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Attempt an ACTIVE plan against a Draft Thesis.",
        idempotency_key="phase3d-active-plan-draft-thesis",
    )
    assert proposed.ok and proposed.data is not None
    confirmed = revisions.confirm_candidate(
        proposed.data.candidate_id,
        reviewed_by="user",
    )

    assert not confirmed.ok
    assert confirmed.errors[0].code == "RESEARCH_STATE_CONFLICT"
    assert confirmed.errors[0].details["thesis_id"] == thesis_id
    assert confirmed.errors[0].details["thesis_status"] == ThesisStatus.DRAFT.value
    with factory() as uow:
        assert uow.trade_plans.get_current_by_subject(subject_id) is None


def test_direct_archive_rejected_while_live_thesis_remains(services) -> None:  # type: ignore[no-untyped-def]
    subjects, revisions, factory, *_ = services
    subject_id = _create_subject(subjects, "phase3d-archive-case")
    _activate_case(revisions, subject_id, "phase3d-archive-case-activate")
    _confirm_thesis(revisions, subject_id, key="phase3d-archive-live-thesis")

    archived = subjects.archive_subject(
        subject_id,
        archived_reason="close the case",
        reviewed_by="user",
        idempotency_key="phase3d-archive-live-case",
    )

    assert not archived.ok
    assert archived.errors[0].code == "RESEARCH_STATE_CONFLICT"
    with factory() as uow:
        assert uow.subjects.get(subject_id).status is ResearchSubjectStatus.ACTIVE


def test_live_plan_blocks_thesis_retirement_until_plan_archived(services) -> None:  # type: ignore[no-untyped-def]
    subjects, revisions, factory, *_ = services
    subject_id = _create_subject(subjects, "phase3d-retirement-case")
    _activate_case(revisions, subject_id, "phase3d-retirement-case-activate")
    thesis_id = _confirm_thesis(revisions, subject_id, key="phase3d-retirement-thesis")

    retirement_payload = _thesis_payload().model_copy(
        update={
            "replaces_revision_no": 1,
            "thesis_status": ThesisStatus.ARCHIVED,
        }
    )
    normal_retirement = revisions.propose_revision(
        subject_id=subject_id,
        thesis_id=thesis_id,
        payload=retirement_payload,
        confirmation_mode=ConfirmationMode.NORMAL,
        proposed_by="codex",
        proposed_by_rationale="Retire the Thesis after review.",
        idempotency_key="phase3d-retirement-normal",  # gitleaks:allow
    )
    assert normal_retirement.ok and normal_retirement.data is not None
    normal_landed = revisions.confirm_candidate(
        normal_retirement.data.candidate_id,
        reviewed_by="user",
    )
    assert not normal_landed.ok
    assert normal_landed.errors[0].code == "STRICT_REVIEW_REQUIRED"

    active_plan = revisions.propose_state_update(
        subject_id=subject_id,
        payload=_plan_payload(thesis_id, status=TradePlanStatus.ACTIVE),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Create the live plan before retiring the Thesis.",
        idempotency_key="phase3d-retirement-plan",  # gitleaks:allow
    )
    assert active_plan.ok and active_plan.data is not None
    active_plan_landed = revisions.confirm_candidate(
        active_plan.data.candidate_id,
        reviewed_by="user",
    )
    assert active_plan_landed.ok and active_plan_landed.data is not None
    state = active_plan_landed.data.research_state
    assert state is not None and state.current_trade_plan is not None
    plan = state.current_trade_plan

    strict_retirement = revisions.propose_revision(
        subject_id=subject_id,
        thesis_id=thesis_id,
        payload=retirement_payload,
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Retire the Thesis after the live plan is closed.",
        idempotency_key="phase3d-retirement-strict",  # gitleaks:allow
    )
    assert strict_retirement.ok and strict_retirement.data is not None
    blocked = revisions.confirm_candidate(
        strict_retirement.data.candidate_id,
        reviewed_by="user",
    )
    assert not blocked.ok
    assert blocked.errors[0].code == "RESEARCH_STATE_CONFLICT"
    assert blocked.errors[0].details["live_trade_plan_id"] == plan.plan_id

    archive_plan = revisions.propose_state_update(
        subject_id=subject_id,
        payload=_plan_payload(
            thesis_id,
            status=TradePlanStatus.ARCHIVED,
            plan_id=plan.plan_id,
            expected_version=plan.version,
        ),
        confirmation_mode=ConfirmationMode.STRICT_REVIEW,
        proposed_by="codex",
        proposed_by_rationale="Explicitly archive the live plan first.",
        idempotency_key="phase3d-retirement-plan-archive",
    )
    assert archive_plan.ok and archive_plan.data is not None
    archived_plan = revisions.confirm_candidate(
        archive_plan.data.candidate_id,
        reviewed_by="user",
    )
    assert archived_plan.ok

    retired = revisions.confirm_candidate(
        strict_retirement.data.candidate_id,
        reviewed_by="user",
    )
    assert retired.ok
    with factory() as uow:
        thesis = uow.theses.get(thesis_id)
        assert thesis.status is ThesisStatus.ARCHIVED
        assert thesis.archived_at is not None
        assert uow.trade_plans.get_current_by_subject(subject_id).status is TradePlanStatus.ARCHIVED  # type: ignore[union-attr]

    archived_case = subjects.archive_subject(
        subject_id,
        archived_reason="Thesis and plan explicitly retired",
        reviewed_by="user",
        idempotency_key="phase3d-retirement-case-archive",
    )
    assert archived_case.ok
    assert archived_case.data is not None
    assert archived_case.data.status in {ResearchSubjectStatus.ARCHIVED, "archived"}
