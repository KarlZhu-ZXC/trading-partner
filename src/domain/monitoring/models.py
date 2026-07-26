"""Immutable Monitoring definitions, state transitions, events, and run receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.enums import Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
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
from domain.risk.enums import RiskOverallStatus
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanFactType,
)

MONITORING_SCHEMA_VERSION = 1

# Price rules may target A-share equities, US exchange instruments, CME/DCE
# futures identities, and OTC spot/CFD seeds. Evaluation remains asset-aware:
# DCE has no quote path and stays NOT_EVALUATED without invented settlements.
_PRICE_RULE_MARKETS = frozenset(
    {Market.A_SHARE, Market.US, Market.CME, Market.DCE, Market.OTC}
)


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be bounded non-blank text")
    return value.strip()


def _aware(value: datetime, field: str) -> None:
    require_aware_datetime(value, field_name=field)


def _decimal(value: Decimal | None, field: str, *, positive: bool = False) -> None:
    if value is None:
        return
    if type(value) is not Decimal or not value.is_finite():
        raise DataContractError(f"{field} must be a finite Decimal")
    if positive and value <= 0:
        raise DataContractError(f"{field} must be positive")


def _codes(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple) or len(values) != len(set(values)):
        raise DataContractError(f"{field} must be a unique tuple")
    for value in values:
        _text(value, field, 128)


@dataclass(frozen=True, slots=True)
class MonitorRule:
    rule_code: str
    rule_type: MonitorRuleType
    severity: MonitorSeverity
    instrument_id: str | None
    price_threshold: Decimal | None
    risk_status_threshold: RiskOverallStatus | None
    max_fact_age_seconds: int
    fact_type: TradePlanFactType | None = None
    metric_key: str | None = None
    comparator: TradePlanComparator | None = None
    numeric_threshold: Decimal | None = None
    event_after: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.rule_code, "rule_code", 64)
        if not isinstance(self.rule_type, MonitorRuleType):
            raise DataContractError("rule_type is invalid")
        if not isinstance(self.severity, MonitorSeverity):
            raise DataContractError("severity is invalid")
        if type(self.max_fact_age_seconds) is not int or self.max_fact_age_seconds <= 0:
            raise DataContractError("max_fact_age_seconds must be positive int")
        price_rule = self.rule_type in {
            MonitorRuleType.PRICE_ABOVE,
            MonitorRuleType.PRICE_BELOW,
        }
        if price_rule:
            if self.instrument_id is None or self.price_threshold is None:
                raise DataContractError("price rule requires instrument_id and price_threshold")
            _asset, market, _symbol = parse_instrument_id(self.instrument_id)
            if market not in _PRICE_RULE_MARKETS:
                raise DataContractError(
                    "price rule market must be A_SHARE, US, CME, DCE, or OTC",
                    details={"market": market.value},
                )
            _decimal(self.price_threshold, "price_threshold", positive=True)
            if self.risk_status_threshold is not None:
                raise DataContractError("price rule cannot set risk_status_threshold")
            if any(
                value is not None
                for value in (
                    self.fact_type,
                    self.metric_key,
                    self.comparator,
                    self.numeric_threshold,
                    self.event_after,
                )
            ):
                raise DataContractError("legacy price rule cannot set fact-comparison fields")
        elif self.rule_type is MonitorRuleType.RISK_OVERALL_AT_LEAST:
            if self.instrument_id is not None or self.price_threshold is not None:
                raise DataContractError("risk rule cannot set instrument or price threshold")
            if self.risk_status_threshold not in {
                RiskOverallStatus.WARN,
                RiskOverallStatus.BREACH,
            }:
                raise DataContractError("risk rule threshold must be WARN or BREACH")
            if any(
                value is not None
                for value in (
                    self.fact_type,
                    self.metric_key,
                    self.comparator,
                    self.numeric_threshold,
                    self.event_after,
                )
            ):
                raise DataContractError("legacy risk rule cannot set fact-comparison fields")
        else:
            if self.price_threshold is not None or self.risk_status_threshold is not None:
                raise DataContractError("fact rule cannot set legacy threshold fields")
            if not isinstance(self.fact_type, TradePlanFactType):
                raise DataContractError("fact rule requires fact_type")
            _text(self.metric_key, "metric_key", 128)
            if not isinstance(self.comparator, TradePlanComparator):
                raise DataContractError("fact rule requires comparator")
            if self.comparator is TradePlanComparator.OCCURRED:
                if self.numeric_threshold is not None:
                    raise DataContractError("OCCURRED rule cannot set numeric_threshold")
            else:
                if self.numeric_threshold is None:
                    raise DataContractError("numeric fact rule requires numeric_threshold")
                _decimal(self.numeric_threshold, "numeric_threshold")
            if self.event_after is not None:
                _aware(self.event_after, "event_after")
            requires_instrument = self.fact_type in {
                TradePlanFactType.PRICE,
                TradePlanFactType.VOLUME,
                TradePlanFactType.TECHNICAL,
                TradePlanFactType.FUNDAMENTAL,
                TradePlanFactType.COMPANY_EVENT,
                TradePlanFactType.SENTIMENT,
            }
            if requires_instrument and self.instrument_id is None:
                raise DataContractError("fact rule requires instrument_id")
            if self.instrument_id is not None:
                parse_instrument_id(self.instrument_id)
            if (
                self.fact_type is TradePlanFactType.COMPANY_EVENT
                and self.comparator is not TradePlanComparator.OCCURRED
            ):
                raise DataContractError("COMPANY_EVENT fact rule requires OCCURRED")
            if (
                self.fact_type is TradePlanFactType.PORTFOLIO_RISK
                and self.metric_key != "overall_status"
            ):
                raise DataContractError(
                    "PORTFOLIO_RISK fact rule metric_key must be overall_status"
                )


@dataclass(frozen=True, slots=True)
class MonitorDefinition:
    monitor_id: str
    version: int
    name: str
    case_id: str | None
    primary_instrument_id: str | None
    cadence: MonitorCadence
    status: MonitorStatus
    rules: tuple[MonitorRule, ...]
    confirmed_by: str
    idempotency_key: str
    created_at: datetime
    trade_plan_id: str | None = None
    trade_plan_version: int | None = None
    valid_until: datetime | None = None
    schema_version: int = MONITORING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _text(self.monitor_id, "monitor_id", 128)
        if type(self.version) is not int or self.version <= 0:
            raise DataContractError("monitor version must be positive")
        _text(self.name, "name", 200)
        if self.case_id is not None:
            _text(self.case_id, "case_id", 128)
        if self.primary_instrument_id is not None:
            parse_instrument_id(self.primary_instrument_id)
        if (self.trade_plan_id is None) != (self.trade_plan_version is None):
            raise DataContractError(
                "trade_plan_id and trade_plan_version must be provided together"
            )
        if self.trade_plan_id is not None:
            _text(self.trade_plan_id, "trade_plan_id", 128)
            if type(self.trade_plan_version) is not int or self.trade_plan_version <= 0:
                raise DataContractError("trade_plan_version must be positive")
        if not isinstance(self.cadence, MonitorCadence) or not isinstance(
            self.status, MonitorStatus
        ):
            raise DataContractError("monitor cadence/status is invalid")
        if not self.rules or len(self.rules) > 50:
            raise DataContractError("monitor requires 1..50 rules")
        keys = [item.rule_code for item in self.rules]
        if len(keys) != len(set(keys)):
            raise DataContractError("monitor rule_code values must be unique")
        expected_market = {
            MonitorCadence.A_SHARE_POST_MARKET: Market.A_SHARE,
            MonitorCadence.US_POST_MARKET: Market.US,
        }.get(self.cadence)
        if expected_market is not None:
            for rule in self.rules:
                if rule.instrument_id is None:
                    continue
                _asset, market, _symbol = parse_instrument_id(rule.instrument_id)
                if market is not expected_market:
                    raise DataContractError(
                        "price rule market must match monitor post-market cadence"
                    )
        if self.confirmed_by not in {"user", "external_agent"}:
            raise DataContractError("confirmed_by is invalid")
        _text(self.idempotency_key, "idempotency_key", 200)
        _aware(self.created_at, "created_at")
        if self.valid_until is not None:
            _aware(self.valid_until, "valid_until")
            if self.valid_until <= self.created_at:
                raise DataContractError("monitor valid_until must follow created_at")
        if self.schema_version != MONITORING_SCHEMA_VERSION:
            raise DataContractError("monitoring schema_version must be 1")


@dataclass(frozen=True, slots=True)
class MonitorRuleState:
    monitor_id: str
    monitor_version: int
    rule_code: str
    state: MonitorRuleStateValue
    observed_value: Decimal | None
    fact_as_of: datetime | None
    message: str
    updated_at: datetime

    def __post_init__(self) -> None:
        _text(self.monitor_id, "monitor_id", 128)
        if type(self.monitor_version) is not int or self.monitor_version <= 0:
            raise DataContractError("monitor_version must be positive")
        _text(self.rule_code, "rule_code", 64)
        if not isinstance(self.state, MonitorRuleStateValue):
            raise DataContractError("monitor rule state is invalid")
        _decimal(self.observed_value, "observed_value")
        if self.fact_as_of is not None:
            _aware(self.fact_as_of, "fact_as_of")
        _text(self.message, "message", 1000)
        _aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    event_id: str
    monitor_id: str
    monitor_version: int
    rule_code: str
    event_type: MonitorEventType
    severity: MonitorSeverity
    observed_value: Decimal | None
    threshold_value: Decimal | None
    fact_as_of: datetime | None
    message: str
    created_at: datetime

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id", 128)
        _text(self.monitor_id, "monitor_id", 128)
        _text(self.rule_code, "rule_code", 64)
        if self.monitor_version <= 0:
            raise DataContractError("monitor_version must be positive")
        if not isinstance(self.event_type, MonitorEventType) or not isinstance(
            self.severity, MonitorSeverity
        ):
            raise DataContractError("monitor event enum is invalid")
        _decimal(self.observed_value, "observed_value")
        _decimal(self.threshold_value, "threshold_value")
        if self.fact_as_of is not None:
            _aware(self.fact_as_of, "fact_as_of")
        _text(self.message, "message", 1000)
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class MonitorEventResolution:
    resolution_id: str
    event_id: str
    action: MonitorEventAction
    note: str
    confirmed_by: str
    idempotency_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        _text(self.resolution_id, "resolution_id", 128)
        _text(self.event_id, "event_id", 128)
        if not isinstance(self.action, MonitorEventAction):
            raise DataContractError("monitor event action is invalid")
        _text(self.note, "note", 2000)
        if self.confirmed_by not in {"user", "external_agent"}:
            raise DataContractError("confirmed_by is invalid")
        _text(self.idempotency_key, "idempotency_key", 200)
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class MonitorRun:
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
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id", 128)
        _codes(self.requested_monitor_ids, "requested_monitor_ids")
        _aware(self.as_of, "as_of")
        _aware(self.started_at, "started_at")
        _aware(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise DataContractError("monitor run completed_at precedes started_at")
        if not isinstance(self.status, MonitorRunStatus):
            raise DataContractError("monitor run status is invalid")
        for value in (self.monitors_evaluated, self.rules_evaluated, self.events_created):
            if type(value) is not int or value < 0:
                raise DataContractError("monitor run counts must be nonnegative ints")
        _codes(self.warning_codes, "warning_codes")
        _codes(self.error_codes, "error_codes")
        if self.execution_effect:
            raise DataContractError("monitor run must not execute")
