"""A-share limit-pool, sentiment, and ETF-option output DTOs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from application.dto.a_share_common import _FrozenForbid
from application.dto.a_share_provenance import AShareComponentProvenanceDTO
from application.dto.market import DecimalWire
from domain.a_share.enums import LimitPoolType, OptionType, SentimentSourceType
from domain.a_share.models import (
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    LimitPoolEntry,
    LimitUpContext,
    LimitUpLadderRung,
    OptionGreeks,
    SentimentSignal,
)
from domain.common.enums import ReliabilityLevel, VendorId


class LimitPoolEntryDTO(_FrozenForbid):
    pool_type: LimitPoolType
    trade_date: date
    instrument_id: str
    name: str
    last: DecimalWire
    change_percent: DecimalWire
    consecutive_limit_count: int | None
    days_and_boards: str | None
    first_seal_at: datetime | None
    last_seal_at: datetime | None
    seal_amount_cny: DecimalWire | None
    broken_count: int | None
    industry: str | None
    reason_tags: tuple[str, ...]
    source_vendor: VendorId
    reliability: ReliabilityLevel

    @classmethod
    def from_domain(cls, entry: LimitPoolEntry) -> LimitPoolEntryDTO:
        return cls.model_validate(entry, from_attributes=True)


class LimitUpLadderRungDTO(_FrozenForbid):
    consecutive_limit_count: int
    instrument_count: int
    instrument_ids: tuple[str, ...]

    @classmethod
    def from_domain(cls, rung: LimitUpLadderRung) -> LimitUpLadderRungDTO:
        return cls.model_validate(rung, from_attributes=True)


class LimitUpContextDTO(_FrozenForbid):
    trade_date: date
    entries: tuple[LimitPoolEntryDTO, ...]
    limit_up_count: int
    limit_down_count: int
    broken_limit_count: int
    broken_rate: DecimalWire | None
    max_consecutive_count: int | None
    promotion_rate: DecimalWire | None
    ladder: tuple[LimitUpLadderRungDTO, ...]

    @classmethod
    def from_domain(cls, context: LimitUpContext) -> LimitUpContextDTO:
        return cls(
            trade_date=context.trade_date,
            entries=tuple(LimitPoolEntryDTO.from_domain(e) for e in context.entries),
            limit_up_count=context.limit_up_count,
            limit_down_count=context.limit_down_count,
            broken_limit_count=context.broken_limit_count,
            broken_rate=context.broken_rate,
            max_consecutive_count=context.max_consecutive_count,
            promotion_rate=context.promotion_rate,
            ladder=tuple(LimitUpLadderRungDTO.from_domain(r) for r in context.ladder),
        )


class SentimentSignalDTO(_FrozenForbid):
    source_type: SentimentSourceType
    trade_date: date
    instrument_id: str | None
    rank: int | None
    rank_change: int | None
    heat_value: DecimalWire | None
    concept_tags: tuple[str, ...]
    label: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool
    source_item_id: str | None
    observed_at: datetime | None

    @classmethod
    def from_domain(cls, signal: SentimentSignal) -> SentimentSignalDTO:
        return cls.model_validate(signal, from_attributes=True)


class EtfOptionContractDTO(_FrozenForbid):
    instrument_id: str
    underlying_instrument_id: str
    option_type: OptionType
    expiry: date
    strike: DecimalWire
    multiplier: DecimalWire | None

    @classmethod
    def from_domain(cls, contract: EtfOptionContract) -> EtfOptionContractDTO:
        return cls.model_validate(contract, from_attributes=True)


class EtfOptionQuoteDTO(_FrozenForbid):
    contract: EtfOptionContractDTO
    quote_at: datetime
    last: DecimalWire | None
    bid_prices: tuple[DecimalWire, ...]
    bid_volumes: tuple[int, ...]
    ask_prices: tuple[DecimalWire, ...]
    ask_volumes: tuple[int, ...]
    volume_contracts: int | None
    open_interest: int | None

    @classmethod
    def from_domain(cls, quote: EtfOptionQuote) -> EtfOptionQuoteDTO:
        return cls(
            contract=EtfOptionContractDTO.from_domain(quote.contract),
            quote_at=quote.quote_at,
            last=quote.last,
            bid_prices=quote.bid_prices,
            bid_volumes=quote.bid_volumes,
            ask_prices=quote.ask_prices,
            ask_volumes=quote.ask_volumes,
            volume_contracts=quote.volume_contracts,
            open_interest=quote.open_interest,
        )


class OptionGreeksDTO(_FrozenForbid):
    contract_instrument_id: str
    as_of: datetime
    delta: DecimalWire | None
    gamma: DecimalWire | None
    theta: DecimalWire | None
    vega: DecimalWire | None
    implied_volatility: DecimalWire | None
    theoretical_value: DecimalWire | None
    source_provided: Literal[True] = True

    @classmethod
    def from_domain(cls, greeks: OptionGreeks) -> OptionGreeksDTO:
        return cls.model_validate(greeks, from_attributes=True)


class EtfOptionSnapshotDTO(_FrozenForbid):
    underlying_instrument_id: str
    expiry: date
    quotes: tuple[EtfOptionQuoteDTO, ...]
    greeks: tuple[OptionGreeksDTO, ...]
    provenance: tuple[AShareComponentProvenanceDTO, ...]

    @classmethod
    def from_domain(
        cls,
        snapshot: EtfOptionSnapshot,
        *,
        provenance: tuple[AShareComponentProvenanceDTO, ...],
    ) -> EtfOptionSnapshotDTO:
        if snapshot.expiry is None:
            raise ValueError("successful ETF option snapshot requires exact expiry")
        return cls(
            underlying_instrument_id=snapshot.underlying_instrument_id,
            expiry=snapshot.expiry,
            quotes=tuple(EtfOptionQuoteDTO.from_domain(q) for q in snapshot.quotes),
            greeks=tuple(OptionGreeksDTO.from_domain(g) for g in snapshot.greeks),
            provenance=provenance,
        )

