"""Frozen US market domain models (Phase 1F F1).

All models are ``@dataclass(frozen=True, slots=True)``. Numerics are
``Decimal`` (no float). Datetimes are timezone-aware. Nested sequences are
immutable tuples. Range, order, uniqueness, and OHLC invariants live in
``__post_init__``. No provider imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from domain.common.enums import AdjustmentMethod, AssetType, Market, TradingSession
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.market.models import MarketBar, TechnicalIndicators
from domain.us_market.enums import USBarInterval

# Equity / ETF / index instruments for US quote, bars, technical, composite.
_QUOTE_ASSET_TYPES = frozenset(
    {AssetType.EQUITY, AssetType.ETF, AssetType.INDEX, AssetType.FUTURE}
)
# Market context proxies are the three US ETF seeds.
_PROXY_ASSET_TYPES = frozenset({AssetType.ETF})
_PROXY_INSTRUMENT_IDS = frozenset(
    {
        "etf:US:SPY",
        "etf:US:QQQ",
        "etf:US:IWM",
    }
)

_TECHNICAL_ALGORITHM_VERSION = "tp_technical_v1"
_SUPPORT_RESISTANCE_METHOD = "rolling_extrema_20_v1"
_BREADTH_WARNING = "US_BREADTH_UNAVAILABLE"
_NEW_YORK = ZoneInfo("America/New_York")


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


def _require_nonnegative_decimal(value: object, *, field: str) -> Decimal:
    number = _require_decimal(value, field=field)
    if number < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field, "rule": "nonnegative"},
        )
    return number


def _require_optional_nonnegative_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _require_nonnegative_decimal(value, field=field)


def _require_int(value: object, *, field: str) -> int:
    _reject_float(value, field=field)
    if type(value) is not int:
        raise DataContractError(
            f"{field} must be an int",
            details={"field": field, "rule": "int_type", "type": type(value).__name__},
        )
    return value


def _require_optional_nonnegative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    number = _require_int(value, field=field)
    if number < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field, "rule": "nonnegative"},
        )
    return number


def _require_nonnegative_int(value: object, *, field: str) -> int:
    number = _require_int(value, field=field)
    if number < 0:
        raise DataContractError(
            f"{field} must be nonnegative",
            details={"field": field, "rule": "nonnegative"},
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


def _require_str(value: object, *, field: str, max_len: int = 128) -> str:
    if not isinstance(value, str):
        raise DataContractError(
            f"{field} must be a string",
            details={"field": field, "rule": "str_type", "type": type(value).__name__},
        )
    if not value or not value.strip():
        raise DataContractError(
            f"{field} must be a non-blank string",
            details={"field": field, "rule": "non_blank"},
        )
    if len(value) > max_len:
        raise DataContractError(
            f"{field} exceeds max length",
            details={"field": field, "rule": "max_length", "max": max_len},
        )
    return value


def _require_tuple(value: object, *, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise DataContractError(
            f"{field} must be a tuple",
            details={"field": field, "rule": "tuple_type", "type": type(value).__name__},
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


def _require_us_instrument_id(
    value: object,
    *,
    field: str,
    allowed_assets: frozenset[AssetType],
) -> str:
    text = _require_str(value, field=field, max_len=128)
    try:
        asset_type, market, _symbol = parse_instrument_id(text)
    except DataContractError as exc:
        raise DataContractError(
            f"{field} must be a well-formed instrument_id",
            details={"field": field, "rule": "instrument_id_syntax"},
        ) from exc
    if market is not Market.US:
        raise DataContractError(
            f"{field} must use Market.US",
            details={
                "field": field,
                "rule": "us_market",
                "market": market.value,
            },
        )
    if asset_type not in allowed_assets:
        raise DataContractError(
            f"{field} asset type not allowed for this model",
            details={
                "field": field,
                "rule": "us_asset_type",
                "asset_type": asset_type.value,
                "allowed": sorted(a.value for a in allowed_assets),
            },
        )
    return text


def _validate_ohlc(
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    *,
    prefix: str,
) -> None:
    if high < max(open_, close, low):
        raise DataContractError(
            f"{prefix}high must be >= max(open, close, low)",
            details={"field": f"{prefix}high", "rule": "ohlc_high"},
        )
    if low > min(open_, close, high):
        raise DataContractError(
            f"{prefix}low must be <= min(open, close, high)",
            details={"field": f"{prefix}low", "rule": "ohlc_low"},
        )


def _validate_market_bar(bar: MarketBar, *, index: int) -> None:
    if not isinstance(bar, MarketBar):
        raise DataContractError(
            "bars items must be MarketBar",
            details={"field": f"bars[{index}]", "rule": "type"},
        )
    require_aware_datetime(bar.timestamp, field_name=f"bars[{index}].timestamp")
    open_ = _require_decimal(bar.open, field=f"bars[{index}].open")
    high = _require_decimal(bar.high, field=f"bars[{index}].high")
    low = _require_decimal(bar.low, field=f"bars[{index}].low")
    close = _require_decimal(bar.close, field=f"bars[{index}].close")
    _require_nonnegative_decimal(bar.volume, field=f"bars[{index}].volume")
    _validate_ohlc(open_, high, low, close, prefix=f"bars[{index}].")


# ---------------------------------------------------------------------------
# Quote / bars / context / technical / composite
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class USQuote:
    instrument_id: str
    quote_at: datetime
    session: TradingSession
    last: Decimal
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    previous_close: Decimal | None
    volume: Decimal | None
    average_volume: Decimal | None
    market_cap: Decimal | None
    beta: Decimal | None
    week_52_low: Decimal | None
    week_52_high: Decimal | None

    def __post_init__(self) -> None:
        _require_us_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        require_aware_datetime(self.quote_at, field_name="quote_at")
        _require_enum(self.session, TradingSession, field="session")
        _require_nonnegative_decimal(self.last, field="last")
        open_ = _require_optional_nonnegative_decimal(self.open, field="open")
        high = _require_optional_nonnegative_decimal(self.high, field="high")
        low = _require_optional_nonnegative_decimal(self.low, field="low")
        _require_optional_nonnegative_decimal(self.previous_close, field="previous_close")
        _require_optional_nonnegative_decimal(self.volume, field="volume")
        _require_optional_nonnegative_decimal(self.average_volume, field="average_volume")
        _require_optional_nonnegative_decimal(self.market_cap, field="market_cap")
        _require_optional_decimal(self.beta, field="beta")
        week_low = _require_optional_nonnegative_decimal(self.week_52_low, field="week_52_low")
        week_high = _require_optional_nonnegative_decimal(self.week_52_high, field="week_52_high")
        if high is not None and low is not None and high < low:
            raise DataContractError(
                "high must be >= low",
                details={"field": "high", "rule": "ohlc_high_ge_low"},
            )
        if open_ is not None and high is not None and low is not None:
            # When open present with high/low, also bound last when available.
            close = self.last
            if high < max(open_, close, low):
                raise DataContractError(
                    "high must be >= max(open, last, low)",
                    details={"field": "high", "rule": "ohlc_high"},
                )
            if low > min(open_, close, high):
                raise DataContractError(
                    "low must be <= min(open, last, high)",
                    details={"field": "low", "rule": "ohlc_low"},
                )
        if week_low is not None and week_high is not None and week_low > week_high:
            raise DataContractError(
                "week_52_low must be <= week_52_high",
                details={"field": "week_52_low", "rule": "range_order"},
            )


@dataclass(frozen=True, slots=True)
class USBarSeries:
    instrument_id: str
    interval: USBarInterval
    adjustment: AdjustmentMethod
    start: date
    end: date
    bars: tuple[MarketBar, ...]

    def __post_init__(self) -> None:
        _require_us_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        _require_enum(self.interval, USBarInterval, field="interval")
        _require_enum(self.adjustment, AdjustmentMethod, field="adjustment")
        start = _require_date(self.start, field="start")
        end = _require_date(self.end, field="end")
        if end < start:
            raise DataContractError(
                "end must be >= start",
                details={"field": "end", "rule": "range_order"},
            )
        bars = _require_tuple(self.bars, field="bars")
        prev_ts: datetime | None = None
        seen: set[datetime] = set()
        for idx, bar in enumerate(bars):
            _validate_market_bar(bar, index=idx)  # type: ignore[arg-type]
            assert isinstance(bar, MarketBar)
            ts = bar.timestamp
            local_day = ts.astimezone(_NEW_YORK).date()
            if local_day < start or local_day > end:
                raise DataContractError(
                    "bar timestamp must fall inside inclusive start/end",
                    details={"field": f"bars[{idx}].timestamp", "rule": "inclusive_range"},
                )
            if ts in seen:
                raise DataContractError(
                    "bars timestamps must be unique",
                    details={"field": f"bars[{idx}].timestamp", "rule": "unique"},
                )
            if prev_ts is not None and ts <= prev_ts:
                raise DataContractError(
                    "bars timestamps must be strictly ascending",
                    details={"field": f"bars[{idx}].timestamp", "rule": "strict_order"},
                )
            seen.add(ts)
            prev_ts = ts


@dataclass(frozen=True, slots=True)
class USMarketProxy:
    instrument_id: str
    latest: Decimal | None
    change_percent: Decimal | None

    def __post_init__(self) -> None:
        instrument_id = _require_us_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_PROXY_ASSET_TYPES,
        )
        if instrument_id not in _PROXY_INSTRUMENT_IDS:
            raise DataContractError(
                "instrument_id must be a frozen US market proxy (SPY/QQQ/IWM)",
                details={"field": "instrument_id", "rule": "proxy_instrument"},
            )
        _require_optional_nonnegative_decimal(self.latest, field="latest")
        _require_optional_decimal(self.change_percent, field="change_percent")


@dataclass(frozen=True, slots=True)
class USSectorRotation:
    """One Yahoo US sector-index return row; values are descriptive, not forecasts."""

    sector: str
    index_symbol: str
    return_1d: Decimal | None
    return_5d: Decimal | None
    return_20d: Decimal | None
    relative_spy_20d: Decimal | None

    def __post_init__(self) -> None:
        _require_str(self.sector, field="sector", max_len=64)
        _require_str(self.index_symbol, field="index_symbol", max_len=32)
        _require_optional_decimal(self.return_1d, field="return_1d")
        _require_optional_decimal(self.return_5d, field="return_5d")
        _require_optional_decimal(self.return_20d, field="return_20d")
        _require_optional_decimal(self.relative_spy_20d, field="relative_spy_20d")


@dataclass(frozen=True, slots=True)
class USBreadthSnapshot:
    """Current Yahoo screener breadth plus sector-index rotation facts."""

    observed_at: datetime
    advancing_count: int
    declining_count: int
    unchanged_count: int
    basis: str
    universe: str
    sector_rotation: tuple[USSectorRotation, ...]

    def __post_init__(self) -> None:
        require_aware_datetime(self.observed_at, field_name="observed_at")
        _require_nonnegative_int(self.advancing_count, field="advancing_count")
        _require_nonnegative_int(self.declining_count, field="declining_count")
        _require_nonnegative_int(self.unchanged_count, field="unchanged_count")
        _require_str(self.basis, field="basis", max_len=64)
        _require_str(self.universe, field="universe", max_len=256)
        rows = _require_tuple(self.sector_rotation, field="sector_rotation")
        sectors: set[str] = set()
        for idx, row in enumerate(rows):
            if not isinstance(row, USSectorRotation):
                raise DataContractError(
                    "sector_rotation items must be USSectorRotation",
                    details={"field": f"sector_rotation[{idx}]", "rule": "type"},
                )
            if row.sector in sectors:
                raise DataContractError(
                    "sector_rotation sectors must be unique",
                    details={"field": "sector_rotation", "rule": "unique"},
                )
            sectors.add(row.sector)


@dataclass(frozen=True, slots=True)
class USCommunityHeatItem:
    """One Moomoo OpenD community-attention ranking row."""

    provider_code: str
    name: str
    rank: int
    trade_heat: Decimal | None
    trade_heat_change: Decimal | None
    search_heat: Decimal | None
    search_heat_change: Decimal | None
    news_heat: Decimal | None
    news_heat_change: Decimal | None
    average_heat: Decimal | None
    average_heat_change: Decimal | None
    related_content_type: str | None
    related_title: str | None
    related_url: str | None

    def __post_init__(self) -> None:
        _require_str(self.provider_code, field="provider_code", max_len=64)
        _require_str(self.name, field="name", max_len=256)
        if type(self.rank) is not int or self.rank <= 0:
            raise DataContractError(
                "rank must be a positive integer",
                details={"field": "rank", "rule": "positive_integer"},
            )
        for field_name in (
            "trade_heat",
            "trade_heat_change",
            "search_heat",
            "search_heat_change",
            "news_heat",
            "news_heat_change",
            "average_heat",
            "average_heat_change",
        ):
            _require_optional_decimal(getattr(self, field_name), field=field_name)
        if self.related_content_type is not None:
            _require_str(
                self.related_content_type,
                field="related_content_type",
                max_len=32,
            )
        if self.related_title is not None:
            _require_str(self.related_title, field="related_title", max_len=500)
        if self.related_url is not None:
            _require_str(self.related_url, field="related_url", max_len=2_000)


@dataclass(frozen=True, slots=True)
class USCommunityHeatSnapshot:
    observed_at: datetime
    basis: str
    items: tuple[USCommunityHeatItem, ...]

    def __post_init__(self) -> None:
        require_aware_datetime(self.observed_at, field_name="observed_at")
        _require_str(self.basis, field="basis", max_len=128)
        rows = _require_tuple(self.items, field="items")
        codes: set[str] = set()
        for idx, row in enumerate(rows):
            if not isinstance(row, USCommunityHeatItem):
                raise DataContractError(
                    "items must contain USCommunityHeatItem",
                    details={"field": f"items[{idx}]", "rule": "type"},
                )
            if row.provider_code in codes:
                raise DataContractError(
                    "community heat provider codes must be unique",
                    details={"field": "items", "rule": "unique"},
                )
            codes.add(row.provider_code)


@dataclass(frozen=True, slots=True)
class USMarketContext:
    as_of: datetime
    spy: USMarketProxy
    qqq: USMarketProxy
    iwm: USMarketProxy
    advancing_count: int | None
    declining_count: int | None
    warning_codes: tuple[str, ...]
    unchanged_count: int | None = None
    breadth_as_of: datetime | None = None
    breadth_basis: str | None = None
    breadth_universe: str | None = None
    sector_rotation: tuple[USSectorRotation, ...] = ()
    community_heat_as_of: datetime | None = None
    community_heat_basis: str | None = None
    community_heat: tuple[USCommunityHeatItem, ...] = ()

    def __post_init__(self) -> None:
        require_aware_datetime(self.as_of, field_name="as_of")
        if not isinstance(self.spy, USMarketProxy):
            raise DataContractError(
                "spy must be a USMarketProxy",
                details={"field": "spy", "rule": "type"},
            )
        if not isinstance(self.qqq, USMarketProxy):
            raise DataContractError(
                "qqq must be a USMarketProxy",
                details={"field": "qqq", "rule": "type"},
            )
        if not isinstance(self.iwm, USMarketProxy):
            raise DataContractError(
                "iwm must be a USMarketProxy",
                details={"field": "iwm", "rule": "type"},
            )
        if self.spy.instrument_id != "etf:US:SPY":
            raise DataContractError(
                "spy.instrument_id must be etf:US:SPY",
                details={"field": "spy.instrument_id", "rule": "proxy_slot"},
            )
        if self.qqq.instrument_id != "etf:US:QQQ":
            raise DataContractError(
                "qqq.instrument_id must be etf:US:QQQ",
                details={"field": "qqq.instrument_id", "rule": "proxy_slot"},
            )
        if self.iwm.instrument_id != "etf:US:IWM":
            raise DataContractError(
                "iwm.instrument_id must be etf:US:IWM",
                details={"field": "iwm.instrument_id", "rule": "proxy_slot"},
            )
        _require_optional_nonnegative_int(self.advancing_count, field="advancing_count")
        _require_optional_nonnegative_int(self.declining_count, field="declining_count")
        _require_optional_nonnegative_int(self.unchanged_count, field="unchanged_count")
        if self.breadth_as_of is not None:
            require_aware_datetime(self.breadth_as_of, field_name="breadth_as_of")
        if self.breadth_basis is not None:
            _require_str(self.breadth_basis, field="breadth_basis", max_len=64)
        if self.breadth_universe is not None:
            _require_str(self.breadth_universe, field="breadth_universe", max_len=256)
        for idx, row in enumerate(_require_tuple(self.sector_rotation, field="sector_rotation")):
            if not isinstance(row, USSectorRotation):
                raise DataContractError(
                    "sector_rotation items must be USSectorRotation",
                    details={"field": f"sector_rotation[{idx}]", "rule": "type"},
                )
        if self.community_heat_as_of is not None:
            require_aware_datetime(
                self.community_heat_as_of,
                field_name="community_heat_as_of",
            )
        if self.community_heat_basis is not None:
            _require_str(
                self.community_heat_basis,
                field="community_heat_basis",
                max_len=128,
            )
        for idx, row in enumerate(_require_tuple(self.community_heat, field="community_heat")):
            if not isinstance(row, USCommunityHeatItem):
                raise DataContractError(
                    "community_heat items must be USCommunityHeatItem",
                    details={"field": f"community_heat[{idx}]", "rule": "type"},
                )
        codes = _require_tuple(self.warning_codes, field="warning_codes")
        for idx, code in enumerate(codes):
            if not isinstance(code, str) or not code.strip():
                raise DataContractError(
                    "warning_codes items must be non-blank strings",
                    details={"field": f"warning_codes[{idx}]", "rule": "non_blank"},
                )
        if len(set(codes)) != len(codes):
            raise DataContractError(
                "warning_codes must be unique",
                details={"field": "warning_codes", "rule": "unique"},
            )
        # Breadth is not fabricated: when both counts are unset, require the
        # frozen unavailability warning (design §3).
        if (
            self.advancing_count is None
            and self.declining_count is None
            and _BREADTH_WARNING not in codes
        ):
            raise DataContractError(
                "warning_codes must include US_BREADTH_UNAVAILABLE when breadth is unset",
                details={"field": "warning_codes", "rule": "breadth_unavailable"},
            )


@dataclass(frozen=True, slots=True)
class USTechnicalSnapshot:
    instrument_id: str
    as_of: datetime
    bar_as_of: datetime
    indicators: TechnicalIndicators
    support: Decimal | None
    resistance: Decimal | None
    algorithm_version: str
    historically_validated: bool
    support_resistance_method: str

    def __post_init__(self) -> None:
        _require_us_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        require_aware_datetime(self.as_of, field_name="as_of")
        require_aware_datetime(self.bar_as_of, field_name="bar_as_of")
        if self.bar_as_of > self.as_of:
            raise DataContractError(
                "bar_as_of must be <= as_of",
                details={"field": "bar_as_of", "rule": "not_after_as_of"},
            )
        if not isinstance(self.indicators, TechnicalIndicators):
            raise DataContractError(
                "indicators must be TechnicalIndicators",
                details={"field": "indicators", "rule": "type"},
            )
        support = _require_optional_nonnegative_decimal(self.support, field="support")
        resistance = _require_optional_nonnegative_decimal(self.resistance, field="resistance")
        if support is not None and resistance is not None and support > resistance:
            raise DataContractError(
                "support must be <= resistance",
                details={"field": "support", "rule": "range_order"},
            )
        version = _require_str(self.algorithm_version, field="algorithm_version", max_len=64)
        if version != _TECHNICAL_ALGORITHM_VERSION:
            raise DataContractError(
                "algorithm_version must be tp_technical_v1",
                details={"field": "algorithm_version", "rule": "frozen_version"},
            )
        validated = _require_bool(self.historically_validated, field="historically_validated")
        if validated is not False:
            raise DataContractError(
                "historically_validated must be false",
                details={"field": "historically_validated", "rule": "not_backtested"},
            )
        method = _require_str(
            self.support_resistance_method,
            field="support_resistance_method",
            max_len=64,
        )
        if method != _SUPPORT_RESISTANCE_METHOD:
            raise DataContractError(
                "support_resistance_method must be rolling_extrema_20_v1",
                details={"field": "support_resistance_method", "rule": "frozen_method"},
            )


@dataclass(frozen=True, slots=True)
class USCompositeSnapshot:
    instrument_id: str
    as_of: datetime
    quote: USQuote | None
    bars: USBarSeries | None
    technical: USTechnicalSnapshot | None
    context: USMarketContext | None
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        instrument_id = _require_us_instrument_id(
            self.instrument_id,
            field="instrument_id",
            allowed_assets=_QUOTE_ASSET_TYPES,
        )
        require_aware_datetime(self.as_of, field_name="as_of")
        _require_bool(self.degraded, field="degraded")
        codes = _require_tuple(self.warning_codes, field="warning_codes")
        for idx, code in enumerate(codes):
            if not isinstance(code, str) or not code.strip():
                raise DataContractError(
                    "warning_codes items must be non-blank strings",
                    details={"field": f"warning_codes[{idx}]", "rule": "non_blank"},
                )
        if len(set(codes)) != len(codes):
            raise DataContractError(
                "warning_codes must be unique",
                details={"field": "warning_codes", "rule": "unique"},
            )
        if self.quote is not None:
            if not isinstance(self.quote, USQuote):
                raise DataContractError(
                    "quote must be a USQuote",
                    details={"field": "quote", "rule": "type"},
                )
            if self.quote.instrument_id != instrument_id:
                raise DataContractError(
                    "quote.instrument_id must match composite instrument_id",
                    details={"field": "quote.instrument_id", "rule": "instrument_match"},
                )
        if self.bars is not None:
            if not isinstance(self.bars, USBarSeries):
                raise DataContractError(
                    "bars must be a USBarSeries",
                    details={"field": "bars", "rule": "type"},
                )
            if self.bars.instrument_id != instrument_id:
                raise DataContractError(
                    "bars.instrument_id must match composite instrument_id",
                    details={"field": "bars.instrument_id", "rule": "instrument_match"},
                )
        if self.technical is not None:
            if not isinstance(self.technical, USTechnicalSnapshot):
                raise DataContractError(
                    "technical must be a USTechnicalSnapshot",
                    details={"field": "technical", "rule": "type"},
                )
            if self.technical.instrument_id != instrument_id:
                raise DataContractError(
                    "technical.instrument_id must match composite instrument_id",
                    details={
                        "field": "technical.instrument_id",
                        "rule": "instrument_match",
                    },
                )
        if self.context is not None and not isinstance(self.context, USMarketContext):
            raise DataContractError(
                "context must be a USMarketContext",
                details={"field": "context", "rule": "type"},
            )
        # The composite endpoint requires both core market components. Partial
        # quote/bars remain available through their dedicated tools.
        if self.quote is None or self.bars is None:
            raise DataContractError(
                "composite requires both quote and bars as core market data",
                details={"field": "quote", "rule": "core_required"},
            )
