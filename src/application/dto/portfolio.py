"""Closed Phase 1I account and portfolio MCP DTOs."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from application.dto.market import DecimalWire
from domain.common.enums import VendorId
from domain.common.values import parse_instrument_id
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountOpenOrderSide,
    AccountOpenOrderStatus,
    AccountPositionSide,
)
from domain.portfolio.models import (
    AccountSnapshot,
    PortfolioEnrichment,
    PortfolioRiskMetric,
    PortfolioSimulation,
    PortfolioSnapshot,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must be timezone-aware")
    return value


class AccountGetSnapshotInput(_DTO):
    providers: tuple[VendorId, ...] = ()
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class AccountGetPositionsInput(_DTO):
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)


class PortfolioAnalyzeInput(_DTO):
    account_snapshot_ids: tuple[str, ...] = ()
    base_currency: str = Field(default="USD", min_length=1, max_length=16)


class PortfolioSimulateAdditionInput(_DTO):
    account_snapshot_ids: tuple[str, ...] = ()
    instrument_id: str
    quantity: Decimal = Field(gt=0)
    assumed_price: Decimal = Field(gt=0)
    currency: str = Field(min_length=1, max_length=16)
    base_currency: str = Field(default="USD", min_length=1, max_length=16)

    @field_validator("instrument_id")
    @classmethod
    def instrument(cls, value: str) -> str:
        parse_instrument_id(value)
        return value


class AccountPositionDTO(_DTO):
    instrument_id: str
    side: AccountPositionSide
    quantity: DecimalWire
    sellable_quantity: DecimalWire | None
    average_cost: DecimalWire | None
    diluted_cost: DecimalWire | None
    market_price: DecimalWire | None
    market_price_at: datetime | None
    market_value: DecimalWire | None
    unrealized_pnl: DecimalWire | None
    realized_pnl: DecimalWire | None
    currency: str

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed property
    @property
    def snapshot_price(self) -> DecimalWire | None:
        """Display-only account price; never promote valuation math to a quote."""
        if self.market_price is not None:
            return self.market_price
        if self.market_value is None or self.quantity <= 0:
            return None
        return (abs(self.market_value) / self.quantity).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed property
    @property
    def snapshot_price_basis(
        self,
    ) -> Literal["BROKER_REPORTED_PRICE", "BROKER_VALUATION_ONLY"] | None:
        """Disclose whether snapshot_price is a Provider price or valuation-only math."""
        if self.market_price is not None:
            return "BROKER_REPORTED_PRICE"
        if self.market_value is not None and self.quantity > 0:
            return "BROKER_VALUATION_ONLY"
        return None


class AccountOpenOrderDTO(_DTO):
    provider_order_id: str
    instrument_id: str
    side: AccountOpenOrderSide
    status: AccountOpenOrderStatus
    quantity: DecimalWire
    filled_quantity: DecimalWire
    limit_price: DecimalWire | None
    submitted_at: datetime | None


class AccountSnapshotDTO(_DTO):
    snapshot_id: str
    account_ref: str
    provider: VendorId
    environment: AccountEnvironment
    base_currency: str
    account_as_of: datetime
    fetched_at: datetime
    cash: DecimalWire | None
    buying_power: DecimalWire | None
    net_assets: DecimalWire | None
    margin_used: DecimalWire | None
    positions: tuple[AccountPositionDTO, ...]
    open_orders: tuple[AccountOpenOrderDTO, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: AccountSnapshot) -> AccountSnapshotDTO:
        return cls.model_validate(value)


class AccountSnapshotsDTO(_DTO):
    snapshots: tuple[AccountSnapshotDTO, ...]


class AccountPositionsAccountDTO(_DTO):
    snapshot_id: str
    account_ref: str
    provider: VendorId
    environment: AccountEnvironment
    base_currency: str
    account_as_of: datetime
    fetched_at: datetime
    cash: DecimalWire | None
    buying_power: DecimalWire | None
    net_assets: DecimalWire | None
    margin_used: DecimalWire | None
    positions: tuple[AccountPositionDTO, ...]
    open_orders: tuple[AccountOpenOrderDTO, ...]
    degraded: bool
    warning_codes: tuple[str, ...]


class AccountPositionsDTO(_DTO):
    accounts: tuple[AccountPositionsAccountDTO, ...]


class PortfolioExposureDTO(_DTO):
    dimension: str
    key: str
    value: DecimalWire
    weight: DecimalWire | None


class PortfolioSnapshotDTO(_DTO):
    portfolio_snapshot_id: str
    account_snapshot_ids: tuple[str, ...]
    as_of: datetime
    base_currency: str
    total_value: DecimalWire | None
    exposures: tuple[PortfolioExposureDTO, ...]
    missing_instrument_ids: tuple[str, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: PortfolioSnapshot) -> PortfolioSnapshotDTO:
        return cls.model_validate(value)


class PortfolioSimulationDTO(_DTO):
    before: PortfolioSnapshotDTO
    after: PortfolioSnapshotDTO
    added_instrument_id: str
    added_quantity: DecimalWire
    assumed_price: DecimalWire
    currency: str
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: PortfolioSimulation) -> PortfolioSimulationDTO:
        return cls.model_validate(value)


class PortfolioRiskMetricDTO(_DTO):
    instrument_id: str
    benchmark_instrument_id: str
    aligned_observations: int
    correlation: DecimalWire | None
    beta: DecimalWire | None
    missing_reason: str | None
    algorithm_version: str

    @classmethod
    def from_domain(cls, value: PortfolioRiskMetric) -> PortfolioRiskMetricDTO:
        return cls.model_validate(value)


class PortfolioEnrichedExposureDTO(_DTO):
    dimension: str
    key: str
    currency: str
    value: DecimalWire
    weight_within_currency: DecimalWire


class PortfolioEnrichmentDTO(_DTO):
    exposures: tuple[PortfolioEnrichedExposureDTO, ...]
    missing_classification_instrument_ids: tuple[str, ...]
    missing_valuation_instrument_ids: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: PortfolioEnrichment) -> PortfolioEnrichmentDTO:
        return cls.model_validate(value)


class PortfolioReviewDerivedDTO(_DTO):
    risk_metrics: tuple[PortfolioRiskMetricDTO, ...]
    enrichment: PortfolioEnrichmentDTO
    warning_codes: tuple[str, ...]
