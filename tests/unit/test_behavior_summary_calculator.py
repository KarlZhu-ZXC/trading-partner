"""Focused Phase 4D behavior facts and denominator tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from application.dto.behavior import BehaviorSummaryDTO, BehaviorSummaryQueryInput
from application.services.behavior_summary_calculator import BehaviorSummaryCalculator
from domain.behavior.enums import BehaviorMetricAvailability
from domain.common.enums import ConfirmationMode, DecisionScenario, DecisionType, VendorId
from domain.portfolio.enums import (
    ActivityAnnotationStatus,
    TradeCycleClassification,
    TradeCycleQuality,
    TradeCycleStatus,
)
from domain.portfolio.models import ActivityAnnotation, TradeCycle
from domain.research.memory_models import DecisionRecord
from domain.retro.enums import TradeRetroSeverity
from domain.retro.models import TradeRetroFinding

T0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
INSTRUMENT = "equity:US:ABC"
SUBJECT = "case_00000000-0000-7000-8000-000000000001"
PLAN = "trade_plan_00000000-0000-7000-8000-000000000001"


def _cycle(
    cycle_id: str,
    pnl: str | None,
    *,
    status: TradeCycleStatus = TradeCycleStatus.CLOSED,
    classification: TradeCycleClassification = TradeCycleClassification.UNCLASSIFIED,
    opened_at: datetime = T0,
    closed_at: datetime | None = T0 + timedelta(days=1),
    attempts: int = 1,
    reentry_of_cycle_id: str | None = None,
    currency: str = "USD",
) -> TradeCycle:
    return TradeCycle(
        cycle_id=cycle_id,
        account_ref="account_a",
        provider=VendorId.SCHWAB,
        instrument_id=INSTRUMENT,
        currency=currency,
        activity_ids=(f"{cycle_id}_activity",),
        opened_at=opened_at,
        closed_at=closed_at,
        status=status,
        classification=classification,
        opening_count=1,
        add_count=max(0, attempts - 1),
        reduce_count=1,
        ending_quantity=Decimal(0),
        gross_realized_pnl=Decimal(pnl) if pnl is not None else None,
        net_realized_pnl=Decimal(pnl) if pnl is not None else None,
        maximum_deployed_capital=Decimal("100"),
        holding_duration_seconds=(
            int((closed_at - opened_at).total_seconds())
            if closed_at is not None
            else None
        ),
        reentry_of_cycle_id=reentry_of_cycle_id,
        quality=TradeCycleQuality.COMPLETE,
        warning_codes=(),
    )


def _decision(
    decision_id: str,
    *,
    decision_type: DecisionType = DecisionType.INITIATE_INTENT,
    decided_at: datetime = T0 - timedelta(hours=1),
    strategy_code: str | None = "strategy_v1",
    scenario: DecisionScenario | None = DecisionScenario.UPSIDE,
    trade_plan_id: str | None = PLAN,
    trade_plan_version: int | None = 1,
    review_due_at: datetime | None = None,
    supersedes_decision_id: str | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        subject_id=SUBJECT,
        decision_type=decision_type,
        title="Behavior fixture",
        rationale="Durable behavior fixture",
        decided_at=decided_at,
        recorded_at=max(T0 + timedelta(days=5), decided_at),
        decided_by="user",
        confirmation_mode=(
            ConfirmationMode.NORMAL
            if decision_type
            in {DecisionType.NO_ACTION, DecisionType.WATCH, DecisionType.RESEARCH_MORE}
            else ConfirmationMode.STRICT_REVIEW
        ),
        primary_instrument_id=INSTRUMENT,
        thesis_revision_ids=(),
        evidence_ids=(),
        report_ids=(),
        supersedes_decision_id=supersedes_decision_id,
        position_context_snapshot_id=None,
        schema_version=1,
        strategy_code=strategy_code,
        strategy_version="1",
        scenario=scenario,
        trade_plan_id=trade_plan_id,
        trade_plan_version=trade_plan_version,
        review_due_at=review_due_at,
    )


def test_win_denominator_excludes_open_unresolved_missing_pnl_and_sgov() -> None:
    cycles = (
        _cycle("win", "5"),
        _cycle("loss", "-2"),
        _cycle("flat", "0"),
        _cycle("missing", None),
        _cycle("open", "9", status=TradeCycleStatus.OPEN, closed_at=None),
        _cycle("unresolved", None, status=TradeCycleStatus.UNRESOLVED),
        _cycle(
            "sgov",
            "100",
            classification=TradeCycleClassification.CASH_MANAGEMENT,
        ),
    )

    result = BehaviorSummaryCalculator().calculate(cycles, ())

    assert result.algorithm_version == "behavior_summary_v1"
    assert result.closed_active_trade_cycles.numerator == 4
    assert result.closed_active_trade_cycles.denominator == 7
    assert result.wins.numerator == 1
    assert result.losses.numerator == 1
    assert result.flat.numerator == 1
    assert result.win_rate.numerator == 1
    assert result.win_rate.denominator == 3
    assert result.win_rate.value == Decimal(1) / Decimal(3)
    assert "OPEN_CYCLE" in result.win_rate.exclusion_reasons
    assert "UNRESOLVED_CYCLE" in result.win_rate.exclusion_reasons
    assert "CASH_MANAGEMENT" in result.win_rate.exclusion_reasons
    assert "NET_PNL_UNAVAILABLE" in result.win_rate.exclusion_reasons
    assert "sgov" in result.win_rate.excluded_cycle_ids
    assert result.avg_win.value == Decimal("5")
    assert result.avg_loss.value == Decimal("-2")
    assert result.payoff_ratio.value == Decimal("2.5")
    assert result.average_holding_duration.value == Decimal(86400)
    assert result.median_holding_duration.value == Decimal(86400)
    assert result.entry_attempt_count.value == Decimal(1)
    assert result.turnover.availability is BehaviorMetricAvailability.NOT_SUPPORTED
    assert result.turnover.numerator is None
    assert "TURNOVER_NOT_SUPPORTED_NO_TRADE_NOTIONAL_FACT" in result.turnover.exclusion_reasons
    assert result.invalidation_adherence.availability is BehaviorMetricAvailability.NOT_SUPPORTED


def test_payoff_ratio_uses_average_win_and_average_loss() -> None:
    cycles = (
        _cycle("win-one", "5"),
        _cycle("win-two", "7"),
        _cycle("loss", "-2"),
    )

    result = BehaviorSummaryCalculator().calculate(
        cycles,
        (),
        minimum_sample_size=1,
    )

    assert result.avg_win.value == Decimal("6")
    assert result.avg_loss.value == Decimal("-2")
    assert result.payoff_ratio.value == Decimal("3")
    assert result.payoff_ratio.note == (
        "Payoff ratio is average winning-cycle P/L divided by absolute average "
        "losing-cycle P/L."
    )


def test_plan_decision_invalidation_proxy_and_scenario_distribution() -> None:
    cycle = _cycle("planned", "2")
    decision = _decision("decision_00000000-0000-7000-8000-000000000001")
    finding = TradeRetroFinding(
        code="MISSING_INVALIDATION",
        severity=TradeRetroSeverity.MEDIUM,
        title="Missing invalidation",
        detail="Fixture finding",
        instrument_id=INSTRUMENT,
        transaction_ids=cycle.activity_ids,
        plan_id=PLAN,
    )

    result = BehaviorSummaryCalculator().calculate(
        (cycle,),
        (decision,),
        (finding,),
        cycle_decision_links={cycle.cycle_id: (decision.decision_id,)},
    )

    assert result.plan_coverage.numerator == 1
    assert result.pre_fill_decision_coverage.numerator == 1
    assert result.pre_fill_invalidation_proxy.numerator == 0
    assert "MISSING_INVALIDATION" not in result.pre_fill_invalidation_proxy.exclusion_reasons
    assert result.pre_fill_invalidation_proxy.denominator == 1
    distribution = result.metric("scenario_action:UPSIDE:initiate_intent")
    assert distribution.numerator == 1
    assert distribution.decision_ids == (decision.decision_id,)


def test_reentry_third_attempt_no_new_plan_and_no_action_review() -> None:
    parent = _cycle("parent", "1", opened_at=T0, closed_at=T0 + timedelta(hours=2))
    child_open = T0 + timedelta(hours=3)
    child = _cycle(
        "child",
        "-1",
        opened_at=child_open,
        closed_at=child_open + timedelta(hours=1),
        attempts=3,
        reentry_of_cycle_id=parent.cycle_id,
    )
    plan_decision = _decision(
        "decision_00000000-0000-7000-8000-000000000002",
        decided_at=T0 + timedelta(hours=2, minutes=30),
    )
    no_action = _decision(
        "decision_00000000-0000-7000-8000-000000000003",
        decision_type=DecisionType.NO_ACTION,
        decided_at=T0 - timedelta(hours=2),
        trade_plan_id=None,
        trade_plan_version=None,
        review_due_at=T0 + timedelta(days=1),
        scenario=DecisionScenario.SIDEWAYS,
    )
    superseder = _decision(
        "decision_00000000-0000-7000-8000-000000000004",
        decided_at=T0 + timedelta(days=2),
        supersedes_decision_id=no_action.decision_id,
    )

    result = BehaviorSummaryCalculator().calculate(
        (child, parent),
        (superseder, no_action, plan_decision),
        cycle_decision_links={
            child.cycle_id: (plan_decision.decision_id,),
            parent.cycle_id: (),
        },
    )

    assert result.same_day_reentry.numerator == 1
    assert result.same_day_reentry.cycle_ids == (child.cycle_id,)
    assert result.third_attempt_without_new_plan.numerator == 1
    assert result.third_attempt_without_new_plan.denominator == 1
    assert result.no_action_count.numerator == 1
    assert result.no_action_review_completion.numerator == 1
    assert result.no_action_review_completion.denominator == 1
    assert result.no_action_review_completion.value == Decimal(1)


def test_empty_sample_is_factful_and_derived_values_are_null_and_deterministic() -> None:
    calculator = BehaviorSummaryCalculator(minimum_sample_size=2)
    first = calculator.calculate((), ())
    second = calculator.calculate((), ())

    assert first == second
    assert first.win_rate.numerator == 0
    assert first.win_rate.denominator == 0
    assert first.win_rate.value is None
    assert first.avg_win.value is None
    assert first.payoff_ratio.value is None
    assert first.no_action_count.value == 0
    dto = BehaviorSummaryDTO.from_domain(first)
    assert dto.algorithm_version == "behavior_summary_v1"
    assert dto.execution_effect is False


def test_strategy_and_instrument_cohort_filters_are_explicit() -> None:
    cycle = _cycle("cohort", "1")
    matching = _decision("decision_00000000-0000-7000-8000-000000000005")
    nonmatching = _decision(
        "decision_00000000-0000-7000-8000-000000000006",
        strategy_code="other_strategy",
    )

    result = BehaviorSummaryCalculator().calculate(
        (cycle,),
        (matching, nonmatching),
        strategy_code="strategy_v1",
        strategy_version="1",
        instrument_id=INSTRUMENT,
        cycle_decision_links={cycle.cycle_id: (matching.decision_id,)},
    )

    assert result.cohort.strategy_code == "strategy_v1"
    assert result.cohort.instrument_ids == (INSTRUMENT,)
    assert result.closed_active_trade_cycles.numerator == 1
    assert result.no_action_count.denominator == 1


def test_date_window_filters_cycle_and_decision_cohort_without_losing_prefill_links() -> None:
    before = _cycle(
        "before-window",
        "2",
        opened_at=T0 - timedelta(days=10),
        closed_at=T0 - timedelta(days=9),
    )
    inside = _cycle("inside-window", "3")
    prefill = _decision(
        "decision_00000000-0000-7000-8000-000000000020",
        decided_at=T0 - timedelta(hours=1),
    )
    in_window_no_action = _decision(
        "decision_00000000-0000-7000-8000-000000000021",
        decision_type=DecisionType.NO_ACTION,
        decided_at=T0 + timedelta(hours=1),
        trade_plan_id=None,
        trade_plan_version=None,
    )
    after_window = _decision(
        "decision_00000000-0000-7000-8000-000000000022",
        decision_type=DecisionType.NO_ACTION,
        decided_at=T0 + timedelta(days=3),
        trade_plan_id=None,
        trade_plan_version=None,
    )

    result = BehaviorSummaryCalculator().calculate(
        (before, inside),
        (prefill, in_window_no_action, after_window),
        cycle_decision_links={inside.cycle_id: (prefill.decision_id,)},
        start=T0,
        end=T0 + timedelta(days=2),
    )

    assert result.cohort.start == T0
    assert result.cohort.end == T0 + timedelta(days=2)
    assert result.cohort_cycle_ids == (inside.cycle_id,)
    assert result.cohort_excluded_cycle_ids == (before.cycle_id,)
    assert "COHORT_DATE_BEFORE_START" in result.cohort_exclusion_reasons
    assert result.pre_fill_decision_coverage.numerator == 1
    assert result.no_action_count.numerator == 1


def test_behavior_summary_query_rejects_naive_or_inverted_date_window() -> None:
    with pytest.raises(ValidationError):
        BehaviorSummaryQueryInput(start=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        BehaviorSummaryQueryInput(
            start=T0 + timedelta(days=1),
            end=T0,
        )


def test_missing_exact_links_never_uses_temporal_instrument_proximity() -> None:
    cycle = _cycle("unlinked", "3")
    decision = _decision("decision_00000000-0000-7000-8000-000000000007")

    result = BehaviorSummaryCalculator().calculate((cycle,), (decision,))

    assert result.plan_coverage.availability is BehaviorMetricAvailability.UNAVAILABLE
    assert (
        result.pre_fill_decision_coverage.availability
        is BehaviorMetricAvailability.UNAVAILABLE
    )
    assert (
        result.pre_fill_invalidation_proxy.availability
        is BehaviorMetricAvailability.UNAVAILABLE
    )
    assert result.plan_coverage.numerator is None
    assert result.plan_coverage.cycle_ids == ()
    assert result.plan_coverage.excluded_cycle_ids == (cycle.cycle_id,)
    assert result.scenario_action_distribution[1].cycle_ids == ()


def test_activity_annotations_are_converted_to_exact_cycle_links() -> None:
    cycle = _cycle("annotated", "3")
    decision = _decision("decision_00000000-0000-7000-8000-000000000008")
    annotation = ActivityAnnotation(
        annotation_id="activity_annotation_00000000-0000-7000-8000-000000000001",
        provider=VendorId.SCHWAB,
        account_ref=cycle.account_ref,
        provider_transaction_id=cycle.activity_ids[0],
        version=1,
        status=ActivityAnnotationStatus.LINKED_DECISION_PLAN,
        actor="user",
        authorization_note="Exact fixture link",
        idempotency_key="behavior-annotation-1",
        created_at=T0,
        decision_id=decision.decision_id,
        trade_plan_id=PLAN,
        trade_plan_version=1,
        subject_id=SUBJECT,
    )

    result = BehaviorSummaryCalculator().calculate(
        (cycle,),
        (decision,),
        activity_annotations=(annotation,),
    )

    assert result.plan_coverage.numerator == 1
    assert result.pre_fill_decision_coverage.numerator == 1


def test_horizon_cohort_without_horizon_fact_is_explicitly_unavailable() -> None:
    cycle = _cycle("horizon-unavailable", "1")
    decision = _decision("decision_00000000-0000-7000-8000-000000000009")

    result = BehaviorSummaryCalculator().calculate(
        (cycle,),
        (decision,),
        horizon="swing",
        cycle_decision_links={cycle.cycle_id: (decision.decision_id,)},
    )

    assert result.closed_active_trade_cycles.numerator == 0
    assert "COHORT_HORIZON_UNAVAILABLE" in result.cohort_exclusion_reasons
    assert result.cohort_excluded_cycle_ids == (cycle.cycle_id,)


def test_exact_link_after_fill_does_not_count_as_pre_fill_coverage() -> None:
    cycle = _cycle("post-fill-link", "1")
    decision = _decision(
        "decision_00000000-0000-7000-8000-000000000010",
        decided_at=cycle.closed_at + timedelta(minutes=1),
    )

    result = BehaviorSummaryCalculator().calculate(
        (cycle,),
        (decision,),
        cycle_decision_links={cycle.cycle_id: (decision.decision_id,)},
    )

    assert result.plan_coverage.numerator == 0
    assert result.pre_fill_decision_coverage.numerator == 0


def test_behavior_cohort_filters_exact_trade_classification() -> None:
    active = _cycle(
        "active", "5", classification=TradeCycleClassification.ACTIVE_TRADE
    )
    long_term = _cycle(
        "long-term", "7", classification=TradeCycleClassification.LONG_TERM_INVESTMENT
    )

    result = BehaviorSummaryCalculator().calculate(
        cycles=(active, long_term),
        decisions=(),
        classifications=(TradeCycleClassification.ACTIVE_TRADE,),
    )

    assert result.closed_active_trade_cycles.eligible_cycle_ids == ("active",)
    assert "long-term" in result.cohort_excluded_cycle_ids
    assert "COHORT_CLASSIFICATION_MISMATCH" in result.cohort_exclusion_reasons
