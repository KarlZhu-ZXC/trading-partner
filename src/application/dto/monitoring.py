"""Closed request/response DTOs for Phase 2C Monitoring."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from application.dto.market import DecimalWire
from domain.common.time import require_aware_datetime
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventAction,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorEventResolution,
    MonitorRule,
    MonitorRuleState,
    MonitorRun,
)
from domain.risk.enums import RiskOverallStatus


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class MonitorRuleInput(_DTO):
    rule_code: str = Field(min_length=1, max_length=64)
    rule_type: MonitorRuleType
    severity: MonitorSeverity = MonitorSeverity.MEDIUM
    instrument_id: str | None = None
    price_threshold: Decimal | None = Field(default=None, gt=0)
    risk_status_threshold: RiskOverallStatus | None = None
    max_fact_age_seconds: int = Field(default=3600, gt=0)

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> MonitorRule:
        return MonitorRule(
            rule_code=self.rule_code,
            rule_type=self.rule_type,
            severity=self.severity,
            instrument_id=self.instrument_id,
            price_threshold=self.price_threshold,
            risk_status_threshold=self.risk_status_threshold,
            max_fact_age_seconds=self.max_fact_age_seconds,
        )


class MonitorCreateInput(_DTO):
    name: str = Field(min_length=1, max_length=200)
    case_id: str | None = None
    primary_instrument_id: str | None = None
    cadence: MonitorCadence = MonitorCadence.ON_DEMAND
    rules: tuple[MonitorRuleInput, ...] = Field(min_length=1, max_length=50)
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class MonitorUpdateInput(_DTO):
    monitor_id: str
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    case_id: str | None = None
    primary_instrument_id: str | None = None
    cadence: MonitorCadence
    status: MonitorStatus
    rules: tuple[MonitorRuleInput, ...] = Field(min_length=1, max_length=50)
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class MonitorGetInput(_DTO):
    monitor_id: str


class MonitorListInput(_DTO):
    status: MonitorStatus | None = None


class MonitorEvaluateInput(_DTO):
    monitor_ids: tuple[str, ...] = ()
    cadence: MonitorCadence | None = None
    as_of: datetime | None = None

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.monitor_ids and self.cadence is not None:
            raise ValueError("monitor_ids cannot be combined with cadence")
        if len(self.monitor_ids) != len(set(self.monitor_ids)):
            raise ValueError("monitor_ids must be unique")
        if self.as_of is not None:
            require_aware_datetime(self.as_of, field_name="as_of")
        return self


class MonitorEventListInput(_DTO):
    monitor_id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class MonitorEventResolveInput(_DTO):
    event_id: str
    action: MonitorEventAction
    note: str = Field(min_length=1, max_length=2000)
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class MonitorRuleDTO(_DTO):
    rule_code: str
    rule_type: MonitorRuleType
    severity: MonitorSeverity
    instrument_id: str | None
    price_threshold: DecimalWire | None
    risk_status_threshold: RiskOverallStatus | None
    max_fact_age_seconds: int


class MonitorDefinitionDTO(_DTO):
    monitor_id: str
    version: int
    name: str
    case_id: str | None
    primary_instrument_id: str | None
    cadence: MonitorCadence
    status: MonitorStatus
    rules: tuple[MonitorRuleDTO, ...]
    confirmed_by: str
    idempotency_key: str
    created_at: datetime
    schema_version: int

    @classmethod
    def from_domain(cls, value: MonitorDefinition) -> MonitorDefinitionDTO:
        return cls.model_validate(value)


class MonitorRuleStateDTO(_DTO):
    monitor_id: str
    monitor_version: int
    rule_code: str
    state: MonitorRuleStateValue
    observed_value: DecimalWire | None
    fact_as_of: datetime | None
    message: str
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: MonitorRuleState) -> MonitorRuleStateDTO:
        return cls.model_validate(value)


class MonitorDetailDTO(_DTO):
    monitor: MonitorDefinitionDTO
    rule_states: tuple[MonitorRuleStateDTO, ...]


class MonitorListDTO(_DTO):
    monitors: tuple[MonitorDefinitionDTO, ...]


class MonitorEventResolutionDTO(_DTO):
    resolution_id: str
    event_id: str
    action: MonitorEventAction
    note: str
    confirmed_by: str
    idempotency_key: str
    created_at: datetime

    @classmethod
    def from_domain(cls, value: MonitorEventResolution) -> MonitorEventResolutionDTO:
        return cls.model_validate(value)


class MonitorEventDTO(_DTO):
    event_id: str
    monitor_id: str
    monitor_version: int
    rule_code: str
    event_type: MonitorEventType
    severity: MonitorSeverity
    observed_value: DecimalWire | None
    threshold_value: DecimalWire | None
    fact_as_of: datetime | None
    message: str
    created_at: datetime
    latest_resolution: MonitorEventResolutionDTO | None = None

    @classmethod
    def from_domain(
        cls,
        value: MonitorEvent,
        resolution: MonitorEventResolution | None = None,
    ) -> MonitorEventDTO:
        dto = cls.model_validate(value)
        if resolution is None:
            return dto
        return dto.model_copy(
            update={
                "latest_resolution": MonitorEventResolutionDTO.from_domain(resolution)
            }
        )


class MonitorEventListDTO(_DTO):
    events: tuple[MonitorEventDTO, ...]


class MonitorRunDTO(_DTO):
    run_id: str
    requested_monitor_ids: tuple[str, ...]
    as_of: datetime
    started_at: datetime
    completed_at: datetime
    status: MonitorRunStatus
    monitors_evaluated: int
    rules_evaluated: int
    events_created: int
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: MonitorRun) -> MonitorRunDTO:
        return cls.model_validate(value)
