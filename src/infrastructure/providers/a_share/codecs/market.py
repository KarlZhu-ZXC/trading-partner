"""Market-data cache codecs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from domain.a_share.models import (
    AShareBar,
    AShareQuote,
    IndustryPerformanceRow,
    MarketBoardSnapshot,
    OrderBookLevel,
    TradeTick,
    validate_order_book_levels,
)
from domain.common.enums import (
    DataCategory,
)
from domain.common.errors import DataContractError
from infrastructure.providers.a_share.codecs.base import (
    _ADJUSTMENT_BY_VALUE,
    _BAR_INTERVAL_BY_VALUE,
    _SESSION_BY_VALUE,
    _TICK_DIR_BY_VALUE,
    AShareProviderCacheCodec,
    _contract_error,
    _decode_date,
    _decode_datetime,
    _decode_decimal,
    _decode_enum,
    _decode_int,
    _decode_optional_decimal,
    _decode_optional_int,
    _decode_optional_str,
    _decode_str,
    _encode_date,
    _encode_datetime,
    _encode_decimal,
    _encode_enum,
    _encode_int,
    _encode_optional_decimal,
    _encode_optional_int,
    _encode_optional_str,
    _encode_str,
    _require_mapping,
)

# E2 codec ids (design §18.3)
CODEC_QUOTE: Final[str] = "a_share_quote.v1"
CODEC_BARS: Final[str] = "a_share_bars.v1"
CODEC_ORDER_BOOK: Final[str] = "a_share_order_book.v1"
CODEC_TICKS: Final[str] = "a_share_ticks.v1"
CODEC_INDUSTRY_PERFORMANCE: Final[str] = "a_share_industry_performance.v1"
CODEC_MARKET_BOARD: Final[str] = "a_share_market_board.v1"

E2_CODEC_IDS: Final[frozenset[str]] = frozenset(
    {
        CODEC_QUOTE,
        CODEC_BARS,
        CODEC_ORDER_BOOK,
        CODEC_TICKS,
        CODEC_INDUSTRY_PERFORMANCE,
        CODEC_MARKET_BOARD,
    }
)

# --- Value encoders / decoders ------------------------------------------------


_QUOTE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "instrument_id",
        "quote_at",
        "session",
        "last",
        "open",
        "high",
        "low",
        "previous_close",
        "change",
        "change_percent",
        "volume_shares",
        "turnover_amount_cny",
        "turnover_rate",
        "pe_ttm",
        "pb",
        "total_market_cap_cny",
        "float_market_cap_cny",
        "limit_up_price",
        "limit_down_price",
    }
)


def _encode_quote(value: AShareQuote) -> dict[str, object]:
    if not isinstance(value, AShareQuote):
        raise _contract_error(
            "value must be AShareQuote",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    return {
        "instrument_id": _encode_str(value.instrument_id, field="value.instrument_id"),
        "quote_at": _encode_datetime(value.quote_at, field="value.quote_at"),
        "session": _encode_enum(value.session, field="value.session", table=_SESSION_BY_VALUE),
        "last": _encode_decimal(value.last, field="value.last"),
        "open": _encode_optional_decimal(value.open, field="value.open"),
        "high": _encode_optional_decimal(value.high, field="value.high"),
        "low": _encode_optional_decimal(value.low, field="value.low"),
        "previous_close": _encode_optional_decimal(
            value.previous_close, field="value.previous_close"
        ),
        "change": _encode_optional_decimal(value.change, field="value.change"),
        "change_percent": _encode_optional_decimal(
            value.change_percent, field="value.change_percent"
        ),
        "volume_shares": _encode_optional_int(value.volume_shares, field="value.volume_shares"),
        "turnover_amount_cny": _encode_optional_decimal(
            value.turnover_amount_cny, field="value.turnover_amount_cny"
        ),
        "turnover_rate": _encode_optional_decimal(value.turnover_rate, field="value.turnover_rate"),
        "pe_ttm": _encode_optional_decimal(value.pe_ttm, field="value.pe_ttm"),
        "pb": _encode_optional_decimal(value.pb, field="value.pb"),
        "total_market_cap_cny": _encode_optional_decimal(
            value.total_market_cap_cny, field="value.total_market_cap_cny"
        ),
        "float_market_cap_cny": _encode_optional_decimal(
            value.float_market_cap_cny, field="value.float_market_cap_cny"
        ),
        "limit_up_price": _encode_optional_decimal(
            value.limit_up_price, field="value.limit_up_price"
        ),
        "limit_down_price": _encode_optional_decimal(
            value.limit_down_price, field="value.limit_down_price"
        ),
    }


def _decode_quote(raw: object) -> AShareQuote:
    obj = _require_mapping(raw, field="value", required_keys=_QUOTE_KEYS)
    try:
        return AShareQuote(
            instrument_id=_decode_str(obj["instrument_id"], field="value.instrument_id"),
            quote_at=_decode_datetime(obj["quote_at"], field="value.quote_at"),
            session=_decode_enum(obj["session"], field="value.session", table=_SESSION_BY_VALUE),
            last=_decode_decimal(obj["last"], field="value.last"),
            open=_decode_optional_decimal(obj["open"], field="value.open"),
            high=_decode_optional_decimal(obj["high"], field="value.high"),
            low=_decode_optional_decimal(obj["low"], field="value.low"),
            previous_close=_decode_optional_decimal(
                obj["previous_close"], field="value.previous_close"
            ),
            change=_decode_optional_decimal(obj["change"], field="value.change"),
            change_percent=_decode_optional_decimal(
                obj["change_percent"], field="value.change_percent"
            ),
            volume_shares=_decode_optional_int(obj["volume_shares"], field="value.volume_shares"),
            turnover_amount_cny=_decode_optional_decimal(
                obj["turnover_amount_cny"], field="value.turnover_amount_cny"
            ),
            turnover_rate=_decode_optional_decimal(
                obj["turnover_rate"], field="value.turnover_rate"
            ),
            pe_ttm=_decode_optional_decimal(obj["pe_ttm"], field="value.pe_ttm"),
            pb=_decode_optional_decimal(obj["pb"], field="value.pb"),
            total_market_cap_cny=_decode_optional_decimal(
                obj["total_market_cap_cny"], field="value.total_market_cap_cny"
            ),
            float_market_cap_cny=_decode_optional_decimal(
                obj["float_market_cap_cny"], field="value.float_market_cap_cny"
            ),
            limit_up_price=_decode_optional_decimal(
                obj["limit_up_price"], field="value.limit_up_price"
            ),
            limit_down_price=_decode_optional_decimal(
                obj["limit_down_price"], field="value.limit_down_price"
            ),
        )
    except DataContractError:
        raise
    except Exception:
        raise _contract_error(
            "value failed contract construction", field="value", rule="construct"
        ) from None


_BAR_KEYS: Final[frozenset[str]] = frozenset(
    {
        "start_at",
        "end_at",
        "interval",
        "open",
        "high",
        "low",
        "close",
        "volume_shares",
        "turnover_amount_cny",
        "adjustment",
    }
)


def _encode_bar(bar: AShareBar, *, prefix: str) -> dict[str, object]:
    return {
        "start_at": _encode_datetime(bar.start_at, field=f"{prefix}.start_at"),
        "end_at": _encode_datetime(bar.end_at, field=f"{prefix}.end_at"),
        "interval": _encode_enum(
            bar.interval, field=f"{prefix}.interval", table=_BAR_INTERVAL_BY_VALUE
        ),
        "open": _encode_decimal(bar.open, field=f"{prefix}.open"),
        "high": _encode_decimal(bar.high, field=f"{prefix}.high"),
        "low": _encode_decimal(bar.low, field=f"{prefix}.low"),
        "close": _encode_decimal(bar.close, field=f"{prefix}.close"),
        "volume_shares": _encode_int(bar.volume_shares, field=f"{prefix}.volume_shares"),
        "turnover_amount_cny": _encode_optional_decimal(
            bar.turnover_amount_cny, field=f"{prefix}.turnover_amount_cny"
        ),
        "adjustment": _encode_enum(
            bar.adjustment, field=f"{prefix}.adjustment", table=_ADJUSTMENT_BY_VALUE
        ),
    }


def _decode_bar(raw: object, *, prefix: str) -> AShareBar:
    obj = _require_mapping(raw, field=prefix, required_keys=_BAR_KEYS)
    try:
        return AShareBar(
            start_at=_decode_datetime(obj["start_at"], field=f"{prefix}.start_at"),
            end_at=_decode_datetime(obj["end_at"], field=f"{prefix}.end_at"),
            interval=_decode_enum(
                obj["interval"], field=f"{prefix}.interval", table=_BAR_INTERVAL_BY_VALUE
            ),
            open=_decode_decimal(obj["open"], field=f"{prefix}.open"),
            high=_decode_decimal(obj["high"], field=f"{prefix}.high"),
            low=_decode_decimal(obj["low"], field=f"{prefix}.low"),
            close=_decode_decimal(obj["close"], field=f"{prefix}.close"),
            volume_shares=_decode_int(obj["volume_shares"], field=f"{prefix}.volume_shares"),
            turnover_amount_cny=_decode_optional_decimal(
                obj["turnover_amount_cny"], field=f"{prefix}.turnover_amount_cny"
            ),
            adjustment=_decode_enum(
                obj["adjustment"],
                field=f"{prefix}.adjustment",
                table=_ADJUSTMENT_BY_VALUE,
            ),
        )
    except DataContractError:
        raise
    except Exception:
        raise _contract_error(
            f"{prefix} failed contract construction", field=prefix, rule="construct"
        ) from None


def _encode_bars(value: tuple[AShareBar, ...]) -> list[dict[str, object]]:
    if not isinstance(value, tuple):
        raise _contract_error(
            "value must be a tuple of AShareBar",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    out: list[dict[str, object]] = []
    for idx, bar in enumerate(value):
        if not isinstance(bar, AShareBar):
            raise _contract_error(
                "value elements must be AShareBar",
                field="value",
                rule="element_type",
                index=idx,
            )
        out.append(_encode_bar(bar, prefix=f"value[{idx}]"))
    return out


def _decode_bars(raw: object) -> tuple[AShareBar, ...]:
    if not isinstance(raw, list):
        raise _contract_error(
            "value must be an array",
            field="value",
            rule="type",
            type=type(raw).__name__,
        )
    return tuple(_decode_bar(item, prefix=f"value[{idx}]") for idx, item in enumerate(raw))


_BOOK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "level",
        "bid_price",
        "bid_volume_shares",
        "ask_price",
        "ask_volume_shares",
    }
)


def _encode_book_level(level: OrderBookLevel, *, prefix: str) -> dict[str, object]:
    return {
        "level": _encode_int(level.level, field=f"{prefix}.level"),
        "bid_price": _encode_optional_decimal(level.bid_price, field=f"{prefix}.bid_price"),
        "bid_volume_shares": _encode_optional_int(
            level.bid_volume_shares, field=f"{prefix}.bid_volume_shares"
        ),
        "ask_price": _encode_optional_decimal(level.ask_price, field=f"{prefix}.ask_price"),
        "ask_volume_shares": _encode_optional_int(
            level.ask_volume_shares, field=f"{prefix}.ask_volume_shares"
        ),
    }


def _decode_book_level(raw: object, *, prefix: str) -> OrderBookLevel:
    obj = _require_mapping(raw, field=prefix, required_keys=_BOOK_KEYS)
    try:
        return OrderBookLevel(
            level=_decode_int(obj["level"], field=f"{prefix}.level"),
            bid_price=_decode_optional_decimal(obj["bid_price"], field=f"{prefix}.bid_price"),
            bid_volume_shares=_decode_optional_int(
                obj["bid_volume_shares"], field=f"{prefix}.bid_volume_shares"
            ),
            ask_price=_decode_optional_decimal(obj["ask_price"], field=f"{prefix}.ask_price"),
            ask_volume_shares=_decode_optional_int(
                obj["ask_volume_shares"], field=f"{prefix}.ask_volume_shares"
            ),
        )
    except DataContractError:
        raise
    except Exception:
        raise _contract_error(
            f"{prefix} failed contract construction", field=prefix, rule="construct"
        ) from None


def _encode_order_book(value: tuple[OrderBookLevel, ...]) -> list[dict[str, object]]:
    if not isinstance(value, tuple):
        raise _contract_error(
            "value must be a tuple of OrderBookLevel",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    validate_order_book_levels(value)
    out: list[dict[str, object]] = []
    for idx, level in enumerate(value):
        out.append(_encode_book_level(level, prefix=f"value[{idx}]"))
    return out


def _decode_order_book(raw: object) -> tuple[OrderBookLevel, ...]:
    if not isinstance(raw, list):
        raise _contract_error(
            "value must be an array",
            field="value",
            rule="type",
            type=type(raw).__name__,
        )
    levels = tuple(_decode_book_level(item, prefix=f"value[{idx}]") for idx, item in enumerate(raw))
    validate_order_book_levels(levels)
    return levels


_TICK_KEYS: Final[frozenset[str]] = frozenset(
    {"occurred_at", "price", "volume_shares", "direction"}
)


def _encode_tick(tick: TradeTick, *, prefix: str) -> dict[str, object]:
    return {
        "occurred_at": _encode_datetime(tick.occurred_at, field=f"{prefix}.occurred_at"),
        "price": _encode_decimal(tick.price, field=f"{prefix}.price"),
        "volume_shares": _encode_int(tick.volume_shares, field=f"{prefix}.volume_shares"),
        "direction": _encode_enum(
            tick.direction, field=f"{prefix}.direction", table=_TICK_DIR_BY_VALUE
        ),
    }


def _decode_tick(raw: object, *, prefix: str) -> TradeTick:
    obj = _require_mapping(raw, field=prefix, required_keys=_TICK_KEYS)
    try:
        return TradeTick(
            occurred_at=_decode_datetime(obj["occurred_at"], field=f"{prefix}.occurred_at"),
            price=_decode_decimal(obj["price"], field=f"{prefix}.price"),
            volume_shares=_decode_int(obj["volume_shares"], field=f"{prefix}.volume_shares"),
            direction=_decode_enum(
                obj["direction"], field=f"{prefix}.direction", table=_TICK_DIR_BY_VALUE
            ),
        )
    except DataContractError:
        raise
    except Exception:
        raise _contract_error(
            f"{prefix} failed contract construction", field=prefix, rule="construct"
        ) from None


def _encode_ticks(value: tuple[TradeTick, ...]) -> list[dict[str, object]]:
    if not isinstance(value, tuple):
        raise _contract_error(
            "value must be a tuple of TradeTick",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    out: list[dict[str, object]] = []
    for idx, tick in enumerate(value):
        if not isinstance(tick, TradeTick):
            raise _contract_error(
                "value elements must be TradeTick",
                field="value",
                rule="element_type",
                index=idx,
            )
        out.append(_encode_tick(tick, prefix=f"value[{idx}]"))
    return out


def _decode_ticks(raw: object) -> tuple[TradeTick, ...]:
    if not isinstance(raw, list):
        raise _contract_error(
            "value must be an array",
            field="value",
            rule="type",
            type=type(raw).__name__,
        )
    return tuple(_decode_tick(item, prefix=f"value[{idx}]") for idx, item in enumerate(raw))


_INDUSTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "industry_code",
        "industry_name",
        "trade_date",
        "change_percent",
        "advancing_count",
        "declining_count",
        "unchanged_count",
        "leading_instrument_id",
        "leading_change_percent",
        "turnover_amount_cny",
    }
)


def _encode_industry_row(row: IndustryPerformanceRow, *, prefix: str) -> dict[str, object]:
    return {
        "industry_code": _encode_str(row.industry_code, field=f"{prefix}.industry_code"),
        "industry_name": _encode_str(row.industry_name, field=f"{prefix}.industry_name"),
        "trade_date": _encode_date(row.trade_date, field=f"{prefix}.trade_date"),
        "change_percent": _encode_decimal(row.change_percent, field=f"{prefix}.change_percent"),
        "advancing_count": _encode_int(row.advancing_count, field=f"{prefix}.advancing_count"),
        "declining_count": _encode_int(row.declining_count, field=f"{prefix}.declining_count"),
        "unchanged_count": _encode_int(row.unchanged_count, field=f"{prefix}.unchanged_count"),
        "leading_instrument_id": _encode_optional_str(
            row.leading_instrument_id, field=f"{prefix}.leading_instrument_id"
        ),
        "leading_change_percent": _encode_optional_decimal(
            row.leading_change_percent, field=f"{prefix}.leading_change_percent"
        ),
        "turnover_amount_cny": _encode_optional_decimal(
            row.turnover_amount_cny, field=f"{prefix}.turnover_amount_cny"
        ),
    }


def _decode_industry_row(raw: object, *, prefix: str) -> IndustryPerformanceRow:
    obj = _require_mapping(raw, field=prefix, required_keys=_INDUSTRY_KEYS)
    try:
        return IndustryPerformanceRow(
            industry_code=_decode_str(obj["industry_code"], field=f"{prefix}.industry_code"),
            industry_name=_decode_str(obj["industry_name"], field=f"{prefix}.industry_name"),
            trade_date=_decode_date(obj["trade_date"], field=f"{prefix}.trade_date"),
            change_percent=_decode_decimal(obj["change_percent"], field=f"{prefix}.change_percent"),
            advancing_count=_decode_int(obj["advancing_count"], field=f"{prefix}.advancing_count"),
            declining_count=_decode_int(obj["declining_count"], field=f"{prefix}.declining_count"),
            unchanged_count=_decode_int(obj["unchanged_count"], field=f"{prefix}.unchanged_count"),
            leading_instrument_id=_decode_optional_str(
                obj["leading_instrument_id"],
                field=f"{prefix}.leading_instrument_id",
            ),
            leading_change_percent=_decode_optional_decimal(
                obj["leading_change_percent"],
                field=f"{prefix}.leading_change_percent",
            ),
            turnover_amount_cny=_decode_optional_decimal(
                obj["turnover_amount_cny"], field=f"{prefix}.turnover_amount_cny"
            ),
        )
    except DataContractError:
        raise
    except Exception:
        raise _contract_error(
            f"{prefix} failed contract construction", field=prefix, rule="construct"
        ) from None


def _encode_industries(
    value: tuple[IndustryPerformanceRow, ...],
) -> list[dict[str, object]]:
    if not isinstance(value, tuple):
        raise _contract_error(
            "value must be a tuple of IndustryPerformanceRow",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    out: list[dict[str, object]] = []
    for idx, row in enumerate(value):
        if not isinstance(row, IndustryPerformanceRow):
            raise _contract_error(
                "value elements must be IndustryPerformanceRow",
                field="value",
                rule="element_type",
                index=idx,
            )
        out.append(_encode_industry_row(row, prefix=f"value[{idx}]"))
    return out


def _decode_industries(raw: object) -> tuple[IndustryPerformanceRow, ...]:
    if not isinstance(raw, list):
        raise _contract_error(
            "value must be an array",
            field="value",
            rule="type",
            type=type(raw).__name__,
        )
    return tuple(_decode_industry_row(item, prefix=f"value[{idx}]") for idx, item in enumerate(raw))


_BOARD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "advancing_count",
        "declining_count",
        "unchanged_count",
        "limit_up_count",
        "limit_down_count",
        "broken_limit_count",
        "total_turnover_cny",
        "median_change_percent",
        "industries",
    }
)


def _encode_market_board(value: MarketBoardSnapshot) -> dict[str, object]:
    if not isinstance(value, MarketBoardSnapshot):
        raise _contract_error(
            "value must be MarketBoardSnapshot",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    industries: list[dict[str, object]] = []
    for idx, row in enumerate(value.industries):
        industries.append(_encode_industry_row(row, prefix=f"value.industries[{idx}]"))
    return {
        "trade_date": _encode_date(value.trade_date, field="value.trade_date"),
        "advancing_count": _encode_int(value.advancing_count, field="value.advancing_count"),
        "declining_count": _encode_int(value.declining_count, field="value.declining_count"),
        "unchanged_count": _encode_int(value.unchanged_count, field="value.unchanged_count"),
        "limit_up_count": _encode_int(value.limit_up_count, field="value.limit_up_count"),
        "limit_down_count": _encode_int(value.limit_down_count, field="value.limit_down_count"),
        "broken_limit_count": _encode_int(
            value.broken_limit_count, field="value.broken_limit_count"
        ),
        "total_turnover_cny": _encode_optional_decimal(
            value.total_turnover_cny, field="value.total_turnover_cny"
        ),
        "median_change_percent": _encode_optional_decimal(
            value.median_change_percent, field="value.median_change_percent"
        ),
        "industries": industries,
    }


def _decode_market_board(raw: object) -> MarketBoardSnapshot:
    obj = _require_mapping(raw, field="value", required_keys=_BOARD_KEYS)
    industries_raw = obj["industries"]
    if not isinstance(industries_raw, list):
        raise _contract_error(
            "value.industries must be an array",
            field="value.industries",
            rule="type",
            type=type(industries_raw).__name__,
        )
    industries = tuple(
        _decode_industry_row(item, prefix=f"value.industries[{idx}]")
        for idx, item in enumerate(industries_raw)
    )
    try:
        return MarketBoardSnapshot(
            trade_date=_decode_date(obj["trade_date"], field="value.trade_date"),
            advancing_count=_decode_int(obj["advancing_count"], field="value.advancing_count"),
            declining_count=_decode_int(obj["declining_count"], field="value.declining_count"),
            unchanged_count=_decode_int(obj["unchanged_count"], field="value.unchanged_count"),
            limit_up_count=_decode_int(obj["limit_up_count"], field="value.limit_up_count"),
            limit_down_count=_decode_int(obj["limit_down_count"], field="value.limit_down_count"),
            broken_limit_count=_decode_int(
                obj["broken_limit_count"], field="value.broken_limit_count"
            ),
            total_turnover_cny=_decode_optional_decimal(
                obj["total_turnover_cny"], field="value.total_turnover_cny"
            ),
            median_change_percent=_decode_optional_decimal(
                obj["median_change_percent"], field="value.median_change_percent"
            ),
            industries=industries,
        )
    except DataContractError:
        raise
    except Exception:
        raise _contract_error(
            "value failed contract construction", field="value", rule="construct"
        ) from None

def quote_codec() -> AShareProviderCacheCodec[AShareQuote]:
    return AShareProviderCacheCodec(
        CODEC_QUOTE,
        _encode_quote,
        _decode_quote,
        expected_category=DataCategory.MARKET_QUOTE,
    )


def bars_codec() -> AShareProviderCacheCodec[tuple[AShareBar, ...]]:
    return AShareProviderCacheCodec(
        CODEC_BARS,
        _encode_bars,
        _decode_bars,
        expected_category=DataCategory.MARKET_OHLCV,
    )


def order_book_codec() -> AShareProviderCacheCodec[tuple[OrderBookLevel, ...]]:
    return AShareProviderCacheCodec(
        CODEC_ORDER_BOOK,
        _encode_order_book,
        _decode_order_book,
        expected_category=DataCategory.MARKET_STRUCTURE,
    )


def ticks_codec() -> AShareProviderCacheCodec[tuple[TradeTick, ...]]:
    return AShareProviderCacheCodec(
        CODEC_TICKS,
        _encode_ticks,
        _decode_ticks,
        expected_category=DataCategory.MARKET_STRUCTURE,
    )


def industry_performance_codec() -> AShareProviderCacheCodec[tuple[IndustryPerformanceRow, ...]]:
    return AShareProviderCacheCodec(
        CODEC_INDUSTRY_PERFORMANCE,
        _encode_industries,
        _decode_industries,
        expected_category=DataCategory.MARKET_STRUCTURE,
    )


def market_board_codec() -> AShareProviderCacheCodec[MarketBoardSnapshot]:
    return AShareProviderCacheCodec(
        CODEC_MARKET_BOARD,
        _encode_market_board,
        _decode_market_board,
        expected_category=DataCategory.MARKET_STRUCTURE,
    )


E2_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_QUOTE: quote_codec,  # type: ignore[dict-item]
    CODEC_BARS: bars_codec,  # type: ignore[dict-item]
    CODEC_ORDER_BOOK: order_book_codec,  # type: ignore[dict-item]
    CODEC_TICKS: ticks_codec,  # type: ignore[dict-item]
    CODEC_INDUSTRY_PERFORMANCE: industry_performance_codec,  # type: ignore[dict-item]
    CODEC_MARKET_BOARD: market_board_codec,  # type: ignore[dict-item]
}

__all__ = [
    "CODEC_BARS",
    "CODEC_INDUSTRY_PERFORMANCE",
    "CODEC_MARKET_BOARD",
    "CODEC_ORDER_BOOK",
    "CODEC_QUOTE",
    "CODEC_TICKS",
    "E2_CODEC_IDS",
    "E2_CODECS",
    "AShareProviderCacheCodec",
    "bars_codec",
    "industry_performance_codec",
    "market_board_codec",
    "order_book_codec",
    "quote_codec",
    "ticks_codec",
]
