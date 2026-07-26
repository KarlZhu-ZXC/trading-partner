"""Closed request/response DTOs for Phase 2C Monitoring."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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
from domain.trade_plan.enums import TradePlanComparator, TradePlanFactType


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


def _normalize_enum_wire_value(value: object) -> object:
    """Accept conversational enum casing while preserving canonical wire values."""
    if isinstance(value, str):
        return value.strip().upper()
    return value


MonitorStatusInput = Annotated[MonitorStatus, BeforeValidator(_normalize_enum_wire_value)]
MonitorCadenceInput = Annotated[MonitorCadence, BeforeValidator(_normalize_enum_wire_value)]
MonitorRuleTypeInput = Annotated[MonitorRuleType, BeforeValidator(_normalize_enum_wire_value)]
MonitorSeverityInput = Annotated[MonitorSeverity, BeforeValidator(_normalize_enum_wire_value)]
MonitorEventActionInput = Annotated[
    MonitorEventAction, BeforeValidator(_normalize_enum_wire_value)
]
RiskOverallStatusInput = Annotated[
    RiskOverallStatus, BeforeValidator(_normalize_enum_wire_value)
]
TradePlanFactTypeInput = Annotated[
    TradePlanFactType, BeforeValidator(_normalize_enum_wire_value)
]
TradePlanComparatorInput = Annotated[
    TradePlanComparator, BeforeValidator(_normalize_enum_wire_value)
]


class MonitorRuleInput(_DTO):
    rule_code: str = Field(min_length=1, max_length=64)
    rule_type: MonitorRuleTypeInput
    severity: MonitorSeverityInput = MonitorSeverity.MEDIUM
    instrument_id: str | None = None
    price_threshold: Decimal | None = Field(default=None, gt=0)
    risk_status_threshold: RiskOverallStatusInput | None = None
    max_fact_age_seconds: int = Field(default=3600, gt=0)
    fact_type: TradePlanFactTypeInput | None = None
    metric_key: str | None = Field(default=None, min_length=1, max_length=128)
    comparator: TradePlanComparatorInput | None = None
    numeric_threshold: Decimal | None = None
    event_after: datetime | None = None

    @field_validator("event_after")
    @classmethod
    def validate_event_after(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="event_after")
        return value

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
            fact_type=self.fact_type,
            metric_key=self.metric_key,
            comparator=self.comparator,
            numeric_threshold=self.numeric_threshold,
            event_after=self.event_after,
        )


class MonitorCreateInput(_DTO):
    name: str = Field(min_length=1, max_length=200)
    case_id: str | None = None
    primary_instrument_id: str | None = None
    cadence: MonitorCadenceInput = MonitorCadence.ON_DEMAND
    rules: tuple[MonitorRuleInput, ...] = Field(default=(), max_length=50)
    trade_plan_id: str | None = None
    trade_plan_version: int | None = Field(default=None, ge=1)
    compile_trade_plan_conditions: bool = False
    valid_until: datetime | None = None
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("valid_until")
    @classmethod
    def validate_valid_until(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="valid_until")
        return value

    @model_validator(mode="after")
    def validate_rules_or_plan(self) -> Self:
        if (self.trade_plan_id is None) != (self.trade_plan_version is None):
            raise ValueError("trade_plan_id and trade_plan_version are required together")
        if self.compile_trade_plan_conditions and self.trade_plan_id is None:
            raise ValueError("compile_trade_plan_conditions requires a Trade Plan version")
        if not self.rules and not self.compile_trade_plan_conditions:
            raise ValueError("monitor requires rules or Trade Plan condition compilation")
        return self


class MonitorUpdateInput(_DTO):
    monitor_id: str
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    case_id: str | None = None
    primary_instrument_id: str | None = None
    cadence: MonitorCadenceInput
    status: MonitorStatusInput
    rules: tuple[MonitorRuleInput, ...] = Field(default=(), max_length=50)
    trade_plan_id: str | None = None
    trade_plan_version: int | None = Field(default=None, ge=1)
    compile_trade_plan_conditions: bool = False
    valid_until: datetime | None = None
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("valid_until")
    @classmethod
    def validate_valid_until(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="valid_until")
        return value

    @model_validator(mode="after")
    def validate_rules_or_plan(self) -> Self:
        if (self.trade_plan_id is None) != (self.trade_plan_version is None):
            raise ValueError("trade_plan_id and trade_plan_version are required together")
        if self.compile_trade_plan_conditions and self.trade_plan_id is None:
            raise ValueError("compile_trade_plan_conditions requires a Trade Plan version")
        if not self.rules and not self.compile_trade_plan_conditions:
            raise ValueError("monitor requires rules or Trade Plan condition compilation")
        return self


class MonitorGetInput(_DTO):
    monitor_id: str


class MonitorListInput(_DTO):
    status: MonitorStatusInput | None = None


class MonitorEvaluateInput(_DTO):
    monitor_ids: tuple[str, ...] = ()
    cadence: MonitorCadenceInput | None = None
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
    action: MonitorEventActionInput
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
    fact_type: TradePlanFactType | None
    metric_key: str | None
    comparator: TradePlanComparator | None
    numeric_threshold: DecimalWire | None
    event_after: datetime | None


class MonitorDefinitionDTO(_DTO):
    monitor_id: str
    version: int
    name: str
    case_id: str | None
    primary_instrument_id: str | None
    cadence: MonitorCadence
    status: MonitorStatus
    rules: tuple[MonitorRuleDTO, ...]
    trade_plan_id: str | None
    trade_plan_version: int | None
    valid_until: datetime | None
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
    execution_effect: Literal[False] = False


class MonitorListDTO(_DTO):
    monitors: tuple[MonitorDefinitionDTO, ...]
    execution_effect: Literal[False] = False


class MonitorEventResolutionDTO(_DTO):
    resolution_id: str
    event_id: str
    action: MonitorEventAction
    note: str
    confirmed_by: str
    idempotency_key: str
    created_at: datetime
    execution_effect: Literal[False] = False

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
    execution_effect: Literal[False] = False


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
