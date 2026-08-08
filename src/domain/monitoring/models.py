"""Immutable Monitoring definitions, state transitions, events, and run receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from domain.common.diagnostics import ProviderFailureDiagnostic
from domain.common.enums import Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
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
from domain.notifications.models import NotificationMessage, NotificationOutboxEntry
from domain.risk.enums import RiskOverallStatus
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanFactType,
)

# Compatibility re-exports for Monitor-only imports. Both names point to the
# canonical generic notification domain models; the old duplicate dataclasses
# no longer exist.
MonitorNotificationMessage = NotificationMessage
MonitorNotificationOutboxEntry = NotificationOutboxEntry

MONITORING_SCHEMA_VERSION = 3

# Price rules may target A-share equities, US exchange instruments, CME/DCE
# futures identities, and OTC spot/CFD seeds. Evaluation remains asset-aware:
# DCE has no quote path and stays NOT_EVALUATED without invented settlements.
_PRICE_RULE_MARKETS = frozenset(
    {Market.A_SHARE, Market.US, Market.KR, Market.CME, Market.DCE, Market.OTC}
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
class MonitorJudgmentPolicy:
    """Versioned LLM judgment policy; confirmed execution state stays immutable."""

    playbook: str
    reference_instrument_ids: tuple[str, ...]
    relative_strength_pairs: tuple[tuple[str, str, str], ...] = ()
    confirmed_state_json: str = "{}"
    prompt_version: str = "monitor-judgment-v1"

    def __post_init__(self) -> None:
        _text(self.playbook, "judgment playbook", 16000)
        if not 1 <= len(self.reference_instrument_ids) <= 12:
            raise DataContractError("judgment policy requires 1..12 reference instruments")
        _codes(self.reference_instrument_ids, "reference_instrument_ids")
        for instrument_id in self.reference_instrument_ids:
            parse_instrument_id(instrument_id)
        if len(self.relative_strength_pairs) > 12:
            raise DataContractError("judgment policy supports at most 12 relative-strength pairs")
        pair_names: list[str] = []
        for name, numerator, denominator in self.relative_strength_pairs:
            pair_names.append(_text(name, "relative strength pair name", 64))
            if numerator not in self.reference_instrument_ids:
                raise DataContractError("relative-strength numerator is not a reference instrument")
            if denominator not in self.reference_instrument_ids:
                raise DataContractError(
                    "relative-strength denominator is not a reference instrument"
                )
        if len(pair_names) != len(set(pair_names)):
            raise DataContractError("relative-strength pair names must be unique")
        try:
            state = json.loads(self.confirmed_state_json)
        except json.JSONDecodeError as exc:
            raise DataContractError("confirmed_state_json must be valid JSON") from exc
        if not isinstance(state, dict) or len(state) > 50:
            raise DataContractError("confirmed state must be a bounded object")
        if any(not isinstance(key, str) or not key.strip() for key in state):
            raise DataContractError("confirmed state keys must be non-blank strings")
        if any(
            not isinstance(value, (str, int, float, bool)) and value is not None
            for value in state.values()
        ):
            raise DataContractError("confirmed state values must be JSON scalars")
        if len(self.confirmed_state_json) > 8000:
            raise DataContractError("confirmed state is too large")
        _text(self.prompt_version, "prompt_version", 64)


@dataclass(frozen=True, slots=True)
class MonitorRule:
    rule_code: str
    rule_type: MonitorRuleType
    severity: MonitorSeverity
    instrument_id: str | None
    price_threshold: Decimal | None
    risk_status_threshold: RiskOverallStatus | None
    max_fact_age_seconds: int
    description: str | None = None
    fact_type: TradePlanFactType | None = None
    metric_key: str | None = None
    comparator: TradePlanComparator | None = None
    numeric_threshold: Decimal | None = None
    recovery_threshold: Decimal | None = None
    technical_interval: Literal["1d", "1w"] | None = None
    event_after: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.rule_code, "rule_code", 64)
        if not isinstance(self.rule_type, MonitorRuleType):
            raise DataContractError("rule_type is invalid")
        if not isinstance(self.severity, MonitorSeverity):
            raise DataContractError("severity is invalid")
        if type(self.max_fact_age_seconds) is not int or self.max_fact_age_seconds <= 0:
            raise DataContractError("max_fact_age_seconds must be positive int")
        if self.description is not None:
            _text(self.description, "description", 500)
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
                    "price rule market must be A_SHARE, US, KR, CME, DCE, or OTC",
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
                    self.recovery_threshold,
                    self.technical_interval,
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
                    self.recovery_threshold,
                    self.technical_interval,
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
                if self.numeric_threshold is not None or self.recovery_threshold is not None:
                    raise DataContractError(
                        "OCCURRED rule cannot set numeric or recovery threshold"
                    )
            else:
                if self.numeric_threshold is None:
                    raise DataContractError("numeric fact rule requires numeric_threshold")
                _decimal(self.numeric_threshold, "numeric_threshold")
                if self.recovery_threshold is not None:
                    _decimal(self.recovery_threshold, "recovery_threshold")
                    if self.comparator is TradePlanComparator.EQ:
                        raise DataContractError("EQ rule cannot set recovery_threshold")
                    if (
                        self.comparator
                        in {
                            TradePlanComparator.GT,
                            TradePlanComparator.GTE,
                        }
                        and self.recovery_threshold >= self.numeric_threshold
                    ):
                        raise DataContractError(
                            "upper trigger recovery_threshold must be below numeric_threshold"
                        )
                    if (
                        self.comparator
                        in {
                            TradePlanComparator.LT,
                            TradePlanComparator.LTE,
                        }
                        and self.recovery_threshold <= self.numeric_threshold
                    ):
                        raise DataContractError(
                            "lower trigger recovery_threshold must be above numeric_threshold"
                        )
            if self.technical_interval is not None:
                if self.fact_type is not TradePlanFactType.TECHNICAL:
                    raise DataContractError("technical_interval is only valid for TECHNICAL rules")
                if self.technical_interval not in {"1d", "1w"}:
                    raise DataContractError("technical_interval must be 1d or 1w")
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
    subject_id: str | None
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
    interval_minutes: int | None = None
    judgment_policy: MonitorJudgmentPolicy | None = None
    schema_version: int = MONITORING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _text(self.monitor_id, "monitor_id", 128)
        if type(self.version) is not int or self.version <= 0:
            raise DataContractError("monitor version must be positive")
        _text(self.name, "name", 200)
        if self.subject_id is not None:
            _text(self.subject_id, "subject_id", 128)
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
        if self.cadence is MonitorCadence.INTERVAL:
            if (
                type(self.interval_minutes) is not int
                or self.interval_minutes < 60
                or self.interval_minutes % 60 != 0
            ):
                raise DataContractError(
                    "INTERVAL monitor requires whole-hour interval_minutes >= 60"
                )
        elif self.interval_minutes is not None:
            raise DataContractError("interval_minutes is only valid for INTERVAL cadence")
        if not self.rules or len(self.rules) > 50:
            raise DataContractError("monitor requires 1..50 rules")
        keys = [item.rule_code for item in self.rules]
        if len(keys) != len(set(keys)):
            raise DataContractError("monitor rule_code values must be unique")
        expected_market = {
            MonitorCadence.A_SHARE_POST_MARKET: Market.A_SHARE,
            MonitorCadence.US_POST_MARKET: Market.US,
            MonitorCadence.KR_POST_MARKET: Market.KR,
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
        if self.schema_version not in {1, 2, MONITORING_SCHEMA_VERSION}:
            raise DataContractError("monitoring schema_version must be 1, 2, or 3")
        if self.cadence is MonitorCadence.INTERVAL and self.schema_version < 2:
            raise DataContractError("INTERVAL monitor requires monitoring schema_version 2")
        if self.judgment_policy is not None and self.schema_version < 3:
            raise DataContractError("judgment policy requires monitoring schema_version 3")


@dataclass(frozen=True, slots=True)
class MonitorJudgment:
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

    def __post_init__(self) -> None:
        _text(self.judgment_id, "judgment_id", 128)
        _text(self.run_id, "run_id", 128)
        _text(self.monitor_id, "monitor_id", 128)
        if self.monitor_version <= 0:
            raise DataContractError("monitor_version must be positive")
        if self.status not in {"SUCCEEDED", "SKIPPED", "FAILED"}:
            raise DataContractError("judgment status is invalid")
        if self.conclusion is not None and not isinstance(
            self.conclusion, MonitorJudgmentConclusion
        ):
            raise DataContractError("judgment conclusion is invalid")
        if (self.quantity_min is None) != (self.quantity_max is None):
            raise DataContractError("judgment quantity bounds must be provided together")
        if self.quantity_min is not None:
            assert self.quantity_max is not None
            if self.quantity_min < 0 or self.quantity_max < self.quantity_min:
                raise DataContractError("judgment quantity bounds are invalid")
        _text(self.summary, "judgment summary", 1000)
        if len(self.evidence_feature_ids) > 3:
            raise DataContractError("judgment supports at most three evidence features")
        _codes(self.evidence_feature_ids, "evidence_feature_ids")
        for field, value, limit in (
            ("phase", self.phase, 100),
            ("market_state", self.market_state, 500),
            ("next_trigger", self.next_trigger, 500),
            ("invalidation", self.invalidation, 500),
        ):
            if value is not None:
                _text(value, field, limit)
        _text(self.feature_signature, "feature_signature", 128)
        if self.result_fingerprint is not None:
            _text(self.result_fingerprint, "result_fingerprint", 128)
        for field, value in (
            ("provider", self.provider),
            ("model", self.model),
            ("reasoning_effort", self.reasoning_effort),
            ("prompt_version", self.prompt_version),
        ):
            _text(value, field, 128)
        _codes(self.warning_codes, "warning_codes")
        _codes(self.error_codes, "error_codes")
        if len(self.web_source_urls) > 10:
            raise DataContractError("judgment supports at most ten web sources")
        for url in self.web_source_urls:
            _text(url, "web_source_url", 2000)
            if urlsplit(url).scheme not in {"http", "https"}:
                raise DataContractError("judgment web source URL is invalid")
        _aware(self.created_at, "created_at")


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
class MonitorRunObservation:
    run_id: str
    monitor_id: str
    monitor_version: int
    rule_code: str
    instrument_id: str | None
    severity: MonitorSeverity
    state: MonitorRuleStateValue
    observed_value: Decimal | None
    threshold_value: Decimal | None
    distance_value: Decimal | None
    distance_percent: Decimal | None
    fact_as_of: datetime | None
    fact_age_seconds: int | None
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    message: str
    diagnostics: tuple[ProviderFailureDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id", 128)
        _text(self.monitor_id, "monitor_id", 128)
        if type(self.monitor_version) is not int or self.monitor_version <= 0:
            raise DataContractError("monitor_version must be positive")
        _text(self.rule_code, "rule_code", 64)
        if self.instrument_id is not None:
            parse_instrument_id(self.instrument_id)
        if not isinstance(self.severity, MonitorSeverity):
            raise DataContractError("monitor observation severity is invalid")
        if not isinstance(self.state, MonitorRuleStateValue):
            raise DataContractError("monitor observation state is invalid")
        for field, value in (
            ("observed_value", self.observed_value),
            ("threshold_value", self.threshold_value),
            ("distance_value", self.distance_value),
            ("distance_percent", self.distance_percent),
        ):
            _decimal(value, field)
        if self.fact_as_of is not None:
            _aware(self.fact_as_of, "fact_as_of")
        if self.fact_age_seconds is not None and (
            type(self.fact_age_seconds) is not int or self.fact_age_seconds < 0
        ):
            raise DataContractError("fact_age_seconds must be a nonnegative int")
        _codes(self.warning_codes, "warning_codes")
        _codes(self.error_codes, "error_codes")
        _text(self.message, "message", 1000)
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, ProviderFailureDiagnostic) for item in self.diagnostics
        ):
            raise DataContractError(
                "monitor observation diagnostics must contain ProviderFailureDiagnostic values"
            )


@dataclass(frozen=True, slots=True)
class MonitorRun:
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
    observations: tuple[MonitorRunObservation, ...] = ()
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id", 128)
        _codes(self.requested_monitor_ids, "requested_monitor_ids")
        _codes(self.selected_monitor_ids, "selected_monitor_ids")
        if self.cadence is not None and not isinstance(self.cadence, MonitorCadence):
            raise DataContractError("monitor run cadence is invalid")
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
        if type(self.observation_history_complete) is not bool:
            raise DataContractError("observation_history_complete must be bool")
        if self.observation_history_complete and len(self.observations) != self.rules_evaluated:
            raise DataContractError("monitor run observations must match rules_evaluated")
        if any(item.run_id != self.run_id for item in self.observations):
            raise DataContractError("monitor observation run_id mismatch")
        if self.execution_effect:
            raise DataContractError("monitor run must not execute")
