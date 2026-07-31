"""A-share limit-pool, market-sentiment, and ETF-option domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.a_share.enums import LimitPoolType, OptionType, SentimentSourceType
from domain.a_share.model_validation import (
    _DAYS_BOARDS_MAX,
    _EQUITY_ONLY,
    _ETF_ONLY,
    _INDUSTRY_MAX,
    _LABEL_MAX,
    _NAME_MAX,
    _OPTION_ONLY,
    _QUOTE_ASSET_TYPES,
    _TAG_MAX,
    _require_a_share_instrument_id,
    _require_bool,
    _require_date,
    _require_decimal,
    _require_decimal_tuple,
    _require_enum,
    _require_int,
    _require_int_tuple,
    _require_nonnegative_int,
    _require_optional_a_share_instrument_id,
    _require_optional_date,
    _require_optional_decimal,
    _require_optional_nonnegative_int,
    _require_optional_ratio,
    _require_optional_str,
    _require_positive_int,
    _require_reliability,
    _require_str,
    _require_str_tuple,
    _require_tuple,
    _require_vendor,
)
from domain.common.enums import ReliabilityLevel, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# ---------------------------------------------------------------------------
# §17.3 Limit-up / sentiment / options
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LimitPoolEntry:
    pool_type: LimitPoolType
    trade_date: date
    instrument_id: str
    name: str
    last: Decimal
    change_percent: Decimal
    consecutive_limit_count: int | None
    days_and_boards: str | None
    first_seal_at: datetime | None
    last_seal_at: datetime | None
    seal_amount_cny: Decimal | None
    broken_count: int | None
    industry: str | None
    reason_tags: tuple[str, ...]
    source_vendor: VendorId
    reliability: ReliabilityLevel

    def __post_init__(self) -> None:
        _require_enum(self.pool_type, LimitPoolType, field="pool_type")
        _require_date(self.trade_date, field="trade_date")
        _require_a_share_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_EQUITY_ONLY,
        )
        _require_str(self.name, field="name", max_len=_NAME_MAX)
        _require_decimal(self.last, field="last")
        _require_decimal(self.change_percent, field="change_percent")
        _require_optional_nonnegative_int(
            self.consecutive_limit_count, field="consecutive_limit_count"
        )
        _require_optional_str(
            self.days_and_boards, field="days_and_boards", max_len=_DAYS_BOARDS_MAX
        )
        if self.first_seal_at is not None:
            require_aware_datetime(self.first_seal_at, field_name="first_seal_at")
        if self.last_seal_at is not None:
            require_aware_datetime(self.last_seal_at, field_name="last_seal_at")
        if (
            self.first_seal_at is not None
            and self.last_seal_at is not None
            and self.last_seal_at < self.first_seal_at
        ):
            raise DataContractError(
                "last_seal_at must be >= first_seal_at",
                details={"field": "last_seal_at", "rule": "range_order"},
            )
        _require_optional_decimal(self.seal_amount_cny, field="seal_amount_cny")
        _require_optional_nonnegative_int(self.broken_count, field="broken_count")
        _require_optional_str(self.industry, field="industry", max_len=_INDUSTRY_MAX)
        tags = _require_str_tuple(self.reason_tags, field="reason_tags", max_item_len=_TAG_MAX)
        object.__setattr__(self, "reason_tags", tags)
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)


@dataclass(frozen=True, slots=True)
class LimitUpLadderRung:
    consecutive_limit_count: int
    instrument_count: int
    instrument_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_positive_int(self.consecutive_limit_count, field="consecutive_limit_count")
        count = _require_nonnegative_int(self.instrument_count, field="instrument_count")
        ids = _require_tuple(self.instrument_ids, field="instrument_ids")
        if len(ids) != count:
            raise DataContractError(
                "instrument_ids length must equal instrument_count",
                details={"field": "instrument_ids", "rule": "count_match"},
            )
        seen: set[str] = set()
        out: list[str] = []
        for idx, instrument_id in enumerate(ids):
            text = _require_a_share_instrument_id(
                instrument_id,
                field=f"instrument_ids[{idx}]",
                allowed_assets=_EQUITY_ONLY,
            )
            if text in seen:
                raise DataContractError(
                    "instrument_ids must be unique",
                    details={"field": "instrument_ids", "rule": "unique"},
                )
            seen.add(text)
            out.append(text)
        object.__setattr__(self, "instrument_ids", tuple(out))


@dataclass(frozen=True, slots=True)
class LimitUpContext:
    trade_date: date
    entries: tuple[LimitPoolEntry, ...]
    limit_up_count: int
    limit_down_count: int
    broken_limit_count: int
    broken_rate: Decimal | None
    max_consecutive_count: int | None
    promotion_rate: Decimal | None
    ladder: tuple[LimitUpLadderRung, ...]

    def __post_init__(self) -> None:
        _require_date(self.trade_date, field="trade_date")
        entries = _require_tuple(self.entries, field="entries")
        seen_keys: set[tuple[str, str]] = set()
        for idx, entry in enumerate(entries):
            if not isinstance(entry, LimitPoolEntry):
                raise DataContractError(
                    "entries elements must be LimitPoolEntry",
                    details={"field": "entries", "index": idx, "rule": "type"},
                )
            key = (entry.pool_type.value, entry.instrument_id)
            if key in seen_keys:
                raise DataContractError(
                    "entries must be unique by pool_type+instrument_id",
                    details={"field": "entries", "rule": "unique"},
                )
            seen_keys.add(key)
        for name in ("limit_up_count", "limit_down_count", "broken_limit_count"):
            _require_nonnegative_int(getattr(self, name), field=name)
        _require_optional_ratio(self.broken_rate, field="broken_rate")
        _require_optional_nonnegative_int(self.max_consecutive_count, field="max_consecutive_count")
        _require_optional_ratio(self.promotion_rate, field="promotion_rate")
        ladder = _require_tuple(self.ladder, field="ladder")
        prev_count = 0
        for idx, rung in enumerate(ladder):
            if not isinstance(rung, LimitUpLadderRung):
                raise DataContractError(
                    "ladder elements must be LimitUpLadderRung",
                    details={"field": "ladder", "index": idx, "rule": "type"},
                )
            if rung.consecutive_limit_count < prev_count:
                raise DataContractError(
                    "ladder must be sorted by consecutive_limit_count ascending",
                    details={"field": "ladder", "rule": "sorted"},
                )
            prev_count = rung.consecutive_limit_count


@dataclass(frozen=True, slots=True)
class SentimentSignal:
    source_type: SentimentSourceType
    trade_date: date
    instrument_id: str | None
    rank: int | None
    rank_change: int | None
    heat_value: Decimal | None
    concept_tags: tuple[str, ...]
    label: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool = False
    source_item_id: str | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_enum(self.source_type, SentimentSourceType, field="source_type")
        _require_date(self.trade_date, field="trade_date")
        _require_optional_a_share_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        _require_optional_nonnegative_int(self.rank, field="rank")
        if self.rank_change is not None:
            _require_int(self.rank_change, field="rank_change")
        _require_optional_decimal(self.heat_value, field="heat_value")
        tags = _require_str_tuple(self.concept_tags, field="concept_tags", max_item_len=_TAG_MAX)
        object.__setattr__(self, "concept_tags", tags)
        _require_optional_str(self.label, field="label", max_len=_LABEL_MAX)
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")
        _require_optional_str(self.source_item_id, field="source_item_id", max_len=_LABEL_MAX)
        if self.observed_at is not None:
            require_aware_datetime(self.observed_at, field_name="observed_at")
        # Heat/rank signals cannot claim authority (design §17.3 freezes False).
        if self.is_authoritative is not False:
            raise DataContractError(
                "SentimentSignal.is_authoritative must be False",
                details={
                    "field": "is_authoritative",
                    "rule": "sentiment_not_authoritative",
                },
            )


@dataclass(frozen=True, slots=True)
class EtfOptionContract:
    instrument_id: str
    underlying_instrument_id: str
    option_type: OptionType
    expiry: date
    strike: Decimal
    multiplier: Decimal | None

    def __post_init__(self) -> None:
        _require_a_share_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_OPTION_ONLY,
        )
        _require_a_share_instrument_id(
            self.underlying_instrument_id,
            field="underlying_instrument_id",
            allowed_assets=_ETF_ONLY,
        )
        _require_enum(self.option_type, OptionType, field="option_type")
        _require_date(self.expiry, field="expiry")
        strike = _require_decimal(self.strike, field="strike")
        if strike <= 0:
            raise DataContractError(
                "strike must be positive",
                details={"field": "strike", "rule": "positive"},
            )
        mult = _require_optional_decimal(self.multiplier, field="multiplier")
        if mult is not None and mult <= 0:
            raise DataContractError(
                "multiplier must be positive when set",
                details={"field": "multiplier", "rule": "positive"},
            )


@dataclass(frozen=True, slots=True)
class EtfOptionQuote:
    contract: EtfOptionContract
    quote_at: datetime
    last: Decimal | None
    bid_prices: tuple[Decimal, ...]
    bid_volumes: tuple[int, ...]
    ask_prices: tuple[Decimal, ...]
    ask_volumes: tuple[int, ...]
    volume_contracts: int | None
    open_interest: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.contract, EtfOptionContract):
            raise DataContractError(
                "contract must be EtfOptionContract",
                details={"field": "contract", "rule": "type"},
            )
        require_aware_datetime(self.quote_at, field_name="quote_at")
        _require_optional_decimal(self.last, field="last")
        bids = _require_decimal_tuple(self.bid_prices, field="bid_prices")
        bid_vols = _require_int_tuple(self.bid_volumes, field="bid_volumes")
        asks = _require_decimal_tuple(self.ask_prices, field="ask_prices")
        ask_vols = _require_int_tuple(self.ask_volumes, field="ask_volumes")
        if len(bids) != len(bid_vols):
            raise DataContractError(
                "bid_prices and bid_volumes length must match",
                details={"field": "bid_volumes", "rule": "length_match"},
            )
        if len(asks) != len(ask_vols):
            raise DataContractError(
                "ask_prices and ask_volumes length must match",
                details={"field": "ask_volumes", "rule": "length_match"},
            )
        object.__setattr__(self, "bid_prices", bids)
        object.__setattr__(self, "bid_volumes", bid_vols)
        object.__setattr__(self, "ask_prices", asks)
        object.__setattr__(self, "ask_volumes", ask_vols)
        _require_optional_nonnegative_int(self.volume_contracts, field="volume_contracts")
        _require_optional_nonnegative_int(self.open_interest, field="open_interest")


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    contract_instrument_id: str
    as_of: datetime
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    implied_volatility: Decimal | None
    theoretical_value: Decimal | None
    source_provided: bool = True

    def __post_init__(self) -> None:
        _require_a_share_instrument_id(
            self.contract_instrument_id,
            field="contract_instrument_id",
            allowed_assets=_OPTION_ONLY,
        )
        require_aware_datetime(self.as_of, field_name="as_of")
        for name in (
            "delta",
            "gamma",
            "theta",
            "vega",
            "implied_volatility",
            "theoretical_value",
        ):
            _require_optional_decimal(getattr(self, name), field=name)
        _require_bool(self.source_provided, field="source_provided")
        if self.source_provided is not True:
            raise DataContractError(
                "source_provided must be True in Phase 1E (no local Greeks)",
                details={"field": "source_provided", "rule": "source_provided_true"},
            )


@dataclass(frozen=True, slots=True)
class EtfOptionSnapshot:
    underlying_instrument_id: str
    expiry: date | None
    quotes: tuple[EtfOptionQuote, ...]
    greeks: tuple[OptionGreeks, ...]

    def __post_init__(self) -> None:
        _require_a_share_instrument_id(
            self.underlying_instrument_id,
            field="underlying_instrument_id",
            allowed_assets=_ETF_ONLY,
        )
        _require_optional_date(self.expiry, field="expiry")
        quotes = _require_tuple(self.quotes, field="quotes")
        quote_ids: set[str] = set()
        for idx, quote in enumerate(quotes):
            if not isinstance(quote, EtfOptionQuote):
                raise DataContractError(
                    "quotes elements must be EtfOptionQuote",
                    details={"field": "quotes", "index": idx, "rule": "type"},
                )
            cid = quote.contract.instrument_id
            if cid in quote_ids:
                raise DataContractError(
                    "quotes must be unique by contract instrument_id",
                    details={"field": "quotes", "rule": "unique"},
                )
            quote_ids.add(cid)
            if quote.contract.underlying_instrument_id != self.underlying_instrument_id:
                raise DataContractError(
                    "quote underlying must match snapshot underlying",
                    details={"field": "quotes", "rule": "underlying_match"},
                )
            if self.expiry is not None and quote.contract.expiry != self.expiry:
                raise DataContractError(
                    "quote contract expiry must match snapshot expiry when set",
                    details={
                        "field": "quotes",
                        "rule": "expiry_match",
                        "index": idx,
                        "snapshot_expiry": self.expiry.isoformat(),
                        "contract_expiry": quote.contract.expiry.isoformat(),
                    },
                )
        greeks = _require_tuple(self.greeks, field="greeks")
        greek_ids: set[str] = set()
        for idx, greek in enumerate(greeks):
            if not isinstance(greek, OptionGreeks):
                raise DataContractError(
                    "greeks elements must be OptionGreeks",
                    details={"field": "greeks", "index": idx, "rule": "type"},
                )
            if greek.contract_instrument_id in greek_ids:
                raise DataContractError(
                    "greeks must be unique by contract_instrument_id",
                    details={"field": "greeks", "rule": "unique"},
                )
            greek_ids.add(greek.contract_instrument_id)

