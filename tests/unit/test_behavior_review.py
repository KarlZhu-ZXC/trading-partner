"""Focused cross-period behavior action recurrence tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from domain.behavior_review import (
    BehaviorActionInput,
    BehaviorActionStatus,
    BehaviorReviewCohort,
    BehaviorReviewPeriodKind,
    BehaviorReviewRunCalculator,
)
from infrastructure.persistence.behavior_review_repository import (
    SqlAlchemyBehaviorReviewRepository,
)
from infrastructure.persistence.metadata import Base

T0 = datetime(2026, 8, 3, tzinfo=UTC)


def _cohort(start: datetime, *, cycle_ids: tuple[str, ...] = ("cycle_1",)) -> BehaviorReviewCohort:
    return BehaviorReviewCohort(
        period_kind=BehaviorReviewPeriodKind.WEEKLY,
        period_start=start,
        period_end=start + timedelta(days=7),
        strategy_code="strategy_v1",
        strategy_version="1",
        horizon="swing",
        instrument_ids=("equity:US:ABC",),
        cycle_ids=cycle_ids,
        decision_ids=("decision_1",),
        retro_run_ids=("retro_1",),
        retro_review_ids=("retro_review_1",),
        review_item_source_keys=("retro-action-retro_1-global-abc",),
    )


def _action(text: str = "Record the next decision before execution.") -> BehaviorActionInput:
    return BehaviorActionInput(
        action_text=text,
        action_code="PRETRADE_DECISION",
        review_item_source_keys=("retro-action-retro_1-global-abc",),
        retro_review_ids=("retro_review_1",),
        cycle_ids=("cycle_1",),
        decision_ids=("decision_1",),
    )


def test_action_statuses_new_persistent_resolved_and_recurred() -> None:
    calculator = BehaviorReviewRunCalculator()
    first = calculator.calculate(
        run_id="behavior_review_1",
        cohort=_cohort(T0),
        generated_at=T0,
        current_actions=(_action(),),
        idempotency_key="review-1",
    )
    assert first.action_observations[0].status is BehaviorActionStatus.NEW
    assert first.action_observations[0].occurrence_count == 1

    second = calculator.calculate(
        run_id="behavior_review_2",
        cohort=_cohort(T0 + timedelta(days=7)),
        generated_at=T0 + timedelta(days=7),
        current_actions=(_action(),),
        prior_observations=first.action_observations,
        idempotency_key="review-2",
    )
    assert second.action_observations[0].status is BehaviorActionStatus.PERSISTENT
    assert second.action_observations[0].occurrence_count == 2

    third = calculator.calculate(
        run_id="behavior_review_3",
        cohort=_cohort(T0 + timedelta(days=14)),
        generated_at=T0 + timedelta(days=14),
        current_actions=(),
        prior_observations=second.action_observations,
        idempotency_key="review-3",
    )
    assert third.action_observations[0].status is BehaviorActionStatus.RESOLVED
    assert third.action_observations[0].resolved_at == T0 + timedelta(days=14)

    fourth = calculator.calculate(
        run_id="behavior_review_4",
        cohort=_cohort(T0 + timedelta(days=21)),
        generated_at=T0 + timedelta(days=21),
        current_actions=(_action(),),
        prior_observations=third.action_observations,
        idempotency_key="review-4",
    )
    assert fourth.action_observations[0].status is BehaviorActionStatus.RECURRED
    assert fourth.action_observations[0].occurrence_count == 3


def test_incomplete_source_never_auto_resolves_disappeared_action() -> None:
    calculator = BehaviorReviewRunCalculator()
    first = calculator.calculate(
        run_id="behavior_review_10",
        cohort=_cohort(T0),
        generated_at=T0,
        current_actions=(_action(),),
        idempotency_key="review-10",
    )
    incomplete = calculator.calculate(
        run_id="behavior_review_11",
        cohort=_cohort(T0 + timedelta(days=7), cycle_ids=()),
        generated_at=T0 + timedelta(days=7),
        current_actions=(),
        prior_observations=first.action_observations,
        source_read_complete=False,
        source_error_code="RETRO_SOURCE_READ_FAILED",
        idempotency_key="review-11",
    )

    assert incomplete.action_observations == ()
    assert incomplete.source_read_complete is False
    assert incomplete.status.value == "INCOMPLETE"
    assert incomplete.warning_codes == ("RETRO_SOURCE_READ_FAILED",)


def test_action_dedup_and_cohort_key_are_order_independent() -> None:
    calculator = BehaviorReviewRunCalculator()
    first = calculator.calculate(
        run_id="behavior_review_20",
        cohort=_cohort(T0),
        generated_at=T0,
        current_actions=(
            _action("Record   the next decision before execution."),
            _action("Record the next decision before execution."),
        ),
        idempotency_key="review-20",
    )
    second = calculator.calculate(
        run_id="behavior_review_20",
        cohort=_cohort(T0),
        generated_at=T0,
        current_actions=tuple(reversed((_action(),))),
        idempotency_key="review-20",
    )

    assert first.cohort.cohort_key == second.cohort.cohort_key
    assert len(first.action_observations) == 1
    assert first.action_observations[0].stable_key == second.action_observations[0].stable_key
    assert (
        first.action_observations[0].observation_id
        == second.action_observations[0].observation_id
    )
    assert _action("A revised wording.").stable_key == _action().stable_key


def test_sqlalchemy_repository_is_append_only_and_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    repository = SqlAlchemyBehaviorReviewRepository(engine)
    run = BehaviorReviewRunCalculator().calculate(
        run_id="behavior_review_30",
        cohort=_cohort(T0),
        generated_at=T0,
        current_actions=(_action(),),
        idempotency_key="review-30",
    )

    assert repository.append_run(run) == run
    assert repository.append_run(run) == run
    assert repository.get_run(run.run_id) == run
    assert repository.get_run_by_idempotency_key(run.idempotency_key) == run
    assert repository.list_runs(limit=10) == (run,)
    assert repository.list_action_observations() == run.action_observations
