"""A-share capital-flow, corporate-action, and chip domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from domain.a_share.enums import BarInterval
from domain.a_share.model_validation import (
    _BRANCH_MAX,
    _CHANNEL_MAX,
    _DISCLOSURE_NOTE_MAX,
    _DRAGON_TIGER_SIDES,
    _EQUITY_ONLY,
    _NORTHBOUND_CHANNELS,
    _PLAN_STATUS_MAX,
    _REASON_MAX,
    _UNLOCK_TYPE_MAX,
    _require_a_share_instrument_id,
    _require_bool,
    _require_date,
    _require_decimal,
    _require_enum,
    _require_int,
    _require_optional_date,
    _require_optional_decimal,
    _require_optional_nonnegative_int,
    _require_optional_ratio,
    _require_optional_str,
    _require_positive_int,
    _require_ratio,
    _require_reliability,
    _require_str,
    _require_tuple,
    _require_vendor,
)
from domain.common.enums import AdjustmentMethod, ReliabilityLevel, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# ---------------------------------------------------------------------------
# §17.2 Capital / chips
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundFlowPoint:
    occurred_at: datetime
    interval: BarInterval
    main_net_cny: Decimal | None
    super_large_net_cny: Decimal | None
    large_net_cny: Decimal | None
    medium_net_cny: Decimal | None
    small_net_cny: Decimal | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        require_aware_datetime(self.occurred_at, field_name="occurred_at")
        _require_enum(self.interval, BarInterval, field="interval")
        for name in (
            "main_net_cny",
            "super_large_net_cny",
            "large_net_cny",
            "medium_net_cny",
            "small_net_cny",
        ):
            _require_optional_decimal(getattr(self, name), field=name)
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")


@dataclass(frozen=True, slots=True)
class NorthboundFlowPoint:
    trade_date: date
    channel: str
    net_buy_cny: Decimal | None
    buy_cny: Decimal | None
    sell_cny: Decimal | None
    disclosure_note: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        _require_date(self.trade_date, field="trade_date")
        channel = _require_str(self.channel, field="channel", max_len=_CHANNEL_MAX)
        if channel not in _NORTHBOUND_CHANNELS:
            raise DataContractError(
                "channel must be sh|sz|total|connect",
                details={"field": "channel", "rule": "northbound_channel"},
            )
        for name in ("net_buy_cny", "buy_cny", "sell_cny"):
            _require_optional_decimal(getattr(self, name), field=name)
        _require_optional_str(
            self.disclosure_note, field="disclosure_note", max_len=_DISCLOSURE_NOTE_MAX
        )
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")


@dataclass(frozen=True, slots=True)
class DragonTigerSeat:
    rank: int
    side: str
    branch_name: str
    amount_cny: Decimal
    is_institution: bool | None

    def __post_init__(self) -> None:
        _require_positive_int(self.rank, field="rank")
        side = _require_str(self.side, field="side", max_len=16)
        if side not in _DRAGON_TIGER_SIDES:
            raise DataContractError(
                "side must be buy|sell",
                details={"field": "side", "rule": "dragon_tiger_side"},
            )
        _require_str(self.branch_name, field="branch_name", max_len=_BRANCH_MAX)
        _require_decimal(self.amount_cny, field="amount_cny")
        if self.is_institution is not None:
            _require_bool(self.is_institution, field="is_institution")


@dataclass(frozen=True, slots=True)
class DragonTigerRecord:
    trade_date: date
    instrument_id: str
    reason: str
    buy_total_cny: Decimal
    sell_total_cny: Decimal
    net_buy_cny: Decimal
    seats: tuple[DragonTigerSeat, ...]
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        _require_date(self.trade_date, field="trade_date")
        _require_a_share_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_EQUITY_ONLY,
        )
        _require_str(self.reason, field="reason", max_len=_REASON_MAX)
        buy = _require_decimal(self.buy_total_cny, field="buy_total_cny")
        sell = _require_decimal(self.sell_total_cny, field="sell_total_cny")
        net = _require_decimal(self.net_buy_cny, field="net_buy_cny")
        if net != buy - sell:
            raise DataContractError(
                "net_buy_cny must equal buy_total_cny - sell_total_cny",
                details={"field": "net_buy_cny", "rule": "net_consistency"},
            )
        seats = _require_tuple(self.seats, field="seats")
        for idx, seat in enumerate(seats):
            if not isinstance(seat, DragonTigerSeat):
                raise DataContractError(
                    "seats elements must be DragonTigerSeat",
                    details={"field": "seats", "index": idx, "rule": "type"},
                )
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")


@dataclass(frozen=True, slots=True)
class MarginRecord:
    trade_date: date
    financing_balance_cny: Decimal
    financing_buy_cny: Decimal
    financing_repayment_cny: Decimal
    securities_lending_balance_cny: Decimal | None
    securities_lending_sell_shares: int | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        _require_date(self.trade_date, field="trade_date")
        for name in (
            "financing_balance_cny",
            "financing_buy_cny",
            "financing_repayment_cny",
        ):
            _require_decimal(getattr(self, name), field=name)
        _require_optional_decimal(
            self.securities_lending_balance_cny,
            field="securities_lending_balance_cny",
        )
        _require_optional_nonnegative_int(
            self.securities_lending_sell_shares,
            field="securities_lending_sell_shares",
        )
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")


@dataclass(frozen=True, slots=True)
class BlockTradeRecord:
    trade_date: date
    price: Decimal
    volume_shares: int
    amount_cny: Decimal
    premium_percent: Decimal | None
    buyer_branch: str | None
    seller_branch: str | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        _require_date(self.trade_date, field="trade_date")
        _require_decimal(self.price, field="price")
        _require_positive_int(self.volume_shares, field="volume_shares")
        _require_decimal(self.amount_cny, field="amount_cny")
        _require_optional_decimal(self.premium_percent, field="premium_percent")
        _require_optional_str(self.buyer_branch, field="buyer_branch", max_len=_BRANCH_MAX)
        _require_optional_str(self.seller_branch, field="seller_branch", max_len=_BRANCH_MAX)
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")


@dataclass(frozen=True, slots=True)
class ShareholderCountRecord:
    period_end: date
    published_at: datetime | None
    shareholder_count: int
    change_percent: Decimal | None
    average_holding_shares: Decimal | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        _require_date(self.period_end, field="period_end")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _require_positive_int(self.shareholder_count, field="shareholder_count")
        _require_optional_decimal(self.change_percent, field="change_percent")
        _require_optional_decimal(self.average_holding_shares, field="average_holding_shares")
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")


@dataclass(frozen=True, slots=True)
class ChipDistributionBin:
    price_low: Decimal
    price_high: Decimal
    holding_ratio: Decimal

    def __post_init__(self) -> None:
        low = _require_decimal(self.price_low, field="price_low")
        high = _require_decimal(self.price_high, field="price_high")
        if high < low:
            raise DataContractError(
                "price_high must be >= price_low",
                details={"field": "price_high", "rule": "range_order"},
            )
        _require_ratio(self.holding_ratio, field="holding_ratio")


@dataclass(frozen=True, slots=True)
class ChipDistributionSnapshot:
    """Derived chip estimate with relative cost-band width metrics.

    ``concentration_90`` and ``concentration_70`` are relative cost-band
    widths, not increasing concentration scores: a lower value means the
    estimated holdings are more concentrated.
    """

    as_of: datetime
    bins: tuple[ChipDistributionBin, ...]
    profit_ratio: Decimal | None
    average_cost: Decimal | None
    concentration_90: Decimal | None
    concentration_70: Decimal | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool
    calculation_method: str
    algorithm_version: str
    lookback_sessions: int
    input_adjustment: AdjustmentMethod
    bar_trade_date: date

    def __post_init__(self) -> None:
        require_aware_datetime(self.as_of, field_name="as_of")
        bins = _require_tuple(self.bins, field="bins")
        if not bins:
            raise DataContractError(
                "chip bins must not be empty", details={"field": "bins", "rule": "non_empty"}
            )
        prev_high: Decimal | None = None
        for idx, bin_row in enumerate(bins):
            if not isinstance(bin_row, ChipDistributionBin):
                raise DataContractError(
                    "bins elements must be ChipDistributionBin",
                    details={"field": "bins", "index": idx, "rule": "type"},
                )
            if prev_high is not None and bin_row.price_low < prev_high:
                raise DataContractError(
                    "chip bins must be non-overlapping and ordered by price",
                    details={"field": "bins", "rule": "sorted_non_overlap"},
                )
            prev_high = bin_row.price_high
            if bin_row.price_low <= 0 or bin_row.price_high <= 0:
                raise DataContractError(
                    "chip bin prices must be positive",
                    details={"field": "bins", "index": idx, "rule": "positive_price"},
                )
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            total = sum(
                (b.holding_ratio for b in bins if isinstance(b, ChipDistributionBin)),
                Decimal(0),
            )
            quantized_total = total.quantize(Decimal("0.000000000001"))
        if quantized_total != Decimal(1):
            raise DataContractError(
                "chip holding ratios must sum to one",
                details={"field": "bins", "rule": "holding_sum"},
            )
        if self.profit_ratio is None:
            raise DataContractError(
                "profit_ratio is required", details={"field": "profit_ratio", "rule": "required"}
            )
        _require_optional_ratio(self.profit_ratio, field="profit_ratio")
        average_cost = _require_optional_decimal(self.average_cost, field="average_cost")
        if average_cost is None or average_cost <= 0:
            raise DataContractError(
                "average_cost must be positive",
                details={"field": "average_cost", "rule": "required_positive"},
            )
        if self.concentration_90 is None or self.concentration_70 is None:
            raise DataContractError(
                "chip concentration is required",
                details={"field": "concentration", "rule": "required"},
            )
        _require_optional_ratio(self.concentration_90, field="concentration_90")
        _require_optional_ratio(self.concentration_70, field="concentration_70")
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")
        if self.source_vendor is not VendorId.EASTMONEY:
            raise DataContractError(
                "chip source_vendor must be eastmoney",
                details={"field": "source_vendor", "rule": "exact"},
            )
        if self.reliability is not ReliabilityLevel.LOW:
            raise DataContractError(
                "chip reliability must be low", details={"field": "reliability", "rule": "exact"}
            )
        if self.is_authoritative is not False:
            raise DataContractError(
                "chip is_authoritative must be false",
                details={"field": "is_authoritative", "rule": "exact"},
            )
        if self.calculation_method != "turnover_decay_uniform_range":
            raise DataContractError(
                "chip calculation method mismatch",
                details={"field": "calculation_method", "rule": "exact"},
            )
        if self.algorithm_version != "tp_chip_v1":
            raise DataContractError(
                "chip algorithm version mismatch",
                details={"field": "algorithm_version", "rule": "exact"},
            )
        if self.lookback_sessions != 120:
            raise DataContractError(
                "chip lookback mismatch", details={"field": "lookback_sessions", "rule": "exact"}
            )
        if self.input_adjustment is not AdjustmentMethod.FORWARD_ADJUSTED:
            raise DataContractError(
                "chip input adjustment must be forward adjusted", details={"rule": "adjustment"}
            )
        _require_date(self.bar_trade_date, field="bar_trade_date")


@dataclass(frozen=True, slots=True)
class UnlockRecord:
    unlock_date: date
    published_at: datetime | None
    unlock_type: str | None
    unlock_shares: int | None
    tradable_shares: int | None
    market_value_cny: Decimal | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        _require_date(self.unlock_date, field="unlock_date")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _require_optional_str(self.unlock_type, field="unlock_type", max_len=_UNLOCK_TYPE_MAX)
        _require_optional_nonnegative_int(self.unlock_shares, field="unlock_shares")
        _require_optional_nonnegative_int(self.tradable_shares, field="tradable_shares")
        _require_optional_decimal(self.market_value_cny, field="market_value_cny")
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")


@dataclass(frozen=True, slots=True)
class DividendRecord:
    fiscal_year: int
    plan_status: str
    ex_date: date | None
    cash_per_share: Decimal | None
    bonus_shares_per_share: Decimal | None
    transfer_shares_per_share: Decimal | None
    published_at: datetime | None
    source_vendor: VendorId
    reliability: ReliabilityLevel
    is_authoritative: bool

    def __post_init__(self) -> None:
        year = _require_int(self.fiscal_year, field="fiscal_year")
        if year < 1990 or year > 2100:
            raise DataContractError(
                "fiscal_year out of range",
                details={"field": "fiscal_year", "rule": "year_range"},
            )
        _require_str(self.plan_status, field="plan_status", max_len=_PLAN_STATUS_MAX)
        _require_optional_date(self.ex_date, field="ex_date")
        for name in (
            "cash_per_share",
            "bonus_shares_per_share",
            "transfer_shares_per_share",
        ):
            _require_optional_decimal(getattr(self, name), field=name)
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _require_vendor(self.source_vendor)
        _require_reliability(self.reliability)
        _require_bool(self.is_authoritative, field="is_authoritative")
