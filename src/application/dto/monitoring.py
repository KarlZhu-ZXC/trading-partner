"""Closed request/response DTOs for Phase 2C Monitoring."""

from __future__ import annotations

import json
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
    MonitorJudgmentConclusion,
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
    MonitorJudgment,
    MonitorJudgmentPolicy,
    MonitorRule,
    MonitorRuleState,
    MonitorRun,
    MonitorRunObservation,
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
MonitorEventActionInput = Annotated[MonitorEventAction, BeforeValidator(_normalize_enum_wire_value)]
RiskOverallStatusInput = Annotated[RiskOverallStatus, BeforeValidator(_normalize_enum_wire_value)]
TradePlanFactTypeInput = Annotated[TradePlanFactType, BeforeValidator(_normalize_enum_wire_value)]
TradePlanComparatorInput = Annotated[
    TradePlanComparator, BeforeValidator(_normalize_enum_wire_value)
]


class MonitorRuleInput(_DTO):
    rule_code: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
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
    recovery_threshold: Decimal | None = None
    technical_interval: Literal["1d", "1w"] | None = None
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
            description=self.description.strip(),
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
            recovery_threshold=self.recovery_threshold,
            technical_interval=self.technical_interval,
            event_after=self.event_after,
        )


class MonitorRelativeStrengthPairInput(_DTO):
    name: str = Field(min_length=1, max_length=64)
    numerator_instrument_id: str
    denominator_instrument_id: str


class MonitorJudgmentPolicyInput(_DTO):
    playbook: str = Field(min_length=1, max_length=16000)
    reference_instrument_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    relative_strength_pairs: tuple[MonitorRelativeStrengthPairInput, ...] = Field(
        default=(), max_length=12
    )
    confirmed_state: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=50
    )

    def to_domain(self) -> MonitorJudgmentPolicy:
        return MonitorJudgmentPolicy(
            playbook=self.playbook.strip(),
            reference_instrument_ids=self.reference_instrument_ids,
            relative_strength_pairs=tuple(
                (item.name.strip(), item.numerator_instrument_id, item.denominator_instrument_id)
                for item in self.relative_strength_pairs
            ),
            confirmed_state_json=json.dumps(
                self.confirmed_state, separators=(",", ":"), sort_keys=True
            ),
        )


class MonitorCreateInput(_DTO):
    name: str = Field(min_length=1, max_length=200)
    subject_id: str | None = None
    primary_instrument_id: str | None = Field(
        default=None,
        description=(
            "Primary observation/display instrument. For a Trade Plan-bound Monitor "
            "this may be a condition reference instrument rather than the plan's "
            "execution instrument."
        ),
    )
    cadence: MonitorCadenceInput = MonitorCadence.ON_DEMAND
    interval_minutes: int | None = Field(default=None, ge=60, le=10080)
    rules: tuple[MonitorRuleInput, ...] = Field(default=(), max_length=50)
    trade_plan_id: str | None = None
    trade_plan_version: int | None = Field(default=None, ge=1)
    compile_trade_plan_conditions: bool = False
    valid_until: datetime | None = None
    judgment_policy: MonitorJudgmentPolicyInput | None = None
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
        _validate_schedule(self.cadence, self.interval_minutes)
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
    subject_id: str | None = None
    primary_instrument_id: str | None = Field(
        default=None,
        description=(
            "Primary observation/display instrument. It may be a condition reference "
            "instrument distinct from a bound Trade Plan execution instrument."
        ),
    )
    cadence: MonitorCadenceInput
    interval_minutes: int | None = Field(default=None, ge=60, le=10080)
    status: MonitorStatusInput
    rules: tuple[MonitorRuleInput, ...] = Field(default=(), max_length=50)
    trade_plan_id: str | None = None
    trade_plan_version: int | None = Field(default=None, ge=1)
    compile_trade_plan_conditions: bool = False
    valid_until: datetime | None = None
    judgment_policy: MonitorJudgmentPolicyInput | None = None
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
        _validate_schedule(self.cadence, self.interval_minutes)
        if (self.trade_plan_id is None) != (self.trade_plan_version is None):
            raise ValueError("trade_plan_id and trade_plan_version are required together")
        if self.compile_trade_plan_conditions and self.trade_plan_id is None:
            raise ValueError("compile_trade_plan_conditions requires a Trade Plan version")
        if not self.rules and not self.compile_trade_plan_conditions:
            raise ValueError("monitor requires rules or Trade Plan condition compilation")
        return self


