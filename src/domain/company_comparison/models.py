"""Pure domain facts and outputs for company peer comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.common.enums import AssetType, Market
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.company_comparison.enums import PeerComparisonPeriodMode, PeerComparisonStatus


def _text(value: str, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be a bounded non-blank string")
    return value


def _instrument(value: str, field: str) -> tuple[AssetType, Market, str]:
    parsed = parse_instrument_id(value)
    if parsed[0] is not AssetType.EQUITY or parsed[1] not in {Market.A_SHARE, Market.US}:
        raise DataContractError(f"{field} must be an A-share or US equity instrument")
    return parsed


def _decimal_items(
    value: tuple[tuple[str, Decimal | None], ...], field: str
) -> tuple[tuple[str, Decimal | None], ...]:
    if not isinstance(value, tuple):
        raise DataContractError(f"{field} must be a tuple")
    keys: set[str] = set()
    for key, item in value:
        _text(key, f"{field}.key", 80)
        if key in keys:
            raise DataContractError(f"{field} keys must be unique")
        keys.add(key)
        if item is not None and (not isinstance(item, Decimal) or not item.is_finite()):
            raise DataContractError(f"{field} values must be finite Decimal or None")
    return value


@dataclass(frozen=True, slots=True)
class PeerCompanyPeriod:
    instrument_id: str
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    basis: str
    currency: str
    published_at: datetime | None
    line_items: tuple[tuple[str, Decimal | None], ...]
    source_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _instrument(self.instrument_id, "instrument_id")
        if self.period_start is not None and self.period_start > self.period_end:
            raise DataContractError("period_start must be <= period_end")
        if self.fiscal_year is not None and not 1900 <= self.fiscal_year <= 2100:
            raise DataContractError("fiscal_year is out of range")
        _text(self.basis, "basis", 64)
        _text(self.currency, "currency", 32)
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _decimal_items(self.line_items, "line_items")
        if len(self.source_names) != len(set(self.source_names)):
            raise DataContractError("source_names must be unique")


@dataclass(frozen=True, slots=True)
class PeerCompanyValuation:
    instrument_id: str
    observed_at: datetime
    values: tuple[tuple[str, Decimal | None], ...]
    currency: str
    source_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _instrument(self.instrument_id, "instrument_id")
        require_aware_datetime(self.observed_at, field_name="observed_at")
        _decimal_items(self.values, "values")
        _text(self.currency, "currency", 32)
        if len(self.source_names) != len(set(self.source_names)):
            raise DataContractError("source_names must be unique")


@dataclass(frozen=True, slots=True)
class PeerOperatingFact:
    instrument_id: str
    metric_code: str
    value: Decimal
    unit: str
    period_start: date
    period_end: date
    frequency: str
    measurement_basis: str
    published_at: datetime
    source_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _instrument(self.instrument_id, "instrument_id")
        _text(self.metric_code, "metric_code", 100)
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise DataContractError("value must be a finite Decimal")
        _text(self.unit, "unit", 64)
        if self.period_start > self.period_end:
            raise DataContractError("period_start must be <= period_end")
        _text(self.frequency, "frequency", 32)
        _text(self.measurement_basis, "measurement_basis", 64)
        require_aware_datetime(self.published_at, field_name="published_at")


@dataclass(frozen=True, slots=True)
class PeerCompanyFacts:
    instrument_id: str
    periods: tuple[PeerCompanyPeriod, ...]
    valuation: PeerCompanyValuation | None = None
    operating_facts: tuple[PeerOperatingFact, ...] = ()

    def __post_init__(self) -> None:
        _instrument(self.instrument_id, "instrument_id")
        if any(item.instrument_id != self.instrument_id for item in self.periods):
            raise DataContractError("period instrument_id mismatch")
        if self.valuation is not None and self.valuation.instrument_id != self.instrument_id:
            raise DataContractError("valuation instrument_id mismatch")
        if any(item.instrument_id != self.instrument_id for item in self.operating_facts):
            raise DataContractError("operating fact instrument_id mismatch")


@dataclass(frozen=True, slots=True)
class PeerComparisonCell:
    instrument_id: str
    value: Decimal | None
    period_start: date | None
    period_end: date | None
    fiscal_year: int | None
    basis: str | None
    published_at: datetime | None
    source_names: tuple[str, ...]
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PeerComparisonRow:
    metric_group: str
    metric_code: str
    comparison_period: str
    unit: str
    formula: str
    formula_version: str
    comparability: PeerComparisonStatus
    values: tuple[PeerComparisonCell, ...]


@dataclass(frozen=True, slots=True)
class PeerComparisonFactPackage:
    primary_instrument_id: str
    peer_instrument_ids: tuple[str, ...]
    market: Market
    as_of: datetime
    period_mode: PeerComparisonPeriodMode
    comparison_rows: tuple[PeerComparisonRow, ...]
    operating_metric_appendix: tuple[PeerComparisonRow, ...]
    unavailable_instrument_ids: tuple[str, ...]
    algorithm_version: str = "peer_comparison_v1"
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _, market, _ = _instrument(self.primary_instrument_id, "primary_instrument_id")
        if market is not self.market:
            raise DataContractError("market must match primary instrument")
        require_aware_datetime(self.as_of, field_name="as_of")
        if self.primary_instrument_id in self.peer_instrument_ids:
            raise DataContractError("primary instrument must not appear in peers")
        if not 1 <= len(self.peer_instrument_ids) <= 5:
            raise DataContractError("peer count must be in [1,5]")
        if len(set(self.peer_instrument_ids)) != len(self.peer_instrument_ids):
            raise DataContractError("peer instruments must be unique")
        if self.execution_effect is not False:
            raise DataContractError("peer comparison must not execute")
