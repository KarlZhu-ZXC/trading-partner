"""Closed contracts for durable native-currency performance attribution."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.attribution.enums import AttributionStatus, CostBasisMethod
from domain.attribution.models import (
    AccountPerformance,
    InstrumentPerformance,
    PerformanceAttribution,
)
from domain.common.enums import VendorId


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class PerformanceAttributionInput(_DTO):
    start: datetime
    end: datetime
    cost_basis_method: CostBasisMethod = CostBasisMethod.FIFO
    providers: tuple[VendorId, ...] = ()
    account_refs: tuple[str, ...] = ()

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @field_validator("providers", "account_refs")
    @classmethod
    def unique_values(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(value) != len(set(value)):
            raise ValueError("performance filters must be unique")
        return value

    @model_validator(mode="after")
    def ordered_window(self) -> PerformanceAttributionInput:
        if self.start > self.end:
            raise ValueError("start must be <= end")
        return self


class InstrumentPerformanceDTO(_DTO):
    instrument_id: str
    currency: str
    ending_quantity: DecimalWire
    open_cost_basis: DecimalWire
    realized_pnl_before_fees: DecimalWire | None
    realized_pnl_after_fees: DecimalWire | None
    unrealized_pnl_before_fees: DecimalWire | None
    broker_reported_unrealized_pnl: DecimalWire | None
    broker_reported_realized_pnl: DecimalWire | None
    known_fees: DecimalWire
    fees_complete: bool
    matched_quantity: DecimalWire
    activity_ids: tuple[str, ...]
    snapshot_id: str | None
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: InstrumentPerformance) -> InstrumentPerformanceDTO:
        return cls.model_validate(value)


class AccountPerformanceDTO(_DTO):
    account_ref: str
    provider: VendorId
    currency: str
    cost_basis_method: CostBasisMethod
    snapshot_id: str | None
    snapshot_as_of: datetime | None
    realized_pnl_before_fees: DecimalWire | None
    realized_pnl_after_fees: DecimalWire | None
    unrealized_pnl_before_fees: DecimalWire | None
    broker_reported_unrealized_pnl: DecimalWire | None
    broker_reported_realized_pnl: DecimalWire | None
    dividends: DecimalWire
    interest: DecimalWire
    known_fees: DecimalWire
    fees_complete: bool
    net_external_cash_flow: DecimalWire
    instruments: tuple[InstrumentPerformanceDTO, ...]
    status: AttributionStatus
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: AccountPerformance) -> AccountPerformanceDTO:
        return cls.model_validate(value)


class PerformanceAttributionDTO(_DTO):
    start: datetime
    end: datetime
    cost_basis_method: CostBasisMethod
    accounts: tuple[AccountPerformanceDTO, ...]
    status: AttributionStatus
    warning_codes: tuple[str, ...]
    algorithm_version: str

    @classmethod
    def from_domain(cls, value: PerformanceAttribution) -> PerformanceAttributionDTO:
        return cls.model_validate(value)
