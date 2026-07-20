"""Frozen A-share domain models (Phase 1E §§4 / 17).

All models are ``@dataclass(frozen=True, slots=True)``. Numerics are
``Decimal`` (no float). Datetimes are timezone-aware. Nested sequences are
immutable tuples. Range, order, and uniqueness invariants live in
``__post_init__``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from domain.a_share.enums import (
    BarInterval,
    FinancialStatementType,
    LimitPoolType,
    OptionType,
    SentimentSourceType,
    TickDirection,
)
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    Market,
    ReliabilityLevel,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id

# Asset types intrinsic to A-share market models (design §19 matrix).
_QUOTE_ASSET_TYPES = frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.INDEX})
_EQUITY_ONLY = frozenset({AssetType.EQUITY})
_OPTION_ONLY = frozenset({AssetType.OPTION})
_ETF_ONLY = frozenset({AssetType.ETF})

_F10_BODY_MAX = 20_000
_TITLE_MAX = 500
_KEY_MAX = 200
_NAME_MAX = 200
_SECTION_MAX = 100
_URL_MAX = 2_000
_TAG_MAX = 100
_REASON_MAX = 500
_DISCLOSURE_NOTE_MAX = 2_000
_METRIC_NAME_MAX = 100
_UNIT_MAX = 50
_ITEM_CODE_MAX = 64
_ITEM_NAME_MAX = 200
_CHANNEL_MAX = 32
_BRANCH_MAX = 200
_LABEL_MAX = 200
_PLAN_STATUS_MAX = 64
_UNLOCK_TYPE_MAX = 64
_DAYS_BOARDS_MAX = 64
_INDUSTRY_MAX = 100

_NORTHBOUND_CHANNELS = frozenset({"sh", "sz", "total", "connect"})
_CONSENSUS_METRICS = frozenset({"eps", "revenue", "net_income"})
_DRAGON_TIGER_SIDES = frozenset({"buy", "sell"})


def _reject_float(value: object, *, field: str) -> None:
    if isinstance(value, float):
        raise DataContractError(
            f"{field} must not be float; use Decimal",
            details={"field": field, "rule": "no_float"},
        )


def _require_decimal(value: object, *, field: str) -> Decimal:
    _reject_float(value, field=field)
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={"field": field, "rule": "decimal_type", "type": type(value).__name__},
        )
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal",
            details={"field": field, "rule": "finite_decimal"},
        )
    return value


def _require_optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _require_decimal(value, field=field)


def _require_int(value: object, *, field: str) -> int:
    _reject_float(value, field=field)
    if type(value) is not int:
        raise DataContractError(
            f"{field} must be an int",
            details={"field": field, "rule": "int_type", "type": type(value).__name__},
        )
    return value


def _require_optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, field=field)


def _require_nonnegative_int(value: object, *, field: str) -> int:
    number = _require_int(value, field=field)
    if number < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field, "rule": "nonnegative"},
        )
    return number


def _require_optional_nonnegative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _require_nonnegative_int(value, field=field)


def _require_positive_int(value: object, *, field: str) -> int:
    number = _require_int(value, field=field)
    if number < 1:
        raise DataContractError(
            f"{field} must be positive",
            details={"field": field, "rule": "positive"},
        )
    return number


def _require_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise DataContractError(
            f"{field} must be a bool",
            details={"field": field, "rule": "bool_type", "type": type(value).__name__},
        )
    return value


def _require_date(value: object, *, field: str) -> date:
    if type(value) is not date:
        raise DataContractError(
            f"{field} must be a date",
            details={"field": field, "rule": "date_type", "type": type(value).__name__},
        )
    return value


def _require_optional_date(value: object, *, field: str) -> date | None:
    if value is None:
        return None
    return _require_date(value, field=field)


def _require_str(value: object, *, field: str, max_len: int, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be a string",
            details={"field": field, "rule": "str_type", "type": type(value).__name__},
        )
    if not allow_blank and (not value or not value.strip()):
        raise DataContractError(
            f"{field} must be a non-blank string",
            details={"field": field, "rule": "non_blank"},
        )
    if len(value) > max_len:
        raise DataContractError(
            f"{field} exceeds max length",
            details={"field": field, "rule": "max_length", "max": max_len, "length": len(value)},
        )
    return value


def _require_optional_str(value: object, *, field: str, max_len: int) -> str | None:
    if value is None:
        return None
    return _require_str(value, field=field, max_len=max_len)


def _require_tuple(value: object, *, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise DataContractError(
            f"{field} must be a tuple",
            details={"field": field, "rule": "tuple_type", "type": type(value).__name__},
        )
    return value


def _require_instrument_id(value: object, *, field: str) -> str:
    text = _require_str(value, field=field, max_len=128)
    parse_instrument_id(text)
    return text


def _require_a_share_instrument_id(
    value: object,
    *,
    field: str,
    allowed_assets: frozenset[AssetType] | None = None,
) -> str:
    """Require a well-formed instrument_id with Market.A_SHARE (and optional assets)."""
    text = _require_instrument_id(value, field=field)
    asset_type, market, _symbol = parse_instrument_id(text)
    if market is not Market.A_SHARE:
        raise DataContractError(
            f"{field} must use Market.A_SHARE",
            details={
                "field": field,
                "rule": "a_share_market",
                "market": market.value,
                "instrument_id": text,
            },
        )
    if allowed_assets is not None and asset_type not in allowed_assets:
        raise DataContractError(
            f"{field} asset type not allowed for this model",
            details={
                "field": field,
                "rule": "a_share_asset_type",
                "asset_type": asset_type.value,
                "allowed": sorted(a.value for a in allowed_assets),
                "instrument_id": text,
            },
        )
    return text


def _require_optional_a_share_instrument_id(
    value: object,
    *,
    field: str,
    allowed_assets: frozenset[AssetType] | None = None,
) -> str | None:
    if value is None:
        return None
    return _require_a_share_instrument_id(value, field=field, allowed_assets=allowed_assets)


def _require_ratio(value: object, *, field: str) -> Decimal:
    ratio = _require_decimal(value, field=field)
    if ratio < 0 or ratio > 1:
        raise DataContractError(
            f"{field} must be in [0, 1]",
            details={"field": field, "rule": "ratio_range"},
        )
    return ratio


def _require_optional_ratio(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _require_ratio(value, field=field)


def _require_vendor(value: object, *, field: str = "source_vendor") -> VendorId:
    if not isinstance(value, VendorId):
        raise DataContractError(
            f"{field} must be a VendorId",
            details={"field": field, "rule": "vendor_type", "type": type(value).__name__},
        )
    return value


def _require_reliability(value: object, *, field: str = "reliability") -> ReliabilityLevel:
    if not isinstance(value, ReliabilityLevel):
        raise DataContractError(
            f"{field} must be a ReliabilityLevel",
            details={
                "field": field,
                "rule": "reliability_type",
                "type": type(value).__name__,
            },
        )
    return value


def _require_enum[T](value: object, enum_type: type[T], *, field: str) -> T:
    if not isinstance(value, enum_type):
        raise DataContractError(
            f"{field} must be a {enum_type.__name__}",
            details={
                "field": field,
                "rule": "enum_type",
                "type": type(value).__name__,
                "expected": enum_type.__name__,
            },
        )
    return value


def _require_str_tuple(
    value: object, *, field: str, max_item_len: int, max_items: int = 100
) -> tuple[str, ...]:
    items = _require_tuple(value, field=field)
    if len(items) > max_items:
        raise DataContractError(
            f"{field} exceeds max items",
            details={"field": field, "rule": "max_items", "max": max_items},
        )
    out: list[str] = []
    for idx, item in enumerate(items):
        out.append(
            _require_str(
                item,
                field=f"{field}[{idx}]",
                max_len=max_item_len,
            )
        )
    return tuple(out)


def _require_decimal_tuple(
    value: object, *, field: str, allow_empty: bool = True
) -> tuple[Decimal, ...]:
    items = _require_tuple(value, field=field)
    if not allow_empty and len(items) == 0:
        raise DataContractError(
            f"{field} must not be empty",
            details={"field": field, "rule": "non_empty"},
        )
    out: list[Decimal] = []
    for idx, item in enumerate(items):
        out.append(_require_decimal(item, field=f"{field}[{idx}]"))
    return tuple(out)


def _require_int_tuple(value: object, *, field: str) -> tuple[int, ...]:
    items = _require_tuple(value, field=field)
    out: list[int] = []
    for idx, item in enumerate(items):
        out.append(_require_nonnegative_int(item, field=f"{field}[{idx}]"))
    return tuple(out)


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


# ---------------------------------------------------------------------------
# §4.2 Fundamentals
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundamentalMetric:
    name: str
    value: Decimal | str | int | None
    unit: str | None
    period_end: date | None
    published_at: datetime | None

    def __post_init__(self) -> None:
        _require_str(self.name, field="name", max_len=_METRIC_NAME_MAX)
        if self.value is not None:
            if isinstance(self.value, float):
                raise DataContractError(
                    "value must not be float",
                    details={"field": "value", "rule": "no_float"},
                )
            if type(self.value) is Decimal:
                _require_decimal(self.value, field="value")
            elif type(self.value) is int:
                pass
            elif isinstance(self.value, str):
                _require_str(self.value, field="value", max_len=200, allow_blank=True)
            else:
                raise DataContractError(
                    "value must be Decimal, str, int, or None",
                    details={
                        "field": "value",
                        "rule": "value_type",
                        "type": type(self.value).__name__,
                    },
                )
        _require_optional_str(self.unit, field="unit", max_len=_UNIT_MAX)
        _require_optional_date(self.period_end, field="period_end")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")


@dataclass(frozen=True, slots=True)
class FinancialStatementLine:
    statement_type: FinancialStatementType
    period_end: date
    published_at: datetime | None
    item_code: str
    item_name: str
    value: Decimal | None
    unit: str

    def __post_init__(self) -> None:
        _require_enum(self.statement_type, FinancialStatementType, field="statement_type")
        _require_date(self.period_end, field="period_end")
        if self.published_at is not None:
            require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.item_code, field="item_code", max_len=_ITEM_CODE_MAX)
        _require_str(self.item_name, field="item_name", max_len=_ITEM_NAME_MAX)
        _require_optional_decimal(self.value, field="value")
        _require_str(self.unit, field="unit", max_len=_UNIT_MAX)


@dataclass(frozen=True, slots=True)
class F10Section:
    section: str
    title: str
    body: str
    as_of: datetime

    def __post_init__(self) -> None:
        _require_str(self.section, field="section", max_len=_SECTION_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        _require_str(self.body, field="body", max_len=_F10_BODY_MAX, allow_blank=True)
        require_aware_datetime(self.as_of, field_name="as_of")


# ---------------------------------------------------------------------------
# §4.3 Research / disclosure / news
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsensusEstimate:
    fiscal_year: int
    metric: str
    mean: Decimal | None
    high: Decimal | None
    low: Decimal | None
    institution_count: int | None

    def __post_init__(self) -> None:
        year = _require_int(self.fiscal_year, field="fiscal_year")
        if year < 1990 or year > 2100:
            raise DataContractError(
                "fiscal_year out of range",
                details={"field": "fiscal_year", "rule": "year_range"},
            )
        metric = _require_str(self.metric, field="metric", max_len=_METRIC_NAME_MAX)
        if metric not in _CONSENSUS_METRICS:
            raise DataContractError(
                "metric must be eps|revenue|net_income",
                details={"field": "metric", "rule": "consensus_metric"},
            )
        mean = _require_optional_decimal(self.mean, field="mean")
        high = _require_optional_decimal(self.high, field="high")
        low = _require_optional_decimal(self.low, field="low")
        if high is not None and low is not None and high < low:
            raise DataContractError(
                "high must be >= low",
                details={"field": "high", "rule": "range_order"},
            )
        if mean is not None and high is not None and mean > high:
            raise DataContractError(
                "mean must be <= high",
                details={"field": "mean", "rule": "range_order"},
            )
        if mean is not None and low is not None and mean < low:
            raise DataContractError(
                "mean must be >= low",
                details={"field": "mean", "rule": "range_order"},
            )
        _require_optional_nonnegative_int(self.institution_count, field="institution_count")


@dataclass(frozen=True, slots=True)
class AnalystReportItem:
    report_key: str
    title: str
    institution: str | None
    analyst_names: tuple[str, ...]
    published_at: datetime
    rating: str | None
    target_price: Decimal | None
    eps_forecasts: tuple[ConsensusEstimate, ...]
    source_url: str | None
    pdf_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.report_key, field="report_key", max_len=_KEY_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        _require_optional_str(self.institution, field="institution", max_len=_NAME_MAX)
        names = _require_str_tuple(
            self.analyst_names, field="analyst_names", max_item_len=_NAME_MAX
        )
        object.__setattr__(self, "analyst_names", names)
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_optional_str(self.rating, field="rating", max_len=64)
        _require_optional_decimal(self.target_price, field="target_price")
        forecasts = _require_tuple(self.eps_forecasts, field="eps_forecasts")
        for idx, item in enumerate(forecasts):
            if not isinstance(item, ConsensusEstimate):
                raise DataContractError(
                    "eps_forecasts elements must be ConsensusEstimate",
                    details={"field": "eps_forecasts", "index": idx, "rule": "type"},
                )
        _require_optional_str(self.source_url, field="source_url", max_len=_URL_MAX)
        _require_optional_str(self.pdf_url, field="pdf_url", max_len=_URL_MAX)


@dataclass(frozen=True, slots=True)
class AnnouncementItem:
    announcement_key: str
    title: str
    published_at: datetime
    category: str | None
    source_url: str
    pdf_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.announcement_key, field="announcement_key", max_len=_KEY_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_optional_str(self.category, field="category", max_len=100)
        _require_str(self.source_url, field="source_url", max_len=_URL_MAX)
        _require_optional_str(self.pdf_url, field="pdf_url", max_len=_URL_MAX)


@dataclass(frozen=True, slots=True)
class NewsItem:
    news_key: str
    title: str
    summary: str | None
    published_at: datetime
    source_name: str
    source_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.news_key, field="news_key", max_len=_KEY_MAX)
        _require_str(self.title, field="title", max_len=_TITLE_MAX)
        _require_optional_str(self.summary, field="summary", max_len=4_000)
        require_aware_datetime(self.published_at, field_name="published_at")
        _require_str(self.source_name, field="source_name", max_len=_NAME_MAX)
        _require_optional_str(self.source_url, field="source_url", max_len=_URL_MAX)


@dataclass(frozen=True, slots=True)
class InteractiveQAItem:
    qa_key: str
    question: str
    asked_at: datetime | None
    answer: str
    answered_at: datetime
    source_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.qa_key, field="qa_key", max_len=_KEY_MAX)
        _require_str(self.question, field="question", max_len=8_000)
        if self.asked_at is not None:
            require_aware_datetime(self.asked_at, field_name="asked_at")
        _require_str(self.answer, field="answer", max_len=20_000, allow_blank=True)
        require_aware_datetime(self.answered_at, field_name="answered_at")
        if self.asked_at is not None and self.answered_at < self.asked_at:
            raise DataContractError(
                "answered_at must be >= asked_at",
                details={"field": "answered_at", "rule": "range_order"},
            )
        _require_optional_str(self.source_url, field="source_url", max_len=_URL_MAX)


# ---------------------------------------------------------------------------
# §17.1 Market board / industry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndustryPerformanceRow:
    industry_code: str
    industry_name: str
    trade_date: date
    change_percent: Decimal
    advancing_count: int
    declining_count: int
    unchanged_count: int
    leading_instrument_id: str | None
    leading_change_percent: Decimal | None
    turnover_amount_cny: Decimal | None

    def __post_init__(self) -> None:
        _require_str(self.industry_code, field="industry_code", max_len=64)
        _require_str(self.industry_name, field="industry_name", max_len=_INDUSTRY_MAX)
        _require_date(self.trade_date, field="trade_date")
        _require_decimal(self.change_percent, field="change_percent")
        _require_nonnegative_int(self.advancing_count, field="advancing_count")
        _require_nonnegative_int(self.declining_count, field="declining_count")
        _require_nonnegative_int(self.unchanged_count, field="unchanged_count")
        _require_optional_a_share_instrument_id(
            self.leading_instrument_id,
            field="leading_instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        _require_optional_decimal(self.leading_change_percent, field="leading_change_percent")
        _require_optional_decimal(self.turnover_amount_cny, field="turnover_amount_cny")


@dataclass(frozen=True, slots=True)
class MarketBoardSnapshot:
    trade_date: date
    advancing_count: int
    declining_count: int
    unchanged_count: int
    limit_up_count: int
    limit_down_count: int
    broken_limit_count: int
    total_turnover_cny: Decimal | None
    median_change_percent: Decimal | None
    industries: tuple[IndustryPerformanceRow, ...]

    def __post_init__(self) -> None:
        _require_date(self.trade_date, field="trade_date")
        for name in (
            "advancing_count",
            "declining_count",
            "unchanged_count",
            "limit_up_count",
            "limit_down_count",
            "broken_limit_count",
        ):
            _require_nonnegative_int(getattr(self, name), field=name)
        _require_optional_decimal(self.total_turnover_cny, field="total_turnover_cny")
        _require_optional_decimal(self.median_change_percent, field="median_change_percent")
        industries = _require_tuple(self.industries, field="industries")
        seen_codes: set[str] = set()
        for idx, row in enumerate(industries):
            if not isinstance(row, IndustryPerformanceRow):
                raise DataContractError(
                    "industries elements must be IndustryPerformanceRow",
                    details={"field": "industries", "index": idx, "rule": "type"},
                )
            if row.industry_code in seen_codes:
                raise DataContractError(
                    "industry_code must be unique within industries",
                    details={"field": "industries", "rule": "unique_industry_code"},
                )
            seen_codes.add(row.industry_code)


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
                (
                    b.holding_ratio
                    for b in bins
                    if isinstance(b, ChipDistributionBin)
                ),
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


# ---------------------------------------------------------------------------
# Calendar window (port return type)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradingSessionWindow:
    session: TradingSession
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        _require_enum(self.session, TradingSession, field="session")
        require_aware_datetime(self.start_at, field_name="start_at")
        require_aware_datetime(self.end_at, field_name="end_at")
        if self.end_at <= self.start_at:
            raise DataContractError(
                "end_at must be > start_at",
                details={"field": "end_at", "rule": "range_order"},
            )
