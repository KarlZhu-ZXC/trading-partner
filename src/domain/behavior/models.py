"""Immutable facts emitted by the Phase 4D behavior calculator.

The behavior projection deliberately has no score.  Every metric keeps the
sample facts (numerator, denominator, exclusions, and exact IDs) next to the
derived value so a caller can explain a rate without reconstructing its
denominator.  The values are projections only; the underlying TradeCycle,
DecisionRecord, and TradeRetro records remain the sources of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from domain.behavior.enums import BehaviorMetricAvailability
from domain.common.errors import DataContractError
from domain.portfolio.enums import TradeCycleClassification

BEHAVIOR_SUMMARY_ALGORITHM_VERSION = "behavior_summary_v1"
type BehaviorScalar = int | Decimal | None


def _bounded_text(value: str, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{field} must be non-blank text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return normalized


def _unique_texts(values: tuple[str, ...], field: str, maximum: int = 256) -> None:
    if not isinstance(values, tuple):
        raise DataContractError(f"{field} must be a tuple")
    if len(values) != len(set(values)):
        raise DataContractError(f"{field} must be unique")
    for value in values:
        _bounded_text(value, field, maximum)


def _finite_scalar(value: BehaviorScalar, field: str) -> None:
    if isinstance(value, Decimal) and not value.is_finite():
        raise DataContractError(f"{field} must be finite")
    if type(value) not in {int, Decimal, type(None)}:
        raise DataContractError(f"{field} must be int, Decimal, or None")


@dataclass(frozen=True, slots=True)
class BehaviorCohort:
    """Optional comparable-cohort filters.

    TradeCycle itself intentionally does not infer strategy or horizon.  When
    a strategy/horizon filter is supplied, a cycle must have a matching
    pre-fill DecisionRecord; otherwise it is excluded with a visible cohort
    reason.  Instrument and currency are native cycle facts and can be
    filtered directly.
    """

    strategy_code: str | None = None
    strategy_version: str | None = None
    horizon: str | None = None
    instrument_ids: tuple[str, ...] = ()
    currency: str | None = None
    classifications: tuple[TradeCycleClassification, ...] = ()

    def __post_init__(self) -> None:
        if self.strategy_code is not None:
            _bounded_text(self.strategy_code, "strategy_code")
        if self.strategy_version is not None:
            _bounded_text(self.strategy_version, "strategy_version")
        if self.horizon is not None:
            _bounded_text(self.horizon, "horizon")
        _unique_texts(self.instrument_ids, "instrument_ids", maximum=256)
        if self.currency is not None:
            _bounded_text(self.currency, "currency", maximum=32)
        if len(self.classifications) != len(set(self.classifications)) or any(
            not isinstance(item, TradeCycleClassification) for item in self.classifications
        ):
            raise DataContractError("classifications must be unique TradeCycle classifications")

    @property
    def instrument_id(self) -> str | None:
        """Convenience alias for the common one-instrument cohort."""

        if len(self.instrument_ids) == 1:
            return self.instrument_ids[0]
        return None


# Older callers may use either spelling while the domain vocabulary remains
# BehaviorCohort.  Keeping aliases costs no wire surface and eases migration.
BehaviorCohortFilter = BehaviorCohort
BehaviorSummaryFilter = BehaviorCohort


@dataclass(frozen=True, slots=True)
class BehaviorMetric:
    """One explainable behavior statistic.

    ``cycle_ids`` identify the numerator/positive observations.  The complete
    denominator sample is exposed as ``eligible_cycle_ids``; decision-only
    metrics leave it empty and use ``decision_ids`` instead.  This distinction
    prevents a consumer from mistaking a win list for the win-rate denominator.
    ``excluded_cycle_ids`` gives exact IDs for cycle exclusions.
    """

    name: str
    numerator: BehaviorScalar
    denominator: int | None
    value: BehaviorScalar
    excluded_count: int
    exclusion_reasons: tuple[str, ...]
    cycle_ids: tuple[str, ...] = ()
    eligible_cycle_ids: tuple[str, ...] = ()
    excluded_cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    eligible_decision_ids: tuple[str, ...] = ()
    excluded_decision_ids: tuple[str, ...] = ()
    sample_sufficient: bool = True
    minimum_sample_size: int = 1
    native_currencies: tuple[str, ...] = ()
    note: str | None = None
    availability: BehaviorMetricAvailability = BehaviorMetricAvailability.AVAILABLE
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.name, "metric name", maximum=160)
        _finite_scalar(self.numerator, "numerator")
        _finite_scalar(self.value, "value")
        if self.denominator is not None and (
            type(self.denominator) is not int or self.denominator < 0
        ):
            raise DataContractError("denominator must be a nonnegative int or None")
        if type(self.excluded_count) is not int or self.excluded_count < 0:
            raise DataContractError("excluded_count must be a nonnegative int")
        if type(self.minimum_sample_size) is not int or self.minimum_sample_size < 0:
            raise DataContractError("minimum_sample_size must be a nonnegative int")
        if type(self.sample_sufficient) is not bool:
            raise DataContractError("sample_sufficient must be bool")
        _unique_texts(self.exclusion_reasons, "exclusion_reasons", maximum=160)
        _unique_texts(self.cycle_ids, "cycle_ids")
        _unique_texts(self.eligible_cycle_ids, "eligible_cycle_ids")
        _unique_texts(self.excluded_cycle_ids, "excluded_cycle_ids")
        _unique_texts(self.decision_ids, "decision_ids")
        _unique_texts(self.eligible_decision_ids, "eligible_decision_ids")
        _unique_texts(self.excluded_decision_ids, "excluded_decision_ids")
        _unique_texts(self.native_currencies, "native_currencies", maximum=32)
        if not isinstance(self.availability, BehaviorMetricAvailability):
            raise DataContractError("availability must be BehaviorMetricAvailability")
        if set(self.eligible_cycle_ids) & set(self.excluded_cycle_ids):
            raise DataContractError("eligible and excluded cycle IDs must be disjoint")
        if set(self.eligible_decision_ids) & set(self.excluded_decision_ids):
            raise DataContractError("eligible and excluded decision IDs must be disjoint")
        if self.note is not None:
            _bounded_text(self.note, "note", maximum=2_000)
        if self.unavailable_reason is not None:
            _bounded_text(self.unavailable_reason, "unavailable_reason", maximum=160)
        if self.availability is not BehaviorMetricAvailability.AVAILABLE and (
            self.unavailable_reason is None
        ):
            raise DataContractError(
                "unavailable metrics must provide unavailable_reason"
            )

    @property
    def ratio(self) -> BehaviorScalar:
        """Alias for the derived value used by clients that call rates ratios."""

        return self.value

    @property
    def exact_cycle_ids(self) -> tuple[str, ...]:
        """Stable alias emphasizing that IDs are not a sampled approximation."""

        return self.cycle_ids


@dataclass(frozen=True, slots=True)
class BehaviorSummary:
    """Complete no-score behavior summary for one deterministic cohort."""

    closed_active_trade_cycles: BehaviorMetric
    wins: BehaviorMetric
    losses: BehaviorMetric
    flat: BehaviorMetric
    win_rate: BehaviorMetric
    avg_win: BehaviorMetric
    avg_loss: BehaviorMetric
    payoff_ratio: BehaviorMetric
    average_holding_duration: BehaviorMetric
    median_holding_duration: BehaviorMetric
    turnover: BehaviorMetric
    plan_coverage: BehaviorMetric
    pre_fill_decision_coverage: BehaviorMetric
    pre_fill_invalidation_proxy: BehaviorMetric
    invalidation_adherence: BehaviorMetric
    same_day_reentry: BehaviorMetric
    entry_attempt_count: BehaviorMetric
    same_entry_logic_attempt_count: BehaviorMetric
    third_attempt_without_new_plan: BehaviorMetric
    add_confirmation_risk_control: BehaviorMetric
    planned_holding_period_mismatch: BehaviorMetric
    scenario_action_distribution: tuple[BehaviorMetric, ...]
    no_action_count: BehaviorMetric
    no_action_review_completion: BehaviorMetric
    cohort: BehaviorCohort = BehaviorCohort()
    cohort_cycle_ids: tuple[str, ...] = ()
    cohort_excluded_cycle_ids: tuple[str, ...] = ()
    cohort_exclusion_reasons: tuple[str, ...] = ()
    native_currencies: tuple[str, ...] = ()
    algorithm_version: str = BEHAVIOR_SUMMARY_ALGORITHM_VERSION
    execution_effect: bool = False

    def __post_init__(self) -> None:
        metrics = self.metric_items
        if len({item.name for item in metrics}) != len(metrics):
            raise DataContractError("behavior metric names must be unique")
        _unique_texts(self.cohort_cycle_ids, "cohort_cycle_ids")
        _unique_texts(self.cohort_excluded_cycle_ids, "cohort_excluded_cycle_ids")
        _unique_texts(self.cohort_exclusion_reasons, "cohort_exclusion_reasons")
        if set(self.cohort_cycle_ids) & set(self.cohort_excluded_cycle_ids):
            raise DataContractError("cohort cycle IDs must be disjoint")
        _unique_texts(self.native_currencies, "native_currencies", maximum=32)
        _bounded_text(self.algorithm_version, "algorithm_version", maximum=64)
        if self.execution_effect is not False:
            raise DataContractError("behavior summary must not have execution effect")

    @property
    def metric_items(self) -> tuple[BehaviorMetric, ...]:
        return (
            self.closed_active_trade_cycles,
            self.wins,
            self.losses,
            self.flat,
            self.win_rate,
            self.avg_win,
            self.avg_loss,
            self.payoff_ratio,
            self.average_holding_duration,
            self.median_holding_duration,
            self.turnover,
            self.plan_coverage,
            self.pre_fill_decision_coverage,
            self.pre_fill_invalidation_proxy,
            self.invalidation_adherence,
            self.same_day_reentry,
            self.entry_attempt_count,
            self.same_entry_logic_attempt_count,
            self.third_attempt_without_new_plan,
            self.add_confirmation_risk_control,
            self.planned_holding_period_mismatch,
            *self.scenario_action_distribution,
            self.no_action_count,
            self.no_action_review_completion,
        )

    @property
    def metrics(self) -> tuple[BehaviorMetric, ...]:
        """Public collection form; no aggregate score is computed."""

        return self.metric_items

    def metric(self, name: str) -> BehaviorMetric:
        aliases = {
            "avg_holding_duration": "average_holding_duration",
            "invalidation_exit_ratio": "invalidation_adherence",
            "entry_logic_attempt_count": "same_entry_logic_attempt_count",
            "plan_horizon_mismatch": "planned_holding_period_mismatch",
            "horizon_drift": "planned_holding_period_mismatch",
        }
        name = aliases.get(name, name)
        for item in self.metric_items:
            if item.name == name:
                return item
        raise KeyError(name)

    @property
    def avg_holding_duration(self) -> BehaviorMetric:
        """Short alias for the canonical average holding-duration metric."""

        return self.average_holding_duration

    @property
    def invalidation_exit_ratio(self) -> BehaviorMetric:
        return self.invalidation_adherence

    @property
    def horizon_drift(self) -> BehaviorMetric:
        return self.planned_holding_period_mismatch

    @property
    def unavailable_metrics(self) -> tuple[BehaviorMetric, ...]:
        return tuple(
            item
            for item in self.metric_items
            if item.availability is not BehaviorMetricAvailability.AVAILABLE
        )


__all__ = [
    "BEHAVIOR_SUMMARY_ALGORITHM_VERSION",
    "BehaviorCohort",
    "BehaviorCohortFilter",
    "BehaviorMetric",
    "BehaviorScalar",
    "BehaviorSummary",
    "BehaviorSummaryFilter",
]
