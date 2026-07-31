"""A-share quote, order-book, tick, and bar domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from domain.a_share.enums import BarInterval, TickDirection
from domain.a_share.model_validation import (
    _QUOTE_ASSET_TYPES,
    _require_a_share_instrument_id,
    _require_decimal,
    _require_enum,
    _require_int,
    _require_nonnegative_int,
    _require_optional_decimal,
    _require_optional_nonnegative_int,
)
from domain.common.enums import AdjustmentMethod, TradingSession
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# ---------------------------------------------------------------------------
# §4.1 Market structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AShareQuote:
    instrument_id: str
    quote_at: datetime
    session: TradingSession
    last: Decimal
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    previous_close: Decimal | None
    change: Decimal | None
    change_percent: Decimal | None
    volume_shares: int | None
    turnover_amount_cny: Decimal | None
    turnover_rate: Decimal | None
    pe_ttm: Decimal | None
    pb: Decimal | None
    total_market_cap_cny: Decimal | None
    float_market_cap_cny: Decimal | None
    limit_up_price: Decimal | None
    limit_down_price: Decimal | None

    def __post_init__(self) -> None:
        _require_a_share_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        require_aware_datetime(self.quote_at, field_name="quote_at")
        _require_enum(self.session, TradingSession, field="session")
        _require_decimal(self.last, field="last")
        for name in (
            "open",
            "high",
            "low",
            "previous_close",
            "change",
            "change_percent",
            "turnover_amount_cny",
            "turnover_rate",
            "pe_ttm",
            "pb",
            "total_market_cap_cny",
            "float_market_cap_cny",
            "limit_up_price",
            "limit_down_price",
        ):
            _require_optional_decimal(getattr(self, name), field=name)
        _require_optional_nonnegative_int(self.volume_shares, field="volume_shares")
        if self.high is not None and self.low is not None and self.high < self.low:
            raise DataContractError(
                "high must be >= low",
                details={"field": "high", "rule": "ohlc_high_ge_low"},
            )
        if (
            self.limit_up_price is not None
            and self.limit_down_price is not None
            and self.limit_up_price < self.limit_down_price
        ):
            raise DataContractError(
                "limit_up_price must be >= limit_down_price",
                details={"field": "limit_up_price", "rule": "limit_order"},
            )


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    level: int
    bid_price: Decimal | None
    bid_volume_shares: int | None
    ask_price: Decimal | None
    ask_volume_shares: int | None

    def __post_init__(self) -> None:
        level = _require_int(self.level, field="level")
        if level < 1 or level > 5:
            raise DataContractError(
                "level must be in 1..5",
                details={"field": "level", "rule": "level_range"},
            )
        _require_optional_decimal(self.bid_price, field="bid_price")
        _require_optional_decimal(self.ask_price, field="ask_price")
        _require_optional_nonnegative_int(self.bid_volume_shares, field="bid_volume_shares")
        _require_optional_nonnegative_int(self.ask_volume_shares, field="ask_volume_shares")


def validate_order_book_levels(levels: tuple[OrderBookLevel, ...]) -> None:
    """Require unique levels sorted ascending 1..5 (no gaps required)."""
    if not isinstance(levels, tuple):
        raise DataContractError(
            "order book levels must be a tuple",
            details={"field": "levels", "rule": "tuple_type"},
        )
    seen: set[int] = set()
    prev = 0
    for idx, level in enumerate(levels):
        if not isinstance(level, OrderBookLevel):
            raise DataContractError(
                "order book levels must be OrderBookLevel",
                details={"field": "levels", "index": idx, "rule": "type"},
            )
        if level.level in seen:
            raise DataContractError(
                "order book levels must be unique",
                details={"field": "levels", "rule": "unique_level"},
            )
        if level.level < prev:
            raise DataContractError(
                "order book levels must be sorted ascending",
                details={"field": "levels", "rule": "sorted_level"},
            )
        seen.add(level.level)
        prev = level.level


@dataclass(frozen=True, slots=True)
class TradeTick:
    occurred_at: datetime
    price: Decimal
    volume_shares: int
    direction: TickDirection

    def __post_init__(self) -> None:
        require_aware_datetime(self.occurred_at, field_name="occurred_at")
        _require_decimal(self.price, field="price")
        _require_nonnegative_int(self.volume_shares, field="volume_shares")
        _require_enum(self.direction, TickDirection, field="direction")


@dataclass(frozen=True, slots=True)
class AShareBar:
    start_at: datetime
    end_at: datetime
    interval: BarInterval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_shares: int
    turnover_amount_cny: Decimal | None
    adjustment: AdjustmentMethod

    def __post_init__(self) -> None:
        require_aware_datetime(self.start_at, field_name="start_at")
        require_aware_datetime(self.end_at, field_name="end_at")
        if self.end_at < self.start_at:
            raise DataContractError(
                "end_at must be >= start_at",
                details={"field": "end_at", "rule": "range_order"},
            )
        _require_enum(self.interval, BarInterval, field="interval")
        open_ = _require_decimal(self.open, field="open")
        high = _require_decimal(self.high, field="high")
        low = _require_decimal(self.low, field="low")
        close = _require_decimal(self.close, field="close")
        _require_nonnegative_int(self.volume_shares, field="volume_shares")
        _require_optional_decimal(self.turnover_amount_cny, field="turnover_amount_cny")
        _require_enum(self.adjustment, AdjustmentMethod, field="adjustment")
        if high < max(open_, close, low):
            raise DataContractError(
                "high must be >= max(open, close, low)",
                details={"field": "high", "rule": "ohlc_high"},
            )
        if low > min(open_, close, high):
            raise DataContractError(
                "low must be <= min(open, close, high)",
                details={"field": "low", "rule": "ohlc_low"},
            )


