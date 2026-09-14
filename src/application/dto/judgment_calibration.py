"""Durable reviewed-judgment calibration, with deliberately separate dimensions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from application.dto.judgment_scorecard import ScorecardDimensionDTO, ScorecardSourceRefDTO


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CalibrationBaselineDTO(_DTO):
    decision_id: str
    decision_type: str
    title: str
    recorded_at: datetime
    thesis_revision_ids: tuple[str, ...]
    trade_plan_id: str | None
    trade_plan_version: int | None
    review_due_at: datetime | None


class CalibrationObservationDTO(_DTO):
    source_id: str
    observed_at: datetime
    occurred_at: datetime | None = None
    title: str
    summary: str
    status: str
    source_refs: tuple[ScorecardSourceRefDTO, ...] = ()
    limitation_codes: tuple[str, ...] = ()


class CalibrationCardDTO(_DTO):
    scorecard_id: str
    thesis_revision_id: str
    generated_at: datetime
    dimension: ScorecardDimensionDTO
    warning_codes: tuple[str, ...] = ()


class CalibrationDimensionDTO(_DTO):
    code: str
    title: str
    status: str
    summary: str
    cards: tuple[CalibrationCardDTO, ...] = ()
    observations: tuple[CalibrationObservationDTO, ...] = ()
    limitation_codes: tuple[str, ...] = ()


class JudgmentCalibrationDTO(_DTO):
    subject_id: str
    as_of: datetime
    baseline: CalibrationBaselineDTO | None
    review_status: str
    dimensions: tuple[CalibrationDimensionDTO, ...]
    coverage: dict[str, str]
    warning_codes: tuple[str, ...]
    execution_effect: bool = False
