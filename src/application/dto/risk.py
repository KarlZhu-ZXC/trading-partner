"""Closed Phase 2B Portfolio Risk Engine request and response DTOs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.common.enums import VendorId
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.risk.models import RiskCheckResult, RiskPolicy


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class RiskPolicyUpdateInput(_DTO):
    single_position_max_percent: Decimal = Field(ge=0, le=100)
    gross_exposure_max_percent: Decimal = Field(gt=0, le=1000)
    minimum_cash_percent: Decimal = Field(ge=0, le=100)
    margin_usage_max_percent: Decimal = Field(ge=0, le=1000)
    max_account_age_seconds: int = Field(gt=0)
    max_price_age_seconds: int = Field(gt=0)
    expected_version: int = Field(ge=1)
    confirmed_by: Literal["user", "external_agent"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    risk_budget_max_percent: Decimal = Field(default=Decimal("2"), ge=0, le=100)
    theme_exposure_max_percent: Decimal = Field(default=Decimal("40"), ge=0, le=100)
    drawdown_max_percent: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    liquidity_participation_max_percent: Decimal = Field(
        default=Decimal("10"), ge=0, le=100
    )
    correlation_max_absolute: Decimal = Field(default=Decimal("0.85"), ge=0, le=1)
    event_blackout_days: int = Field(default=3, ge=0, le=365)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def normalize_key(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("idempotency_key must not be blank")
        return value


class RiskCheckInput(_DTO):
    account_snapshot_ids: tuple[str, ...] = ()
    refresh_accounts: bool = False
    providers: tuple[VendorId, ...] = ()
    hypothetical_instrument_id: str | None = None
    hypothetical_quantity: Decimal | None = Field(default=None, gt=0)
    hypothetical_assumed_price: Decimal | None = Field(default=None, gt=0)
    hypothetical_currency: str | None = Field(default=None, min_length=1, max_length=16)
    trade_plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    average_daily_value: Decimal | None = Field(default=None, gt=0)
    max_liquidity_participation_percent: Decimal | None = Field(
        default=None, gt=0, le=100
    )
    atr: Decimal | None = Field(default=None, gt=0)
    target_volatility_percent: Decimal | None = Field(default=None, gt=0, le=100)
    annualized_volatility_percent: Decimal | None = Field(default=None, gt=0)
    as_of: datetime | None = None

    @model_validator(mode="after")
    def validate_combination(self) -> Self:
        if self.providers and not self.refresh_accounts:
            raise ValueError("providers requires refresh_accounts=true")
        if self.refresh_accounts and self.account_snapshot_ids:
            raise ValueError("refresh_accounts cannot be combined with snapshot ids")
        hypothetical = (
            self.hypothetical_instrument_id,
            self.hypothetical_quantity,
            self.hypothetical_assumed_price,
            self.hypothetical_currency,
        )
        if any(value is not None for value in hypothetical) and not all(
            value is not None for value in hypothetical
        ):
            raise ValueError("hypothetical fields must be provided together")
        if self.hypothetical_instrument_id is not None:
            parse_instrument_id(self.hypothetical_instrument_id)
        if self.trade_plan_id is not None and any(value is not None for value in hypothetical):
            raise ValueError("trade_plan_id cannot be combined with manual hypothetical fields")
        if (self.average_daily_value is None) != (
            self.max_liquidity_participation_percent is None
        ):
            raise ValueError(
                "average_daily_value and max_liquidity_participation_percent are required together"
            )
        volatility = (
            self.target_volatility_percent,
            self.annualized_volatility_percent,
        )
        if any(value is not None for value in volatility) and not all(
            value is not None for value in volatility
        ):
            raise ValueError("target and annualized volatility must be provided together")
        if self.as_of is not None:
            require_aware_datetime(self.as_of, field_name="as_of")
        return self


class RiskPolicyDTO(_DTO):
    policy_id: str
    version: int
    single_position_max_percent: DecimalWire
    gross_exposure_max_percent: DecimalWire
    minimum_cash_percent: DecimalWire
    margin_usage_max_percent: DecimalWire
    max_account_age_seconds: int
    max_price_age_seconds: int
    is_system_default: bool
    confirmed_by: str
    created_at: datetime
    idempotency_key: str
    risk_budget_max_percent: DecimalWire
    theme_exposure_max_percent: DecimalWire
    drawdown_max_percent: DecimalWire
    liquidity_participation_max_percent: DecimalWire
    correlation_max_absolute: DecimalWire
    event_blackout_days: int
    schema_version: int

    @classmethod
    def from_domain(cls, value: RiskPolicy) -> RiskPolicyDTO:
        return cls.model_validate(value)


class RiskHypotheticalAdditionDTO(_DTO):
    instrument_id: str
    quantity: DecimalWire
    assumed_price: DecimalWire
    currency: str


class RiskCheckDTO(_DTO):
    rule_code: str
    status: str
    severity: str
    actual: DecimalWire | int | None
    limit: DecimalWire | int | None
    unit: str
    scope: str
    message: str


class PositionSizingConstraintDTO(_DTO):
    constraint_code: str
    status: str
    max_quantity: DecimalWire | None
    limiting_value: DecimalWire | int | None
    unit: str
    message: str


class PositionSizingResultDTO(_DTO):
    plan_id: str
    plan_version: int
    instrument_id: str
    currency: str
    reference_price: DecimalWire
    reference_price_at: datetime
    current_quantity: DecimalWire
    lot_size: DecimalWire
    target_total_quantity: DecimalWire | None
    max_total_quantity: DecimalWire | None
    recommended_min_additional_quantity: DecimalWire | None
    recommended_max_additional_quantity: DecimalWire | None
    estimated_max_loss: DecimalWire | None
    constraints: tuple[PositionSizingConstraintDTO, ...]
    data_quality_codes: tuple[str, ...]
    historically_validated: bool
    execution_effect: bool


class RiskCheckResultDTO(_DTO):
    policy: RiskPolicyDTO
    account_snapshot_ids: tuple[str, ...]
    as_of: datetime
    checks: tuple[RiskCheckDTO, ...]
    data_quality_codes: tuple[str, ...]
    hypothetical: RiskHypotheticalAdditionDTO | None
    overall_status: str
    position_sizing: PositionSizingResultDTO | None
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: RiskCheckResult) -> RiskCheckResultDTO:
        return cls.model_validate(value)
