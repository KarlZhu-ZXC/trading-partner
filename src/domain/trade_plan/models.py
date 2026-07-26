"""Immutable Trade Plan identities, versions, and conditions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)

TRADE_PLAN_SCHEMA_VERSION = 1
_UUID7 = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_PLAN_ID_RE = re.compile(rf"^trade_plan_{_UUID7}$")
_CASE_ID_RE = re.compile(rf"^case_{_UUID7}$")
_THESIS_ID_RE = re.compile(rf"^thesis_{_UUID7}$")


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{field} must be non-blank text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return normalized


def _optional_text(value: str | None, field: str, maximum: int) -> str | None:
    return None if value is None else _text(value, field, maximum)


def _decimal(
    value: Decimal | None,
    field: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> None:
    if value is None:
        return
    if type(value) is not Decimal or not value.is_finite():
        raise DataContractError(f"{field} must be a finite Decimal")
    if minimum is not None and value < minimum:
        raise DataContractError(f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise DataContractError(f"{field} must be <= {maximum}")


@dataclass(frozen=True, slots=True)
class TradePlanCondition:
    condition_code: str
    phase: TradePlanConditionPhase
    mode: TradePlanConditionMode
    description: str
    severity: str
    fact_type: TradePlanFactType | None = None
    metric_key: str | None = None
    comparator: TradePlanComparator | None = None
    threshold: Decimal | None = None
    unit: str | None = None
    instrument_id: str | None = None
    max_fact_age_seconds: int | None = None
    event_after: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.condition_code, "condition_code", 64)
        if not isinstance(self.phase, TradePlanConditionPhase):
            raise DataContractError("condition phase is invalid")
        if not isinstance(self.mode, TradePlanConditionMode):
            raise DataContractError("condition mode is invalid")
        _text(self.description, "description", 2000)
        if self.severity not in {"INFO", "MEDIUM", "HIGH"}:
            raise DataContractError("condition severity is invalid")
        if self.instrument_id is not None:
            parse_instrument_id(self.instrument_id)
        if self.event_after is not None:
            require_aware_datetime(self.event_after, field_name="event_after")
        _decimal(self.threshold, "threshold")

        if self.mode is TradePlanConditionMode.MANUAL:
            machine_fields = (
                self.fact_type,
                self.metric_key,
                self.comparator,
                self.threshold,
                self.unit,
                self.instrument_id,
                self.max_fact_age_seconds,
                self.event_after,
            )
            if any(value is not None for value in machine_fields):
                raise DataContractError("MANUAL condition cannot set machine fact fields")
            return

        if not isinstance(self.fact_type, TradePlanFactType):
            raise DataContractError("MONITORABLE condition requires fact_type")
        metric = _optional_text(self.metric_key, "metric_key", 128)
        if metric is None:
            raise DataContractError("MONITORABLE condition requires metric_key")
        if not isinstance(self.comparator, TradePlanComparator):
            raise DataContractError("MONITORABLE condition requires comparator")
        if type(self.max_fact_age_seconds) is not int or self.max_fact_age_seconds <= 0:
            raise DataContractError(
                "MONITORABLE condition requires positive max_fact_age_seconds"
            )
        numeric = self.comparator is not TradePlanComparator.OCCURRED
        if numeric and self.threshold is None:
            raise DataContractError("numeric condition requires threshold")
        if not numeric and self.threshold is not None:
            raise DataContractError("OCCURRED condition cannot set threshold")
        if self.fact_type in {
            TradePlanFactType.PRICE,
            TradePlanFactType.VOLUME,
            TradePlanFactType.TECHNICAL,
            TradePlanFactType.FUNDAMENTAL,
            TradePlanFactType.COMPANY_EVENT,
            TradePlanFactType.SENTIMENT,
        } and self.instrument_id is None:
            raise DataContractError(f"{self.fact_type.value} condition requires instrument_id")
        if self.fact_type is TradePlanFactType.COMPANY_EVENT and (
            self.comparator is not TradePlanComparator.OCCURRED
        ):
            raise DataContractError("COMPANY_EVENT condition requires OCCURRED comparator")


@dataclass(frozen=True, slots=True)
class TradePlan:
    plan_id: str
    version: int
    case_id: str
    thesis_id: str
    instrument_id: str
    status: TradePlanStatus
    valid_from: datetime
    valid_until: datetime | None
    currency: str
    reference_price: Decimal
    reference_price_at: datetime
    target_position_percent: Decimal
    max_position_percent: Decimal
    risk_budget_percent: Decimal
    stop_price: Decimal | None
    conditions: tuple[TradePlanCondition, ...]
    notes: str
    confirmed_by: str
    created_at: datetime
    idempotency_key: str
    schema_version: int = TRADE_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not _PLAN_ID_RE.fullmatch(self.plan_id):
            raise DataContractError("plan_id must match trade_plan_<uuid7>")
        if type(self.version) is not int or self.version <= 0:
            raise DataContractError("trade plan version must be positive")
        if not _CASE_ID_RE.fullmatch(self.case_id):
            raise DataContractError("case_id must match case_<uuid7>")
        if not _THESIS_ID_RE.fullmatch(self.thesis_id):
            raise DataContractError("thesis_id must match thesis_<uuid7>")
        parse_instrument_id(self.instrument_id)
        if not isinstance(self.status, TradePlanStatus):
            raise DataContractError("trade plan status is invalid")
        require_aware_datetime(self.valid_from, field_name="valid_from")
        require_aware_datetime(self.reference_price_at, field_name="reference_price_at")
        require_aware_datetime(self.created_at, field_name="created_at")
        if self.valid_until is not None:
            require_aware_datetime(self.valid_until, field_name="valid_until")
            if self.valid_until <= self.valid_from:
                raise DataContractError("valid_until must follow valid_from")
        currency = _text(self.currency, "currency", 16).upper()
        object.__setattr__(self, "currency", currency)
        _decimal(self.reference_price, "reference_price", minimum=Decimal("0.00000001"))
        for name, value in (
            ("target_position_percent", self.target_position_percent),
            ("max_position_percent", self.max_position_percent),
            ("risk_budget_percent", self.risk_budget_percent),
        ):
            _decimal(value, name, minimum=Decimal("0"), maximum=Decimal("100"))
        if self.target_position_percent > self.max_position_percent:
            raise DataContractError(
                "target_position_percent must not exceed max_position_percent"
            )
        _decimal(self.stop_price, "stop_price", minimum=Decimal("0.00000001"))
        if not isinstance(self.conditions, tuple) or len(self.conditions) > 100:
            raise DataContractError("conditions must be a tuple with at most 100 items")
        codes = [item.condition_code for item in self.conditions]
        if len(codes) != len(set(codes)):
            raise DataContractError("condition_code values must be unique")
        _text(self.notes, "notes", 8000)
        if self.confirmed_by not in {"user", "external_agent"}:
            raise DataContractError("confirmed_by is invalid")
        _text(self.idempotency_key, "idempotency_key", 200)
        if self.schema_version != TRADE_PLAN_SCHEMA_VERSION:
            raise DataContractError("trade plan schema_version must be 1")
        if self.status is TradePlanStatus.ACTIVE and not self.conditions:
            raise DataContractError("ACTIVE trade plan requires at least one condition")