class MonitorArchiveInput(_DTO):
    """Explicit, audited soft-delete request for local operator surfaces."""

    monitor_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)


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
        if len(self.monitor_ids) != len(set(self.monitor_ids)):
            raise ValueError("monitor_ids must be unique")
        if self.as_of is not None:
            require_aware_datetime(self.as_of, field_name="as_of")
        return self


class MonitorEventListInput(_DTO):
    monitor_id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class MonitorDashboardInput(_DTO):
    status: MonitorStatusInput | None = MonitorStatus.ACTIVE


class MonitorRunListInput(_DTO):
    run_id: str | None = None
    monitor_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_selector(self) -> Self:
        if self.run_id is not None and self.monitor_id is not None:
            raise ValueError("run_id and monitor_id cannot be combined")
        return self


class MonitorEventResolveInput(_DTO):
    event_id: str
    action: MonitorEventActionInput
    note: str = Field(min_length=1, max_length=2000)
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)


class MonitorRuleDTO(_DTO):
    rule_code: str
    description: str | None
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
    recovery_threshold: DecimalWire | None
    technical_interval: Literal["1d", "1w"] | None
    event_after: datetime | None


class MonitorJudgmentPolicyDTO(_DTO):
    playbook: str
    reference_instrument_ids: tuple[str, ...]
    relative_strength_pairs: tuple[tuple[str, str, str], ...]
    confirmed_state: dict[str, str | int | float | bool | None]
    prompt_version: str

    @classmethod
    def from_domain(cls, value: MonitorJudgmentPolicy) -> MonitorJudgmentPolicyDTO:
        return cls(
            playbook=value.playbook,
            reference_instrument_ids=value.reference_instrument_ids,
            relative_strength_pairs=value.relative_strength_pairs,
            confirmed_state=json.loads(value.confirmed_state_json),
            prompt_version=value.prompt_version,
        )


class MonitorDefinitionDTO(_DTO):
    monitor_id: str
    version: int
    name: str
    subject_id: str | None
    primary_instrument_id: str | None
    cadence: MonitorCadence
    interval_minutes: int | None
    status: MonitorStatus
    rules: tuple[MonitorRuleDTO, ...]
    trade_plan_id: str | None
    trade_plan_version: int | None
    valid_until: datetime | None
    confirmed_by: str
    idempotency_key: str
    created_at: datetime
    schema_version: int
    judgment_policy: MonitorJudgmentPolicyDTO | None

    @classmethod
    def from_domain(cls, value: MonitorDefinition) -> MonitorDefinitionDTO:
        return cls(
            monitor_id=value.monitor_id,
            version=value.version,
            name=value.name,
            subject_id=value.subject_id,
            primary_instrument_id=value.primary_instrument_id,
            cadence=value.cadence,
            interval_minutes=value.interval_minutes,
            status=value.status,
            rules=tuple(MonitorRuleDTO.model_validate(item) for item in value.rules),
            trade_plan_id=value.trade_plan_id,
            trade_plan_version=value.trade_plan_version,
            valid_until=value.valid_until,
            confirmed_by=value.confirmed_by,
            idempotency_key=value.idempotency_key,
            created_at=value.created_at,
            schema_version=value.schema_version,
            judgment_policy=(
                MonitorJudgmentPolicyDTO.from_domain(value.judgment_policy)
                if value.judgment_policy is not None
                else None
            ),
        )


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


class MonitorJudgmentDTO(_DTO):
    judgment_id: str
    run_id: str
    monitor_id: str
    monitor_version: int
    status: Literal["SUCCEEDED", "SKIPPED", "FAILED"]
    urgency: Literal["WATCH", "ACTION", "URGENT"] | None
    phase: str | None
    market_state: str | None
    divergence: Literal["BULLISH", "BEARISH", "NONE"] | None
    conclusion: MonitorJudgmentConclusion | None
    quantity_min: int | None
    quantity_max: int | None
    summary: str
    evidence_feature_ids: tuple[str, ...]
    next_trigger: str | None
    invalidation: str | None
    feature_signature: str
    result_fingerprint: str | None
    provider: str
    model: str
    reasoning_effort: str
    prompt_version: str
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    created_at: datetime
    web_search_used: bool = False
    web_source_urls: tuple[str, ...] = ()

    @classmethod
    def from_domain(cls, value: MonitorJudgment) -> MonitorJudgmentDTO:
        return cls.model_validate(value)


