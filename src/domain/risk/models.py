"""Risk policy, check, and hypothesis models for Phase 2B."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.risk.enums import RiskCheckStatus, RiskConfirmer, RiskOverallStatus, RiskSeverity

RISK_POLICY_SCHEMA_VERSION = 1


_UUID7_TOKEN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_RISK_POLICY_ID_RE = re.compile(
    rf"^{re.escape(EntityIdPrefix.RISK_POLICY.value)}_{_UUID7_TOKEN}$"
)


def _require_entity_id(value: object, *, field: str) -> None:
    if not isinstance(value, str) or not _RISK_POLICY_ID_RE.fullmatch(value):
        raise DataContractError(
            f"{field} must match risk_policy_<uuid7>",
            details={"field": field, "value": value},
        )


def _require_text(
    value: object,
    *,
    field: str,
    maximum: int,
    minimum: int = 1,
) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be text",
            details={"field": field, "type": type(value).__name__},
        )
    normalized = value.strip()
    if len(normalized) < minimum:
        raise DataContractError(
            f"{field} must be non-empty",
            details={"field": field},
        )
    if len(normalized) > maximum:
        raise DataContractError(
            f"{field} length must be <= {maximum}",
            details={"field": field, "max": maximum, "length": len(normalized)},
        )
    return normalized


def _require_decimal(
    value: object,
    *,
    field: str,
    minimum: Decimal | int,
    maximum: Decimal | int,
    allow_zero: bool = False,
) -> Decimal:
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be finite",
            details={"field": field},
        )
    lower = Decimal(str(minimum))
    upper = Decimal(str(maximum))
    if not allow_zero and value == 0:
        raise DataContractError(
            f"{field} must not be 0",
            details={"field": field, "value": str(value)},
        )
    if value < lower or value > upper:
        raise DataContractError(
            f"{field} must be between {minimum} and {maximum}",
            details={"field": field, "value": str(value)},
        )
    return value


def _require_int(*, value: object, field: str, minimum: int = 1) -> int:
    if type(value) is not int:
        raise DataContractError(
            f"{field} must be int",
            details={"field": field, "type": type(value).__name__},
        )
    if value < minimum:
        raise DataContractError(
            f"{field} must be >={minimum}",
            details={"field": field, "value": value},
        )
    return value


def _require_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise DataContractError(
            f"{field} must be bool",
            details={"field": field, "type": type(value).__name__},
        )
    return value


def _require_schema_version(value: object) -> None:
    if type(value) is not int or value != RISK_POLICY_SCHEMA_VERSION:
        raise DataContractError(
            "schema_version must be 1",
            details={
                "field": "schema_version",
                "value": value,
                "expected": RISK_POLICY_SCHEMA_VERSION,
            },
        )


def _require_amount(
    value: object,
    *,
    field: str,
    minimum: Decimal | int = 0,
    allow_zero: bool = True,
) -> Decimal | int:
    if type(value) is int:
        if value < minimum:
            raise DataContractError(
                f"{field} must be >= {minimum}",
                details={"field": field, "value": value, "minimum": minimum},
            )
        return value
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal or int",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be finite",
            details={"field": field},
        )
    if not allow_zero and value == 0:
        raise DataContractError(
            f"{field} must not be 0",
            details={"field": field, "value": str(value)},
        )
    if value < Decimal(str(minimum)):
        raise DataContractError(
            f"{field} must be >= {minimum}",
            details={"field": field, "value": str(value), "minimum": str(minimum)},
        )
    return value


def _require_signed_amount(value: object, *, field: str) -> Decimal | int:
    """Validate a finite numeric observation that may legitimately be negative."""

    if type(value) is int:
        return value
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal or int",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be finite",
            details={"field": field},
        )
    return value


def _require_positive_decimal(value: object, *, field: str) -> Decimal:
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={"field": field, "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be finite",
            details={"field": field},
        )
    if value <= 0:
        raise DataContractError(
            f"{field} must be > 0",
            details={"field": field, "value": str(value)},
        )
    return value


def _ensure_codes(value: tuple[str, ...], *, field: str, unique: bool = True) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise DataContractError(f"{field} must be a tuple")
    normalized: list[str] = []
    for item in value:
        normalized.append(_require_text(item, field=field, minimum=1, maximum=64))
    if unique and len(normalized) != len(set(normalized)):
        raise DataContractError(
            f"{field} values must be unique",
            details={"field": field},
        )
    return tuple(normalized)


def _ensure_tuple_unique_rules(checks: tuple[RiskCheck, ...]) -> None:
    seen: set[tuple[str, str]] = set()
    for item in checks:
        key = (item.rule_code, item.scope)
        if key in seen:
            raise DataContractError(
                "risk check rule_code and scope must be unique",
                details={"rule_code": item.rule_code, "scope": item.scope},
            )
        seen.add(key)


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    policy_id: str
    version: int
    single_position_max_percent: Decimal
    gross_exposure_max_percent: Decimal
    minimum_cash_percent: Decimal
    margin_usage_max_percent: Decimal
    max_account_age_seconds: int
    max_price_age_seconds: int
    is_system_default: bool
    confirmed_by: RiskConfirmer
    created_at: datetime
    idempotency_key: str
    risk_budget_max_percent: Decimal = Decimal("2")
    theme_exposure_max_percent: Decimal = Decimal("40")
    drawdown_max_percent: Decimal = Decimal("20")
    liquidity_participation_max_percent: Decimal = Decimal("10")
    correlation_max_absolute: Decimal = Decimal("0.85")
    event_blackout_days: int = 3
    schema_version: int = RISK_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_entity_id(self.policy_id, field="policy_id")
        _require_int(value=self.version, field="version")
        _require_decimal(
            self.single_position_max_percent,
            field="single_position_max_percent",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
            allow_zero=True,
        )
        _require_decimal(
            self.gross_exposure_max_percent,
            field="gross_exposure_max_percent",
            minimum=Decimal("0.0000000001"),
            maximum=Decimal("1000"),
            allow_zero=False,
        )
        _require_decimal(
            self.minimum_cash_percent,
            field="minimum_cash_percent",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
            allow_zero=True,
        )
        _require_decimal(
            self.margin_usage_max_percent,
            field="margin_usage_max_percent",
            minimum=Decimal("0"),
            maximum=Decimal("1000"),
            allow_zero=True,
        )
        _require_int(
            value=self.max_account_age_seconds,
            field="max_account_age_seconds",
            minimum=1,
        )
        _require_int(
            value=self.max_price_age_seconds,
            field="max_price_age_seconds",
            minimum=1,
        )
        _require_bool(self.is_system_default, field="is_system_default")
        if not isinstance(self.confirmed_by, RiskConfirmer):
            raise DataContractError(
                "confirmed_by must be RiskConfirmer",
                details={"field": "confirmed_by", "value": self.confirmed_by},
            )
        require_aware_datetime(self.created_at, field_name="created_at")
        _require_text(self.idempotency_key, field="idempotency_key", maximum=256)
        for field, value, maximum in (
            ("risk_budget_max_percent", self.risk_budget_max_percent, Decimal("100")),
            (
                "theme_exposure_max_percent",
                self.theme_exposure_max_percent,
                Decimal("100"),
            ),
            ("drawdown_max_percent", self.drawdown_max_percent, Decimal("100")),
            (
                "liquidity_participation_max_percent",
                self.liquidity_participation_max_percent,
                Decimal("100"),
            ),
            ("correlation_max_absolute", self.correlation_max_absolute, Decimal("1")),
        ):
            _require_decimal(
                value,
                field=field,
                minimum=Decimal("0"),
                maximum=maximum,
                allow_zero=True,
            )
        _require_int(value=self.event_blackout_days, field="event_blackout_days", minimum=0)
        _require_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class RiskCheck:
    rule_code: str
    status: RiskCheckStatus
    severity: RiskSeverity
    actual: Decimal | int | None
    limit: Decimal | int | None
    unit: str
    scope: str
    message: str

    def __post_init__(self) -> None:
        _require_text(self.rule_code, field="rule_code", maximum=128)
        if not isinstance(self.status, RiskCheckStatus):
            raise DataContractError("status must be RiskCheckStatus")
        if not isinstance(self.severity, RiskSeverity):
            raise DataContractError("severity must be RiskSeverity")
        if self.actual is not None:
            _require_signed_amount(self.actual, field="actual")
        if self.limit is not None:
            _require_amount(self.limit, field="limit")
        _require_text(self.unit, field="unit", maximum=64)
        _require_text(self.scope, field="scope", maximum=128)
        _require_text(self.message, field="message", maximum=4000)


@dataclass(frozen=True, slots=True)
class RiskHypotheticalAddition:
    instrument_id: str
    quantity: Decimal
    assumed_price: Decimal
    currency: str

    def __post_init__(self) -> None:
        parse_instrument_id(self.instrument_id)
        _require_positive_decimal(self.quantity, field="quantity")
        _require_positive_decimal(self.assumed_price, field="assumed_price")
        currency = _require_text(self.currency, field="currency", minimum=1, maximum=16)
        object.__setattr__(self, "currency", currency.upper())

    @property
    def value(self) -> Decimal | None:
        try:
            return self.quantity * self.assumed_price
        except (ArithmeticError, TypeError):
            return None


@dataclass(frozen=True, slots=True)
class PositionSizingConstraint:
    constraint_code: str
    status: RiskCheckStatus
    max_quantity: Decimal | None
    limiting_value: Decimal | None
    unit: str
    message: str

    def __post_init__(self) -> None:
        _require_text(self.constraint_code, field="constraint_code", maximum=128)
        if not isinstance(self.status, RiskCheckStatus):
            raise DataContractError("position sizing constraint status is invalid")
        if self.status is RiskCheckStatus.NOT_EVALUATED and self.max_quantity is not None:
            raise DataContractError("NOT_EVALUATED sizing constraint cannot set max_quantity")
        if self.max_quantity is not None:
            _require_amount(self.max_quantity, field="max_quantity")
        if self.limiting_value is not None:
            _require_amount(self.limiting_value, field="limiting_value")
        _require_text(self.unit, field="unit", maximum=64)
        _require_text(self.message, field="message", maximum=2000)


@dataclass(frozen=True, slots=True)
class PositionSizingResult:
    plan_id: str
    plan_version: int
    instrument_id: str
    currency: str
    reference_price: Decimal
    reference_price_at: datetime
    current_quantity: Decimal
    lot_size: Decimal
    target_total_quantity: Decimal | None
    max_total_quantity: Decimal | None
    recommended_min_additional_quantity: Decimal | None
    recommended_max_additional_quantity: Decimal | None
    estimated_max_loss: Decimal | None
    constraints: tuple[PositionSizingConstraint, ...]
    data_quality_codes: tuple[str, ...]
    historically_validated: bool = False
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _require_text(self.plan_id, field="plan_id", maximum=128)
        _require_int(value=self.plan_version, field="plan_version")
        parse_instrument_id(self.instrument_id)
        _require_text(self.currency, field="currency", maximum=16)
        _require_positive_decimal(self.reference_price, field="reference_price")
        require_aware_datetime(self.reference_price_at, field_name="reference_price_at")
        _require_amount(self.current_quantity, field="current_quantity")
        _require_positive_decimal(self.lot_size, field="lot_size")
        for field, value in (
            ("target_total_quantity", self.target_total_quantity),
            ("max_total_quantity", self.max_total_quantity),
            (
                "recommended_min_additional_quantity",
                self.recommended_min_additional_quantity,
            ),
            (
                "recommended_max_additional_quantity",
                self.recommended_max_additional_quantity,
            ),
            ("estimated_max_loss", self.estimated_max_loss),
        ):
            if value is not None:
                _require_amount(value, field=field)
        if not isinstance(self.constraints, tuple):
            raise DataContractError("position sizing constraints must be a tuple")
        _ensure_codes(self.data_quality_codes, field="data_quality_codes", unique=True)
        if self.historically_validated or self.execution_effect:
            raise DataContractError("position sizing cannot claim validation or execution")


@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    policy: RiskPolicy
    account_snapshot_ids: tuple[str, ...]
    as_of: datetime
    checks: tuple[RiskCheck, ...]
    data_quality_codes: tuple[str, ...]
    hypothetical: RiskHypotheticalAddition | None
    overall_status: RiskOverallStatus
    position_sizing: PositionSizingResult | None = None
    execution_effect: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy, RiskPolicy):
            raise DataContractError("policy must be a RiskPolicy")
        if not isinstance(self.account_snapshot_ids, tuple):
            raise DataContractError("account_snapshot_ids must be a tuple")
        for value in self.account_snapshot_ids:
            _require_text(value, field="account_snapshot_ids", minimum=1, maximum=256)
        require_aware_datetime(self.as_of, field_name="as_of")
        if not isinstance(self.checks, tuple):
            raise DataContractError("checks must be a tuple")
        _ensure_tuple_unique_rules(self.checks)
        _ensure_codes(self.data_quality_codes, field="data_quality_codes", unique=True)
        if type(self.overall_status) is not RiskOverallStatus:
            raise DataContractError("overall_status must be RiskOverallStatus")
        if self.position_sizing is not None and not isinstance(
            self.position_sizing, PositionSizingResult
        ):
            raise DataContractError("position_sizing must be PositionSizingResult")
        _require_bool(self.execution_effect, field="execution_effect")
        if self.execution_effect:
            raise DataContractError(
                "risk result must not cause execution",
                details={"execution_effect": self.execution_effect},
            )
