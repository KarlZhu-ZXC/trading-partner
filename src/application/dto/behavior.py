"""Closed application DTOs for read-only Phase 4D behavior analytics."""

from __future__ import annotations

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from application.dto.market import DecimalWire
from domain.behavior.enums import BehaviorMetricAvailability
from domain.behavior.models import BehaviorCohort, BehaviorMetric, BehaviorSummary
from domain.common.enums import VendorId
from domain.portfolio.enums import TradeCycleClassification


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class BehaviorCohortDTO(_DTO):
    strategy_code: str | None = None
    strategy_version: str | None = None
    horizon: str | None = None
    instrument_ids: tuple[str, ...] = ()
    currency: str | None = None
    classifications: tuple[TradeCycleClassification, ...] = ()
    start: datetime | None = None
    end: datetime | None = None

    @classmethod
    def from_domain(cls, value: BehaviorCohort) -> BehaviorCohortDTO:
        return cls.model_validate(value)


BehaviorScalarWire = DecimalWire | int | None


class BehaviorMetricDTO(_DTO):
    name: str
    numerator: BehaviorScalarWire
    denominator: int | None
    value: BehaviorScalarWire
    excluded_count: int
    exclusion_reasons: tuple[str, ...]
    cycle_ids: tuple[str, ...] = ()
    eligible_cycle_ids: tuple[str, ...] = ()
    excluded_cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    eligible_decision_ids: tuple[str, ...] = ()
    excluded_decision_ids: tuple[str, ...] = ()
    sample_sufficient: bool
    minimum_sample_size: int
    native_currencies: tuple[str, ...] = ()
    note: str | None = None
    availability: BehaviorMetricAvailability = BehaviorMetricAvailability.AVAILABLE
    unavailable_reason: str | None = None

    @classmethod
    def from_domain(cls, value: BehaviorMetric) -> BehaviorMetricDTO:
        return cls.model_validate(value)