class MonitorDetailDTO(_DTO):
    monitor: MonitorDefinitionDTO
    rule_states: tuple[MonitorRuleStateDTO, ...]
    latest_judgment: MonitorJudgmentDTO | None = None
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
            update={"latest_resolution": MonitorEventResolutionDTO.from_domain(resolution)}
        )


class MonitorEventListDTO(_DTO):
    events: tuple[MonitorEventDTO, ...]
    execution_effect: Literal[False] = False


class ProviderFailureDiagnosticDTO(_DTO):
    provider: str
    stage: str
    error_code: str
    retryable: bool
    attempt_count: int
    error_type: str | None = None
    status_class: str | None = None
    status_code: int | None = None


class MonitorRunObservationDTO(_DTO):
    run_id: str
    monitor_id: str
    monitor_version: int
    rule_code: str
    instrument_id: str | None
    severity: MonitorSeverity
    state: MonitorRuleStateValue
    observed_value: DecimalWire | None
    threshold_value: DecimalWire | None
    distance_value: DecimalWire | None
    distance_percent: DecimalWire | None
    fact_as_of: datetime | None
    fact_age_seconds: int | None
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    message: str
    diagnostics: tuple[ProviderFailureDiagnosticDTO, ...] = ()

    @classmethod
    def from_domain(cls, value: MonitorRunObservation) -> MonitorRunObservationDTO:
        return cls.model_validate(value)


class MonitorRunDTO(_DTO):
    run_id: str
    requested_monitor_ids: tuple[str, ...]
    selected_monitor_ids: tuple[str, ...]
    cadence: MonitorCadence | None
    as_of: datetime
    started_at: datetime
    completed_at: datetime
    status: MonitorRunStatus
    monitors_evaluated: int
    rules_evaluated: int
    events_created: int
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    observation_history_complete: bool
    observations: tuple[MonitorRunObservationDTO, ...]
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: MonitorRun) -> MonitorRunDTO:
        return cls.model_validate(value)


class MonitorRunListDTO(_DTO):
    runs: tuple[MonitorRunDTO, ...]
    execution_effect: Literal[False] = False


class MonitorLatestRunSummaryDTO(_DTO):
    run_id: str
    cadence: MonitorCadence | None
    completed_at: datetime
    status: MonitorRunStatus
    observation_count: int = Field(ge=0)
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    observation_history_complete: bool

    @classmethod
    def from_domain(cls, value: MonitorRun) -> MonitorLatestRunSummaryDTO:
        return cls(
            run_id=value.run_id,
            cadence=value.cadence,
            completed_at=value.completed_at,
            status=value.status,
            observation_count=len(value.observations),
            warning_codes=value.warning_codes,
            error_codes=value.error_codes,
            observation_history_complete=value.observation_history_complete,
        )


class MonitorDashboardItemDTO(_DTO):
    monitor: MonitorDefinitionDTO
    monitor_created_at: datetime
    monitor_updated_at: datetime
    rule_states: tuple[MonitorRuleStateDTO, ...]
    latest_run: MonitorLatestRunSummaryDTO | None
    latest_judgment: MonitorJudgmentDTO | None
    last_run_at: datetime | None
    next_due_at: datetime | None
    due: bool
    schedule_health: Literal[
        "ON_DEMAND",
        "MARKET_SCHEDULED",
        "MARKET_CLOSED",
        "NEVER_RUN",
        "ON_SCHEDULE",
        "OVERDUE",
    ]


class MonitorDashboardDTO(_DTO):
    generated_at: datetime
    items: tuple[MonitorDashboardItemDTO, ...]
    execution_effect: Literal[False] = False


def _validate_schedule(cadence: MonitorCadence, interval_minutes: int | None) -> None:
    if cadence is MonitorCadence.INTERVAL:
        if interval_minutes is None or interval_minutes % 60 != 0:
            raise ValueError("INTERVAL cadence requires whole-hour interval_minutes")
    elif interval_minutes is not None:
        raise ValueError("interval_minutes is only valid for INTERVAL cadence")
