"""Closed application DTOs for native-currency performance facts."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.common.enums import VendorId
from domain.performance.enums import PerformanceComputationStatus, PerformanceStatus
from domain.performance.models import CyclePerformance, DailyEquityPoint, PerformanceSeries
from domain.portfolio.enums import TradeCycleStatus


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class DailyEquityPointDTO(_DTO):
    account_ref: str
    currency: str
    valuation_at: datetime
    market_session_date: date
    equity_value: DecimalWire | None
    source_snapshot_id: str
    source_snapshot_as_of: datetime
    source_fetched_at: datetime
    coverage_status: PerformanceStatus
    net_external_cash_flow_since_previous: DecimalWire
    external_flow_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]
    valuation_basis: str

    @classmethod
    def from_domain(cls, value: DailyEquityPoint) -> DailyEquityPointDTO:
        return cls.model_validate(value)

    @property
    def snapshot_id(self) -> str:
        return self.source_snapshot_id


class CyclePerformanceDTO(_DTO):
    cycle_id: str
    account_ref: str
    currency: str
    instrument_id: str | None
    opened_at: datetime | None
    closed_at: datetime | None
    status: TradeCycleStatus
    opening_count: int
    add_count: int
    reduce_count: int
    holding_duration_seconds: int | None
    gross_realized_pnl: DecimalWire | None
    net_realized_pnl: DecimalWire | None
    remaining_unrealized_pnl: DecimalWire | None
    maximum_deployed_capital: DecimalWire | None
    cycle_return: DecimalWire | None
    initial_planned_risk: DecimalWire | None
    r_multiple: DecimalWire | None
    activity_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]
    algorithm_version: str

    @classmethod
    def from_domain(cls, value: CyclePerformance) -> CyclePerformanceDTO:
        return cls.model_validate(value)

    @property
    def return_on_maximum_deployed_capital(self) -> DecimalWire | None:
        return self.cycle_return


class PerformanceSeriesDTO(_DTO):
    account_ref: str
    currency: str
    period_start: datetime
    period_end: datetime
    points: tuple[DailyEquityPointDTO, ...]
    twr: DecimalWire | None
    xirr: DecimalWire | None
    maximum_drawdown: DecimalWire | None
    twr_index: tuple[DecimalWire, ...]
    status: PerformanceStatus
    input_snapshot_ids: tuple[str, ...]
    input_snapshot_times: tuple[datetime, ...]
    input_activity_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]
    twr_status: PerformanceComputationStatus
    xirr_status: PerformanceComputationStatus
    drawdown_status: PerformanceComputationStatus
    algorithm_version: str
    dividends: DecimalWire | None
    interest: DecimalWire | None
    known_fees: DecimalWire | None
    capital_base: DecimalWire | None
    income_return: DecimalWire | None
    fee_drag: DecimalWire | None
    income_return_status: PerformanceComputationStatus
    fee_drag_status: PerformanceComputationStatus
    cycle_performance: tuple[CyclePerformanceDTO, ...]
    input_cycle_ids: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: PerformanceSeries) -> PerformanceSeriesDTO:
        return cls.model_validate(value)

    @property
    def mwr(self) -> DecimalWire | None:
        """MWR is the actual-timestamp XIRR value."""

        return self.xirr

    @property
    def mwr_status(self) -> PerformanceComputationStatus:
        return self.xirr_status

    @property
    def time_weighted_return(self) -> DecimalWire | None:
        return self.twr

    @property
    def money_weighted_return(self) -> DecimalWire | None:
        return self.xirr

    @property
    def max_drawdown(self) -> DecimalWire | None:
        return self.maximum_drawdown

    @property
    def selected_capital_base(self) -> DecimalWire | None:
        return self.capital_base

    @property
    def cycle_returns(self) -> tuple[CyclePerformanceDTO, ...]:
        return self.cycle_performance

    @property
    def equity_points(self) -> tuple[DailyEquityPointDTO, ...]:
        return self.points

    @property
    def account_snapshot_ids(self) -> tuple[str, ...]:
        return self.input_snapshot_ids

    @property
    def transaction_ids(self) -> tuple[str, ...]:
        return self.input_activity_ids


class PerformanceSeriesCollectionDTO(_DTO):
    series: tuple[PerformanceSeriesDTO, ...]


class PerformanceSeriesQueryInput(_DTO):
    start: datetime
    end: datetime
    providers: tuple[VendorId, ...] = ()
    account_refs: tuple[str, ...] = ()

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("performance series datetime must be timezone-aware")
        return value

    @field_validator("providers", "account_refs")
    @classmethod
    def unique(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(value) != len(set(value)):
            raise ValueError("performance series filters must be unique")
        return value

    @field_validator("account_refs")
    @classmethod
    def nonblank_accounts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("account_refs entries must be non-blank")
        return value

    @model_validator(mode="after")
    def ordered(self) -> PerformanceSeriesQueryInput:
        if self.start >= self.end:
            raise ValueError("performance series start must be before end")
        return self


__all__ = [
    "CyclePerformanceDTO",
    "DailyEquityPointDTO",
    "PerformanceSeriesCollectionDTO",
    "PerformanceSeriesDTO",
    "PerformanceSeriesQueryInput",
]
