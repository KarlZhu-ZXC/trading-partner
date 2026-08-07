"""Phase 3D Trade Plan response DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from application.dto.market import DecimalWire
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from domain.trade_plan.models import TradePlan, TradePlanCondition


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class TradePlanConditionDTO(_DTO):
    condition_code: str
    phase: TradePlanConditionPhase
    mode: TradePlanConditionMode
    description: str
    severity: str
    fact_type: TradePlanFactType | None
    metric_key: str | None
    comparator: TradePlanComparator | None
    threshold: DecimalWire | None
    unit: str | None
    instrument_id: str | None
    max_fact_age_seconds: int | None
    event_after: datetime | None

    @classmethod
    def from_domain(cls, value: TradePlanCondition) -> TradePlanConditionDTO:
        return cls.model_validate(value)


class TradePlanDTO(_DTO):
    plan_id: str
    version: int
    subject_id: str
    thesis_id: str
    instrument_id: str
    status: TradePlanStatus
    valid_from: datetime
    valid_until: datetime | None
    currency: str
    reference_price: DecimalWire
    reference_price_at: datetime
    target_position_percent: DecimalWire
    max_position_percent: DecimalWire
    risk_budget_percent: DecimalWire
    stop_price: DecimalWire | None
    conditions: tuple[TradePlanConditionDTO, ...]
    notes: str
    confirmed_by: str
    created_at: datetime
    idempotency_key: str
    schema_version: int
    execution_effect: Literal[False] = False

    @classmethod
    def from_domain(cls, value: TradePlan) -> TradePlanDTO:
        return cls.model_validate(value)

    @classmethod
    def from_domain_list(cls, values: tuple[TradePlan, ...]) -> tuple[TradePlanDTO, ...]:
        return tuple(cls.from_domain(value) for value in values)
