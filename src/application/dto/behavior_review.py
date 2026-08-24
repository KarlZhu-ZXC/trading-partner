"""Closed application DTOs for cross-period behavior reviews."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.behavior_review.enums import (
    BehaviorActionStatus,
    BehaviorReviewPeriodKind,
    BehaviorReviewRunStatus,
)
from domain.behavior_review.models import (
    BehaviorActionInput,
    BehaviorActionObservation,
    BehaviorReviewCohort,
    BehaviorReviewRun,
)


class _DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        from_attributes=True,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


class BehaviorReviewCohortDTO(_DTO):
    period_kind: BehaviorReviewPeriodKind
    period_start: datetime
    period_end: datetime
    strategy_code: str | None = None
    strategy_version: str | None = None
    horizon: str | None = None
    instrument_ids: tuple[str, ...] = ()
    currency: str | None = None
    cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    retro_run_ids: tuple[str, ...] = ()
    retro_review_ids: tuple[str, ...] = ()
    review_item_source_keys: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()

    _start_aware = field_validator("period_start", "period_end")(_aware)

    @model_validator(mode="after")
    def ordered(self) -> BehaviorReviewCohortDTO:
        if self.period_start >= self.period_end:
            raise ValueError("period_end must follow period_start")
        return self

    def to_domain(self) -> BehaviorReviewCohort:
        return BehaviorReviewCohort(
            period_kind=self.period_kind,
            period_start=self.period_start,
            period_end=self.period_end,
            strategy_code=self.strategy_code,
            strategy_version=self.strategy_version,
            horizon=self.horizon,
            instrument_ids=self.instrument_ids,
            currency=self.currency,
            cycle_ids=self.cycle_ids,
            decision_ids=self.decision_ids,
            retro_run_ids=self.retro_run_ids,
            retro_review_ids=self.retro_review_ids,
            review_item_source_keys=self.review_item_source_keys,
            subject_ids=self.subject_ids,
        )

    @classmethod
    def from_domain(cls, value: BehaviorReviewCohort) -> BehaviorReviewCohortDTO:
        return cls(
            period_kind=value.period_kind,
            period_start=value.period_start,
            period_end=value.period_end,
            strategy_code=value.strategy_code,
            strategy_version=value.strategy_version,
            horizon=value.horizon,
            instrument_ids=value.instrument_ids,
            currency=value.currency,
            cycle_ids=value.cycle_ids,
            decision_ids=value.decision_ids,
            retro_run_ids=value.retro_run_ids,
            retro_review_ids=value.retro_review_ids,
            review_item_source_keys=value.review_item_source_keys,
            subject_ids=value.subject_ids,
        )


class BehaviorActionInputDTO(_DTO):
    action_text: str = Field(min_length=1, max_length=2_000)
    action_code: str | None = Field(default=None, min_length=1, max_length=128)
    review_item_source_keys: tuple[str, ...] = ()
    retro_review_ids: tuple[str, ...] = ()
    cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()

    def to_domain(self) -> BehaviorActionInput:
        return BehaviorActionInput(
            action_text=self.action_text,
            action_code=self.action_code,
            review_item_source_keys=self.review_item_source_keys,
            retro_review_ids=self.retro_review_ids,
            cycle_ids=self.cycle_ids,
            decision_ids=self.decision_ids,
        )


class BehaviorReviewRunInput(_DTO):
    period_kind: BehaviorReviewPeriodKind
    period_start: datetime
    period_end: datetime
    strategy_code: str | None = Field(default=None, min_length=1, max_length=128)
    strategy_version: str | None = Field(default=None, min_length=1, max_length=128)
    horizon: str | None = Field(default=None, min_length=1, max_length=128)
    instrument_ids: tuple[str, ...] = ()
    currency: str | None = Field(default=None, min_length=1, max_length=32)
    cycle_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    retro_run_ids: tuple[str, ...] = ()
    retro_review_ids: tuple[str, ...] = ()
    review_item_source_keys: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()
    action_items: tuple[BehaviorActionInputDTO, ...] = ()
    source_read_complete: bool = True
    source_error_code: str | None = Field(default=None, min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=200)

    _start_aware = field_validator("period_start", "period_end")(_aware)

    @model_validator(mode="after")
    def validate_window(self) -> BehaviorReviewRunInput:
        if self.period_start >= self.period_end:
            raise ValueError("period_end must follow period_start")
        if self.source_read_complete and self.source_error_code is not None:
            raise ValueError("source_error_code requires incomplete source read")
        if type(self.source_read_complete) is not bool:
            raise ValueError("source_read_complete must be bool")
        return self

    def cohort(self) -> BehaviorReviewCohort:
        return BehaviorReviewCohortDTO(
            period_kind=self.period_kind,
            period_start=self.period_start,
            period_end=self.period_end,
            strategy_code=self.strategy_code,
            strategy_version=self.strategy_version,
            horizon=self.horizon,
            instrument_ids=self.instrument_ids,
            currency=self.currency,
            cycle_ids=self.cycle_ids,
            decision_ids=self.decision_ids,
            retro_run_ids=self.retro_run_ids,
            retro_review_ids=self.retro_review_ids,
            review_item_source_keys=self.review_item_source_keys,
            subject_ids=self.subject_ids,
        ).to_domain()


class BehaviorActionObservationDTO(_DTO):
    observation_id: str
    run_id: str
    stable_key: str
    action_text: str
    action_code: str | None
    status: BehaviorActionStatus
    occurrence_count: int
    period_key: str
    cohort_key: str
    review_item_source_keys: tuple[str, ...]
    retro_review_ids: tuple[str, ...]
    cycle_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    observed_at: datetime
    previous_observation_id: str | None
    resolved_at: datetime | None
    resolution_note: str | None

    @classmethod
    def from_domain(cls, value: BehaviorActionObservation) -> BehaviorActionObservationDTO:
        return cls.model_validate(value)


class BehaviorReviewRunDTO(_DTO):
    run_id: str
    cohort: BehaviorReviewCohortDTO
    generated_at: datetime
    status: BehaviorReviewRunStatus
    source_read_complete: bool
    action_observations: tuple[BehaviorActionObservationDTO, ...]
    warning_codes: tuple[str, ...]
    idempotency_key: str
    source_error_code: str | None
    algorithm_version: str
    schema_version: int
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: BehaviorReviewRun) -> BehaviorReviewRunDTO:
        return cls(
            run_id=value.run_id,
            cohort=BehaviorReviewCohortDTO.from_domain(value.cohort),
            generated_at=value.generated_at,
            status=value.status,
            source_read_complete=value.source_read_complete,
            action_observations=tuple(
                BehaviorActionObservationDTO.from_domain(item)
                for item in value.action_observations
            ),
            warning_codes=value.warning_codes,
            idempotency_key=value.idempotency_key,
            source_error_code=value.source_error_code,
            algorithm_version=value.algorithm_version,
            schema_version=value.schema_version,
            execution_effect=value.execution_effect,
        )


__all__ = [
    "BehaviorActionInputDTO",
    "BehaviorActionObservationDTO",
    "BehaviorReviewCohortDTO",
    "BehaviorReviewRunDTO",
    "BehaviorReviewRunInput",
]
