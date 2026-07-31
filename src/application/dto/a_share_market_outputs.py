"""A-share market-context and capital output DTOs."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from application.dto.a_share_common import _FrozenForbid
from application.dto.market import DecimalWire
from domain.a_share.enums import BarInterval
from domain.a_share.models import (
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    DividendRecord,
    DragonTigerRecord,
    DragonTigerSeat,
    FundFlowPoint,
    IndustryPerformanceRow,
    MarginRecord,
    MarketBoardSnapshot,
    NorthboundFlowPoint,
    ShareholderCountRecord,
    UnlockRecord,
)
from domain.common.enums import AdjustmentMethod, ReliabilityLevel, VendorId


class IndustryPerformanceRowDTO(_FrozenForbid):
    industry_code: str
    industry_name: str
    trade_date: date
    change_percent: DecimalWire
    advancing_count: int
    declining_count: int
    unchanged_count: int
    leading_instrument_id: str | None
    leading_change_percent: DecimalWire | None
    turnover_amount_cny: DecimalWire | None

    @classmethod
    def from_domain(cls, row: IndustryPerformanceRow) -> IndustryPerformanceRowDTO:
        return cls.model_validate(row, from_attributes=True)


class MarketBoardSnapshotDTO(_FrozenForbid):
    trade_date: date
    advancing_count: int
    declining_count: int
    unchanged_count: int
    limit_up_count: int
    limit_down_count: int
    broken_limit_count: int
    total_turnover_cny: DecimalWire | None
    median_change_percent: DecimalWire | None
    industries: tuple[IndustryPerformanceRowDTO, ...]

    @classmethod
    def from_domain(cls, board: MarketBoardSnapshot) -> MarketBoardSnapshotDTO:
        return cls(
            trade_date=board.trade_date,
            advancing_count=board.advancing_count,
            declining_count=board.declining_count,
            unchanged_count=board.unchanged_count,
            limit_up_count=board.limit_up_count,
            limit_down_count=board.limit_down_count,
            broken_limit_count=board.broken_limit_count,
            total_turnover_cny=board.total_turnover_cny,
            median_change_percent=board.median_change_percent,
            industries=tuple(IndustryPerformanceRowDTO.from_domain(r) for r in board.industries),
        )


class FundFlowPointDTO(_FrozenForbid):
    occurred_at: datetime
    interval: BarInterval
    main_net_cny: DecimalWire | None
    super_large_net_cny: DecimalWire | None
    large_net_cny: DecimalWire | None
    medium_net_cny: DecimalWire | None
    small_net_cny: DecimalWire | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, point: FundFlowPoint) -> FundFlowPointDTO:
        return cls.model_validate(point, from_attributes=True)


class NorthboundFlowPointDTO(_FrozenForbid):
    trade_date: date
    channel: str
    net_buy_cny: DecimalWire | None
    buy_cny: DecimalWire | None
    sell_cny: DecimalWire | None
    disclosure_note: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, point: NorthboundFlowPoint) -> NorthboundFlowPointDTO:
        return cls.model_validate(point, from_attributes=True)


class DragonTigerSeatDTO(_FrozenForbid):
    rank: int
    side: str
    branch_name: str
    amount_cny: DecimalWire
    is_institution: bool | None

    @classmethod
    def from_domain(cls, seat: DragonTigerSeat) -> DragonTigerSeatDTO:
        return cls.model_validate(seat, from_attributes=True)


class DragonTigerRecordDTO(_FrozenForbid):
    trade_date: date
    instrument_id: str
    reason: str
    buy_total_cny: DecimalWire
    sell_total_cny: DecimalWire
    net_buy_cny: DecimalWire
    seats: tuple[DragonTigerSeatDTO, ...]
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: DragonTigerRecord) -> DragonTigerRecordDTO:
        return cls(
            trade_date=record.trade_date,
            instrument_id=record.instrument_id,
            reason=record.reason,
            buy_total_cny=record.buy_total_cny,
            sell_total_cny=record.sell_total_cny,
            net_buy_cny=record.net_buy_cny,
            seats=tuple(DragonTigerSeatDTO.from_domain(s) for s in record.seats),
            source_vendor=record.source_vendor,
            reliability=record.reliability,
            is_authoritative=record.is_authoritative,
        )


class MarginRecordDTO(_FrozenForbid):
    trade_date: date
    financing_balance_cny: DecimalWire
    financing_buy_cny: DecimalWire
    financing_repayment_cny: DecimalWire
    securities_lending_balance_cny: DecimalWire | None
    securities_lending_sell_shares: int | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: MarginRecord) -> MarginRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class BlockTradeRecordDTO(_FrozenForbid):
    trade_date: date
    price: DecimalWire
    volume_shares: int
    amount_cny: DecimalWire
    premium_percent: DecimalWire | None
    buyer_branch: str | None
    seller_branch: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: BlockTradeRecord) -> BlockTradeRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class ShareholderCountRecordDTO(_FrozenForbid):
    period_end: date
    published_at: datetime | None
    shareholder_count: int
    change_percent: DecimalWire | None
    average_holding_shares: DecimalWire | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: ShareholderCountRecord) -> ShareholderCountRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class ChipDistributionBinDTO(_FrozenForbid):
    price_low: DecimalWire
    price_high: DecimalWire
    holding_ratio: DecimalWire

    @classmethod
    def from_domain(cls, bin_row: ChipDistributionBin) -> ChipDistributionBinDTO:
        return cls.model_validate(bin_row, from_attributes=True)


class ChipDistributionSnapshotDTO(_FrozenForbid):
    as_of: datetime
    bins: tuple[ChipDistributionBinDTO, ...]
    profit_ratio: DecimalWire | None
    average_cost: DecimalWire | None
    concentration_90: DecimalWire | None = Field(
        description=(
            "Relative 90% cost-band width; lower values mean holdings are more concentrated."
        )
    )
    concentration_70: DecimalWire | None = Field(
        description=(
            "Relative 70% cost-band width; lower values mean holdings are more concentrated."
        )
    )
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool
    calculation_method: str
    algorithm_version: str
    lookback_sessions: int
    input_adjustment: AdjustmentMethod
    bar_trade_date: date

    @classmethod
    def from_domain(cls, snapshot: ChipDistributionSnapshot) -> ChipDistributionSnapshotDTO:
        return cls(
            as_of=snapshot.as_of,
            bins=tuple(ChipDistributionBinDTO.from_domain(b) for b in snapshot.bins),
            profit_ratio=snapshot.profit_ratio,
            average_cost=snapshot.average_cost,
            concentration_90=snapshot.concentration_90,
            concentration_70=snapshot.concentration_70,
            source_vendor=snapshot.source_vendor,
            reliability=snapshot.reliability,
            is_authoritative=snapshot.is_authoritative,
            calculation_method=snapshot.calculation_method,
            algorithm_version=snapshot.algorithm_version,
            lookback_sessions=snapshot.lookback_sessions,
            input_adjustment=snapshot.input_adjustment,
            bar_trade_date=snapshot.bar_trade_date,
        )


class UnlockRecordDTO(_FrozenForbid):
    unlock_date: date
    published_at: datetime | None
    unlock_type: str | None
    unlock_shares: int | None
    tradable_shares: int | None
    market_value_cny: DecimalWire | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: UnlockRecord) -> UnlockRecordDTO:
        return cls.model_validate(record, from_attributes=True)


class DividendRecordDTO(_FrozenForbid):
    fiscal_year: int
    plan_status: str
    ex_date: date | None
    cash_per_share: DecimalWire | None
    bonus_shares_per_share: DecimalWire | None
    transfer_shares_per_share: DecimalWire | None
    published_at: datetime | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    @classmethod
    def from_domain(cls, record: DividendRecord) -> DividendRecordDTO:
        return cls.model_validate(record, from_attributes=True)

