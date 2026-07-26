"""Closed DTOs for caller-specified company peer comparison."""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.common.enums import AssetType, Market
from domain.common.values import parse_instrument_id
from domain.company_comparison.enums import PeerComparisonPeriodMode, PeerComparisonStatus
from domain.company_comparison.models import (
    PeerComparisonCell,
    PeerComparisonFactPackage,
    PeerComparisonRow,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


def _equity_instrument(value: str, field: str) -> tuple[AssetType, Market, str]:
    try:
        parsed = parse_instrument_id(value)
    except Exception:
        raise ValueError(f"{field} must be a canonical instrument_id") from None
    if parsed[0] is not AssetType.EQUITY or parsed[1] not in {Market.A_SHARE, Market.US}:
        raise ValueError(f"{field} must be an A-share or US equity")
    return parsed


class PeerComparisonRunInput(_DTO):
    idempotency_key: str = Field(min_length=1, max_length=128)
    primary_instrument_id: str
    peer_instrument_ids: tuple[str, ...] = Field(min_length=1, max_length=5)
    period_mode: PeerComparisonPeriodMode = PeerComparisonPeriodMode.ANNUAL
    periods: int = Field(default=3, ge=1, le=5)
    include_valuation: bool = True
    include_operating_metrics: bool = False
    as_of: datetime | None = None

    @field_validator("idempotency_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("primary_instrument_id")
    @classmethod
    def validate_primary(cls, value: str) -> str:
        _equity_instrument(value, "primary_instrument_id")
        return value

    @field_validator("peer_instrument_ids")
    @classmethod
    def validate_peers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate_peer_instrument")
        for item in value:
            _equity_instrument(item, "peer_instrument_ids")
        return value

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        return value

    @model_validator(mode="after")
    def compatible_set(self) -> Self:
        if self.primary_instrument_id in self.peer_instrument_ids:
            raise ValueError("primary_in_peer_set")
        _, primary_market, _ = _equity_instrument(
            self.primary_instrument_id, "primary_instrument_id"
        )
        if any(
            _equity_instrument(item, "peer_instrument_ids")[1] is not primary_market
            for item in self.peer_instrument_ids
        ):
            raise ValueError("peer_market_mismatch")
        if self.include_operating_metrics and primary_market is not Market.A_SHARE:
            raise ValueError("include_operating_metrics is A-share only")
        return self


class PeerComparisonCellDTO(_DTO):
    instrument_id: str
    value: DecimalWire | None
    period_start: date | None
    period_end: date | None
    fiscal_year: int | None
    basis: str | None
    published_at: datetime | None
    source_names: tuple[str, ...]
    unavailable_reason: str | None

    @classmethod
    def from_domain(cls, value: PeerComparisonCell) -> PeerComparisonCellDTO:
        return cls.model_validate(value)


class PeerComparisonRowDTO(_DTO):
    metric_group: str
    metric_code: str
    comparison_period: str
    unit: str
    formula: str
    formula_version: str
    comparability: PeerComparisonStatus
    values: tuple[PeerComparisonCellDTO, ...]

    @classmethod
    def from_domain(cls, value: PeerComparisonRow) -> PeerComparisonRowDTO:
        return cls(
            metric_group=value.metric_group,
            metric_code=value.metric_code,
            comparison_period=value.comparison_period,
            unit=value.unit,
            formula=value.formula,
            formula_version=value.formula_version,
            comparability=value.comparability,
            values=tuple(PeerComparisonCellDTO.from_domain(item) for item in value.values),
        )


class PeerComparisonFactPackageDTO(_DTO):
    primary_instrument_id: str
    peer_instrument_ids: tuple[str, ...]
    market: Market
    as_of: datetime
    period_mode: PeerComparisonPeriodMode
    comparison_rows: tuple[PeerComparisonRowDTO, ...]
    operating_metric_appendix: tuple[PeerComparisonRowDTO, ...]
    unavailable_instrument_ids: tuple[str, ...]
    algorithm_version: str
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: PeerComparisonFactPackage) -> PeerComparisonFactPackageDTO:
        return cls(
            primary_instrument_id=value.primary_instrument_id,
            peer_instrument_ids=value.peer_instrument_ids,
            market=value.market,
            as_of=value.as_of,
            period_mode=value.period_mode,
            comparison_rows=tuple(
                PeerComparisonRowDTO.from_domain(item) for item in value.comparison_rows
            ),
            operating_metric_appendix=tuple(
                PeerComparisonRowDTO.from_domain(item)
                for item in value.operating_metric_appendix
            ),
            unavailable_instrument_ids=value.unavailable_instrument_ids,
            algorithm_version=value.algorithm_version,
            execution_effect=value.execution_effect,
        )
