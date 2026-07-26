"""Closed cross-asset futures/spot/basis DTOs (Phase 3A-0 scaffolding).

Output DTOs mirror frozen domain models with ``extra=forbid`` and Decimal wire
serialization. No MCP tool surface is registered in this slice.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.market import DecimalWire, MarketBarDTO
from domain.common.enums import AdjustmentMethod, Market
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.cross_asset.basis_service import BasisLeg, BasisSnapshot
from domain.cross_asset.enums import (
    BasisComparability,
    ContinuousAdjustment,
    ContractLifecycleStatus,
    CurveCompleteness,
    CurveShape,
    OfferSide,
    PriceBasis,
    RollRule,
    SettlementMethod,
    SettlementStatus,
    SpotVenueBasis,
    SpotVolumeBasis,
)
from domain.cross_asset.futures_models import (
    ContinuousContractMapping,
    ContinuousSeriesDefinition,
    FuturesContractDefinition,
    FuturesContractStatistics,
    FuturesCurveContractPoint,
    FuturesCurveSnapshot,
    FuturesProductDefinition,
)
from domain.cross_asset.spot_models import CommoditySpotBarSeries, SpotObservation
from domain.us_market.enums import USBarInterval


class _FrozenForbid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FuturesProductDefinitionDTO(_FrozenForbid):
    product_id: str
    product_key: str
    root: str
    market: Market
    exchange: str
    commodity: str
    currency: str
    price_unit: str
    multiplier: DecimalWire
    tick_size: DecimalWire
    settlement_method: SettlementMethod
    session_calendar_id: str
    source: str
    valid_from: datetime
    definition_as_of: datetime
    version_id: str | None = None
    version: int = 1
    valid_to: datetime | None = None

    @classmethod
    def from_domain(cls, value: FuturesProductDefinition) -> Self:
        return cls(
            product_id=value.product_id,
            product_key=value.product_key,
            root=value.root,
            market=value.market,
            exchange=value.exchange,
            commodity=value.commodity,
            currency=value.currency,
            price_unit=value.price_unit,
            multiplier=value.multiplier,
            tick_size=value.tick_size,
            settlement_method=value.settlement_method,
            session_calendar_id=value.session_calendar_id,
            source=value.source,
            valid_from=value.valid_from,
            definition_as_of=value.definition_as_of,
            version_id=value.version_id,
            version=value.version,
            valid_to=value.valid_to,
        )


class FuturesContractDefinitionDTO(_FrozenForbid):
    instrument_id: str
    product_id: str
    contract_month: str
    status: ContractLifecycleStatus
    definition_as_of: datetime
    version_id: str | None = None
    version: int = 1
    listed_at: datetime | None = None
    first_trade_at: datetime | None = None
    last_trade_at: datetime | None = None
    expiration_at: datetime | None = None
    first_notice_at: datetime | None = None
    delivery_start: date | None = None
    delivery_end: date | None = None
    settlement_at: datetime | None = None
    source: str = "unknown"

    @classmethod
    def from_domain(cls, value: FuturesContractDefinition) -> Self:
        return cls(
            instrument_id=value.instrument_id,
            product_id=value.product_id,
            contract_month=value.contract_month,
            status=value.status,
            definition_as_of=value.definition_as_of,
            version_id=value.version_id,
            version=value.version,
            listed_at=value.listed_at,
            first_trade_at=value.first_trade_at,
            last_trade_at=value.last_trade_at,
            expiration_at=value.expiration_at,
            first_notice_at=value.first_notice_at,
            delivery_start=value.delivery_start,
            delivery_end=value.delivery_end,
            settlement_at=value.settlement_at,
            source=value.source,
        )


class FuturesContractStatisticsDTO(_FrozenForbid):
    instrument_id: str
    trade_date: date
    settlement: DecimalWire | None
    settlement_status: SettlementStatus
    session_volume: DecimalWire | None
    open_interest: DecimalWire | None
    published_at: datetime
    source: str

    @classmethod
    def from_domain(cls, value: FuturesContractStatistics) -> Self:
        return cls(
            instrument_id=value.instrument_id,
            trade_date=value.trade_date,
            settlement=value.settlement,
            settlement_status=value.settlement_status,
            session_volume=value.session_volume,
            open_interest=value.open_interest,
            published_at=value.published_at,
            source=value.source,
        )


class ContinuousSeriesDefinitionDTO(_FrozenForbid):
    instrument_id: str
    product_id: str
    roll_rule: RollRule
    rank: int = Field(ge=0)
    adjustment: ContinuousAdjustment
    provider_methodology_version: str
    valid_from: datetime
    valid_to: datetime | None = None

    @classmethod
    def from_domain(cls, value: ContinuousSeriesDefinition) -> Self:
        return cls(
            instrument_id=value.instrument_id,
            product_id=value.product_id,
            roll_rule=value.roll_rule,
            rank=value.rank,
            adjustment=value.adjustment,
            provider_methodology_version=value.provider_methodology_version,
            valid_from=value.valid_from,
            valid_to=value.valid_to,
        )


class ContinuousContractMappingDTO(_FrozenForbid):
    continuous_instrument_id: str
    contract_instrument_id: str
    effective_from: datetime
    mapping_source: str
    effective_to: datetime | None = None

    @classmethod
    def from_domain(cls, value: ContinuousContractMapping) -> Self:
        return cls(
            continuous_instrument_id=value.continuous_instrument_id,
            contract_instrument_id=value.contract_instrument_id,
            effective_from=value.effective_from,
            mapping_source=value.mapping_source,
            effective_to=value.effective_to,
        )


class FuturesCurveContractPointDTO(_FrozenForbid):
    instrument_id: str
    contract_month: str
    expiration_at: datetime | None
    price: DecimalWire
    open_interest: DecimalWire | None = None
    session_volume: DecimalWire | None = None

    @classmethod
    def from_domain(cls, value: FuturesCurveContractPoint) -> Self:
        return cls(
            instrument_id=value.instrument_id,
            contract_month=value.contract_month,
            expiration_at=value.expiration_at,
            price=value.price,
            open_interest=value.open_interest,
            session_volume=value.session_volume,
        )


class FuturesCurveSnapshotDTO(_FrozenForbid):
    product_id: str
    as_of: datetime
    price_basis: PriceBasis
    contracts: tuple[FuturesCurveContractPointDTO, ...]
    curve_shape: CurveShape
    completeness: CurveCompleteness
    front_next_spread: DecimalWire | None = None

    @classmethod
    def from_domain(cls, value: FuturesCurveSnapshot) -> Self:
        return cls(
            product_id=value.product_id,
            as_of=value.as_of,
            price_basis=value.price_basis,
            contracts=tuple(
                FuturesCurveContractPointDTO.from_domain(item)
                for item in value.contracts
            ),
            curve_shape=value.curve_shape,
            completeness=value.completeness,
            front_next_spread=value.front_next_spread,
        )


class SpotObservationDTO(_FrozenForbid):
    instrument_id: str
    currency: str
    unit: str
    quote_at: datetime | None
    venue_basis: SpotVenueBasis
    source: str
    bid: DecimalWire | None = None
    ask: DecimalWire | None = None
    mid: DecimalWire | None = None
    last: DecimalWire | None = None
    delivery_location: str | None = None

    @classmethod
    def from_domain(cls, value: SpotObservation) -> Self:
        return cls(
            instrument_id=value.instrument_id,
            currency=value.currency,
            unit=value.unit,
            quote_at=value.quote_at,
            venue_basis=value.venue_basis,
            source=value.source,
            bid=value.bid,
            ask=value.ask,
            mid=value.mid,
            last=value.last,
            delivery_location=value.delivery_location,
        )


class CommoditySpotBarSeriesDTO(_FrozenForbid):
    instrument_id: str
    interval: USBarInterval
    offer_side: OfferSide
    start: date
    end: date
    adjustment: AdjustmentMethod
    bars: tuple[MarketBarDTO, ...]
    volume_basis: SpotVolumeBasis

    @classmethod
    def from_domain(cls, value: CommoditySpotBarSeries) -> Self:
        return cls(
            instrument_id=value.instrument_id,
            interval=value.interval,
            offer_side=value.offer_side,
            start=value.start,
            end=value.end,
            adjustment=value.adjustment,
            bars=tuple(MarketBarDTO.from_domain(bar) for bar in value.bars),
            volume_basis=value.volume_basis,
        )


class BasisLegDTO(_FrozenForbid):
    instrument_id: str
    price: DecimalWire
    currency: str
    unit: str
    observed_at: datetime
    price_basis: PriceBasis
    delivery_location: str | None = None

    @classmethod
    def from_domain(cls, value: BasisLeg) -> Self:
        return cls(
            instrument_id=value.instrument_id,
            price=value.price,
            currency=value.currency,
            unit=value.unit,
            observed_at=value.observed_at,
            price_basis=value.price_basis,
            delivery_location=value.delivery_location,
        )


class BasisSnapshotDTO(_FrozenForbid):
    left_leg: BasisLegDTO
    right_leg: BasisLegDTO
    normalized_unit: str | None
    observation_lag_seconds: int = Field(ge=0)
    absolute_spread: DecimalWire | None
    percentage_spread: DecimalWire | None
    comparability: BasisComparability
    formula_version: str
    reason_codes: tuple[str, ...] = ()

    @classmethod
    def from_domain(cls, value: BasisSnapshot) -> Self:
        return cls(
            left_leg=BasisLegDTO.from_domain(value.left_leg),
            right_leg=BasisLegDTO.from_domain(value.right_leg),
            normalized_unit=value.normalized_unit,
            observation_lag_seconds=value.observation_lag_seconds,
            absolute_spread=value.absolute_spread,
            percentage_spread=value.percentage_spread,
            comparability=value.comparability,
            formula_version=value.formula_version,
            reason_codes=value.reason_codes,
        )


class SpotFutureBasisInput(_FrozenForbid):
    """Future market_get_context operation input scaffold (not wired to MCP yet)."""

    left_instrument_id: str
    right_instrument_id: str
    max_observation_lag_seconds: int = Field(default=300, ge=0)
    as_of: datetime | None = None

    @field_validator("left_instrument_id", "right_instrument_id")
    @classmethod
    def _instrument_id(cls, value: str) -> str:
        parse_instrument_id(value)
        return value

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="as_of")
        return value

    @model_validator(mode="after")
    def _distinct_legs(self) -> Self:
        if self.left_instrument_id == self.right_instrument_id:
            raise ValueError("left and right instrument ids must differ")
        return self