class BehaviorSummaryDTO(_DTO):
    closed_active_trade_cycles: BehaviorMetricDTO
    wins: BehaviorMetricDTO
    losses: BehaviorMetricDTO
    flat: BehaviorMetricDTO
    win_rate: BehaviorMetricDTO
    avg_win: BehaviorMetricDTO
    avg_loss: BehaviorMetricDTO
    payoff_ratio: BehaviorMetricDTO
    average_holding_duration: BehaviorMetricDTO
    median_holding_duration: BehaviorMetricDTO
    turnover: BehaviorMetricDTO
    plan_coverage: BehaviorMetricDTO
    pre_fill_decision_coverage: BehaviorMetricDTO
    pre_fill_invalidation_proxy: BehaviorMetricDTO
    invalidation_adherence: BehaviorMetricDTO
    same_day_reentry: BehaviorMetricDTO
    entry_attempt_count: BehaviorMetricDTO
    same_entry_logic_attempt_count: BehaviorMetricDTO
    third_attempt_without_new_plan: BehaviorMetricDTO
    add_confirmation_risk_control: BehaviorMetricDTO
    planned_holding_period_mismatch: BehaviorMetricDTO
    scenario_action_distribution: tuple[BehaviorMetricDTO, ...] = Field(default=())
    no_action_count: BehaviorMetricDTO
    no_action_review_completion: BehaviorMetricDTO
    cohort: BehaviorCohortDTO
    cohort_cycle_ids: tuple[str, ...] = ()
    cohort_excluded_cycle_ids: tuple[str, ...] = ()
    cohort_exclusion_reasons: tuple[str, ...] = ()
    native_currencies: tuple[str, ...] = ()
    algorithm_version: str
    execution_effect: bool = False

    @classmethod
    def from_domain(cls, value: BehaviorSummary) -> BehaviorSummaryDTO:
        return cls(
            closed_active_trade_cycles=BehaviorMetricDTO.from_domain(
                value.closed_active_trade_cycles
            ),
            wins=BehaviorMetricDTO.from_domain(value.wins),
            losses=BehaviorMetricDTO.from_domain(value.losses),
            flat=BehaviorMetricDTO.from_domain(value.flat),
            win_rate=BehaviorMetricDTO.from_domain(value.win_rate),
            avg_win=BehaviorMetricDTO.from_domain(value.avg_win),
            avg_loss=BehaviorMetricDTO.from_domain(value.avg_loss),
            payoff_ratio=BehaviorMetricDTO.from_domain(value.payoff_ratio),
            average_holding_duration=BehaviorMetricDTO.from_domain(
                value.average_holding_duration
            ),
            median_holding_duration=BehaviorMetricDTO.from_domain(
                value.median_holding_duration
            ),
            turnover=BehaviorMetricDTO.from_domain(value.turnover),
            plan_coverage=BehaviorMetricDTO.from_domain(value.plan_coverage),
            pre_fill_decision_coverage=BehaviorMetricDTO.from_domain(
                value.pre_fill_decision_coverage
            ),
            pre_fill_invalidation_proxy=BehaviorMetricDTO.from_domain(
                value.pre_fill_invalidation_proxy
            ),
            invalidation_adherence=BehaviorMetricDTO.from_domain(
                value.invalidation_adherence
            ),
            same_day_reentry=BehaviorMetricDTO.from_domain(value.same_day_reentry),
            entry_attempt_count=BehaviorMetricDTO.from_domain(value.entry_attempt_count),
            same_entry_logic_attempt_count=BehaviorMetricDTO.from_domain(
                value.same_entry_logic_attempt_count
            ),
            third_attempt_without_new_plan=BehaviorMetricDTO.from_domain(
                value.third_attempt_without_new_plan
            ),
            add_confirmation_risk_control=BehaviorMetricDTO.from_domain(
                value.add_confirmation_risk_control
            ),
            planned_holding_period_mismatch=BehaviorMetricDTO.from_domain(
                value.planned_holding_period_mismatch
            ),
            scenario_action_distribution=tuple(
                BehaviorMetricDTO.from_domain(item)
                for item in value.scenario_action_distribution
            ),
            no_action_count=BehaviorMetricDTO.from_domain(value.no_action_count),
            no_action_review_completion=BehaviorMetricDTO.from_domain(
                value.no_action_review_completion
            ),
            cohort=BehaviorCohortDTO.from_domain(value.cohort),
            cohort_cycle_ids=value.cohort_cycle_ids,
            cohort_excluded_cycle_ids=value.cohort_excluded_cycle_ids,
            cohort_exclusion_reasons=value.cohort_exclusion_reasons,
            native_currencies=value.native_currencies,
            algorithm_version=value.algorithm_version,
            execution_effect=value.execution_effect,
        )

    @property
    def metric_items(self) -> tuple[BehaviorMetricDTO, ...]:
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
    def avg_holding_duration(self) -> BehaviorMetricDTO:
        return self.average_holding_duration

    @property
    def invalidation_exit_ratio(self) -> BehaviorMetricDTO:
        return self.invalidation_adherence

    @property
    def horizon_drift(self) -> BehaviorMetricDTO:
        return self.planned_holding_period_mismatch

    @property
    def unavailable_metrics(self) -> tuple[BehaviorMetricDTO, ...]:
        return tuple(
            item
            for item in self.metric_items
            if item.availability is not BehaviorMetricAvailability.AVAILABLE
        )


class BehaviorSummaryQueryInput(_DTO):
    case_id: str | None = Field(default=None, min_length=1, max_length=128)
    providers: tuple[VendorId, ...] = ()
    account_refs: tuple[str, ...] = ()
    instrument_ids: tuple[str, ...] = ()
    strategy_code: str | None = Field(default=None, min_length=1, max_length=128)
    strategy_version: str | None = Field(default=None, min_length=1, max_length=128)
    horizon: str | None = Field(default=None, min_length=1, max_length=128)
    currency: str | None = Field(default=None, min_length=1, max_length=32)
    classifications: tuple[TradeCycleClassification, ...] = ()
    minimum_sample_size: int = Field(default=3, ge=0, le=100)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> BehaviorSummaryQueryInput:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("behavior summary start must be <= end")
        return self


__all__ = [
    "BehaviorCohortDTO",
    "BehaviorMetricDTO",
    "BehaviorScalarWire",
    "BehaviorSummaryDTO",
    "BehaviorSummaryQueryInput",
]
