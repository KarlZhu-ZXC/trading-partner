"""Explicit A-share provider cache codecs (Phase 1E E2 inventory).

``AShareProviderCacheCodec`` injects encode/decode callables — no reflection,
pickle, ``default=str``, float intermediates, or open Mapping domain outputs.
E2 codecs: quote, bars, order_book, ticks, industry_performance, market_board.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Final

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from domain.a_share.enums import BarInterval, TickDirection
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
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    Freshness,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError

_CANONICAL_DECIMAL_RE: Final[re.Pattern[str]] = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset({"codec", "meta", "schema_version", "value"})
_META_KEYS: Final[frozenset[str]] = frozenset(
    {
        "vendor",
        "category",
        "role",
        "as_of",
        "fetched_at",
        "freshness",
        "session",
        "latency_ms",
        "cache_disposition",
        "adjustment",
        "data_delay_seconds",
        "warnings",
    }
)
_SCHEMA_VERSION: Final[int] = 1

_VENDOR_BY_VALUE: Final[Mapping[str, VendorId]] = {m.value: m for m in VendorId}
_CATEGORY_BY_VALUE: Final[Mapping[str, DataCategory]] = {m.value: m for m in DataCategory}
_ROLE_BY_VALUE: Final[Mapping[str, SourceRole]] = {m.value: m for m in SourceRole}
_FRESHNESS_BY_VALUE: Final[Mapping[str, Freshness]] = {m.value: m for m in Freshness}
_SESSION_BY_VALUE: Final[Mapping[str, TradingSession]] = {m.value: m for m in TradingSession}
_CACHE_DISP_BY_VALUE: Final[Mapping[str, CacheDisposition]] = {m.value: m for m in CacheDisposition}
_ADJUSTMENT_BY_VALUE: Final[Mapping[str, AdjustmentMethod]] = {m.value: m for m in AdjustmentMethod}
_BAR_INTERVAL_BY_VALUE: Final[Mapping[str, BarInterval]] = {m.value: m for m in BarInterval}
_TICK_DIR_BY_VALUE: Final[Mapping[str, TickDirection]] = {m.value: m for m in TickDirection}

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


def _contract_error(message: str, *, field: str, rule: str, **extra: object) -> DataContractError:
    details: dict[str, object] = {"field": field, "rule": rule}
    details.update(extra)
    return DataContractError(message, details=details)


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate JSON object keys at every nesting level."""
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise _contract_error(
                "JSON object contains duplicate keys",
                field="payload_json",
                rule="duplicate_key",
                key=key,
            )
        out[key] = value
    return out


def _reject_nonfinite_constant(name: str) -> None:
    raise _contract_error(
        "JSON non-finite constant is not allowed",
        field="payload_json",
        rule="no_nan_infinity",
        constant=name,
    )


def _loads_cache_json(text: str) -> object:
    """Strict JSON load: no NaN/Infinity, reject duplicate keys at all levels."""
    return json.loads(
        text,
        parse_constant=_reject_nonfinite_constant,
        object_pairs_hook=_reject_duplicate_object_pairs,
    )


def _require_mapping(
    value: object, *, field: str, required_keys: frozenset[str]
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _contract_error(
            f"{field} must be an object",
            field=field,
            rule="type",
            type=type(value).__name__,
        )
    if any(not isinstance(k, str) for k in value):
        raise _contract_error(f"{field} keys must be strings", field=field, rule="key_type")
    keys = frozenset(value)
    if keys != required_keys:
        if not keys <= required_keys:
            raise _contract_error(f"{field} contains unknown keys", field=field, rule="extra_keys")
        raise _contract_error(f"{field} is missing required keys", field=field, rule="missing_keys")
    return value


def _encode_decimal(value: Decimal, *, field: str) -> str:
    if type(value) is not Decimal:
        raise _contract_error(
            f"{field} must be Decimal",
            field=field,
            rule="decimal_type",
            type=type(value).__name__,
        )
    if not value.is_finite():
        raise _contract_error(
            f"{field} must be a finite Decimal", field=field, rule="finite_decimal"
        )
    raw = str(value)
    encoded = format(value, "f") if "E" in raw.upper() else raw
    if not _CANONICAL_DECIMAL_RE.fullmatch(encoded):
        raise _contract_error(
            f"{field} could not be encoded as a canonical decimal string",
            field=field,
            rule="canonical_decimal_string",
        )
    return encoded


def _encode_optional_decimal(value: Decimal | None, *, field: str) -> str | None:
    if value is None:
        return None
    return _encode_decimal(value, field=field)


def _decode_decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise _contract_error(
            f"{field} must be a decimal string",
            field=field,
            rule="decimal_string",
            type=type(value).__name__,
        )
    if not _CANONICAL_DECIMAL_RE.fullmatch(value):
        raise _contract_error(
            f"{field} must be a canonical fixed-point decimal string",
            field=field,
            rule="canonical_decimal_string",
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError, ArithmeticError):
        raise _contract_error(
            f"{field} is not a valid decimal string",
            field=field,
            rule="decimal_parse",
        ) from None
    if not parsed.is_finite():
        raise _contract_error(
            f"{field} must be a finite Decimal", field=field, rule="finite_decimal"
        )
    return parsed


def _decode_optional_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _decode_decimal(value, field=field)


def _encode_datetime(value: datetime, *, field: str) -> str:
    if not isinstance(value, datetime):
        raise _contract_error(
            f"{field} must be a datetime",
            field=field,
            rule="type",
            type=type(value).__name__,
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise _contract_error(f"{field} must be timezone-aware", field=field, rule="timezone_aware")
    return value.isoformat()


def _decode_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise _contract_error(
            f"{field} must be an ISO 8601 datetime string",
            field=field,
            rule="datetime_string",
            type=type(value).__name__,
        )
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise _contract_error(
            f"{field} is not a valid ISO 8601 datetime",
            field=field,
            rule="datetime_parse",
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _contract_error(f"{field} must be timezone-aware", field=field, rule="timezone_aware")
    if parsed.isoformat() != value:
        raise _contract_error(
            f"{field} must be canonical ISO 8601 (isoformat round-trip)",
            field=field,
            rule="canonical_isoformat",
        )
    return parsed


def _encode_date(value: date, *, field: str) -> str:
    if type(value) is not date:
        raise _contract_error(
            f"{field} must be a date",
            field=field,
            rule="type",
            type=type(value).__name__,
        )
    return value.isoformat()


def _decode_date(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise _contract_error(
            f"{field} must be an ISO date string",
            field=field,
            rule="date_string",
            type=type(value).__name__,
        )
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _contract_error(
            f"{field} is not a valid ISO date", field=field, rule="date_parse"
        ) from None


def _encode_str(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise _contract_error(
            f"{field} must be a string",
            field=field,
            rule="string_type",
            type=type(value).__name__,
        )
    return value


def _encode_optional_str(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _encode_str(value, field=field)


def _decode_str(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise _contract_error(
            f"{field} must be a string",
            field=field,
            rule="string_type",
            type=type(value).__name__,
        )
    return value


def _decode_optional_str(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _decode_str(value, field=field)


def _encode_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _contract_error(
            f"{field} must be an int",
            field=field,
            rule="int_type",
            type=type(value).__name__,
        )
    return value


def _encode_optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _encode_int(value, field=field)


def _decode_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _contract_error(
            f"{field} must be an int",
            field=field,
            rule="int_type",
            type=type(value).__name__,
        )
    return value


def _decode_optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _decode_int(value, field=field)


def _encode_enum(value: object, *, field: str, table: Mapping[str, Enum]) -> str:
    for member in table.values():
        if value is member:
            wire = member.value
            if not isinstance(wire, str):
                raise _contract_error(
                    f"{field} must be a str enum member",
                    field=field,
                    rule="enum_type",
                    type=type(value).__name__,
                )
            return wire
    raise _contract_error(
        f"{field} must be an exact frozen enum member",
        field=field,
        rule="enum_type",
        type=type(value).__name__,
    )


def _encode_optional_enum(value: object, *, field: str, table: Mapping[str, Enum]) -> str | None:
    if value is None:
        return None
    return _encode_enum(value, field=field, table=table)


def _decode_enum[E](value: object, *, field: str, table: Mapping[str, E]) -> E:
    if not isinstance(value, str):
        raise _contract_error(
            f"{field} must be a string enum value",
            field=field,
            rule="enum_type",
            type=type(value).__name__,
        )
    mapped = table.get(value)
    if mapped is None:
        raise _contract_error(f"{field} is not a known enum value", field=field, rule="enum_value")
    return mapped


def _decode_optional_enum[E](value: object, *, field: str, table: Mapping[str, E]) -> E | None:
    if value is None:
        return None
    return _decode_enum(value, field=field, table=table)


def _encode_warning_codes(warnings: object, *, field: str) -> list[str]:
    if not isinstance(warnings, Sequence) or isinstance(warnings, (str, bytes)):
        raise _contract_error(
            f"{field} must be a sequence of strings",
            field=field,
            rule="type",
            type=type(warnings).__name__,
        )
    out: list[str] = []
    for idx, code in enumerate(warnings):
        if not isinstance(code, str):
            raise _contract_error(
                f"{field} elements must be strings",
                field=field,
                rule="element_type",
                index=idx,
            )
        out.append(code)
    return out


def _encode_meta(meta: ProviderResultMeta) -> dict[str, object]:
    return {
        "vendor": _encode_enum(meta.vendor, field="meta.vendor", table=_VENDOR_BY_VALUE),
        "category": _encode_enum(meta.category, field="meta.category", table=_CATEGORY_BY_VALUE),
        "role": _encode_enum(meta.role, field="meta.role", table=_ROLE_BY_VALUE),
        "as_of": _encode_datetime(meta.as_of, field="meta.as_of"),
        "fetched_at": _encode_datetime(meta.fetched_at, field="meta.fetched_at"),
        "freshness": _encode_enum(
            meta.freshness, field="meta.freshness", table=_FRESHNESS_BY_VALUE
        ),
        "session": _encode_enum(meta.session, field="meta.session", table=_SESSION_BY_VALUE),
        "latency_ms": _encode_optional_int(meta.latency_ms, field="meta.latency_ms"),
        "cache_disposition": _encode_enum(
            meta.cache_disposition,
            field="meta.cache_disposition",
            table=_CACHE_DISP_BY_VALUE,
        ),
        "adjustment": _encode_optional_enum(
            meta.adjustment, field="meta.adjustment", table=_ADJUSTMENT_BY_VALUE
        ),
        "data_delay_seconds": _encode_optional_int(
            meta.data_delay_seconds, field="meta.data_delay_seconds"
        ),
        "warnings": _encode_warning_codes(meta.warnings, field="meta.warnings"),
    }


def _decode_meta(raw: object) -> ProviderResultMeta:
    obj = _require_mapping(raw, field="meta", required_keys=_META_KEYS)
    warnings_raw = obj["warnings"]
    if not isinstance(warnings_raw, list):
        raise _contract_error(
            "meta.warnings must be an array",
            field="meta.warnings",
            rule="type",
            type=type(warnings_raw).__name__,
        )
    warnings: list[str] = []
    for idx, code in enumerate(warnings_raw):
        if not isinstance(code, str):
            raise _contract_error(
                "meta.warnings elements must be strings",
                field="meta.warnings",
                rule="element_type",
                index=idx,
            )
        warnings.append(code)
    try:
        return ProviderResultMeta(
            vendor=_decode_enum(obj["vendor"], field="meta.vendor", table=_VENDOR_BY_VALUE),
            category=_decode_enum(obj["category"], field="meta.category", table=_CATEGORY_BY_VALUE),
            role=_decode_enum(obj["role"], field="meta.role", table=_ROLE_BY_VALUE),
            as_of=_decode_datetime(obj["as_of"], field="meta.as_of"),
            fetched_at=_decode_datetime(obj["fetched_at"], field="meta.fetched_at"),
            freshness=_decode_enum(
                obj["freshness"], field="meta.freshness", table=_FRESHNESS_BY_VALUE
            ),
            session=_decode_enum(obj["session"], field="meta.session", table=_SESSION_BY_VALUE),
            latency_ms=_decode_optional_int(obj["latency_ms"], field="meta.latency_ms"),
            cache_disposition=_decode_enum(
                obj["cache_disposition"],
                field="meta.cache_disposition",
                table=_CACHE_DISP_BY_VALUE,
            ),
            adjustment=_decode_optional_enum(
                obj["adjustment"],
                field="meta.adjustment",
                table=_ADJUSTMENT_BY_VALUE,
            ),
            data_delay_seconds=_decode_optional_int(
                obj["data_delay_seconds"], field="meta.data_delay_seconds"
            ),
            warnings=tuple(warnings),
        )
    except DataContractError:
        raise
    except Exception:
        raise _contract_error(
            "meta failed contract construction", field="meta", rule="construct"
        ) from None


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


class AShareProviderCacheCodec[T]:
    """Typed provider cache codec with explicit encode/decode callables."""

    def __init__(
        self,
        codec_id: str,
        encode_value: Callable[[T], object],
        decode_value: Callable[[object], T],
        *,
        expected_category: DataCategory | None = None,
    ) -> None:
        if not isinstance(codec_id, str) or not codec_id.strip():
            raise DataContractError(
                "codec_id must be a non-blank string",
                details={"field": "codec_id", "rule": "non_blank"},
            )
        if not callable(encode_value) or not callable(decode_value):
            raise DataContractError(
                "encode_value and decode_value must be callable",
                details={"field": "codec", "rule": "callable"},
            )
        self._codec_id = codec_id
        self._encode_value = encode_value
        self._decode_value = decode_value
        self._expected_category = expected_category

    @property
    def codec_id(self) -> str:
        return self._codec_id

    def encode(self, success: ProviderSuccess[T]) -> str:
        if not isinstance(success, ProviderSuccess):
            raise _contract_error(
                "success must be ProviderSuccess",
                field="success",
                rule="type",
                type=type(success).__name__,
            )
        payload = {
            "codec": self._codec_id,
            "schema_version": _SCHEMA_VERSION,
            "meta": _encode_meta(success.meta),
            "value": self._encode_value(success.value),
        }
        # Canonical JSON: sorted keys, no whitespace, ensure_ascii, no default=.
        return json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )

    def decode(self, entry: CacheEntry) -> ProviderSuccess[T]:
        if not isinstance(entry, CacheEntry):
            raise _contract_error(
                "entry must be CacheEntry",
                field="entry",
                rule="type",
                type=type(entry).__name__,
            )
        if not isinstance(entry.payload_json, str):
            raise _contract_error(
                "entry.payload_json must be a string",
                field="entry.payload_json",
                rule="type",
                type=type(entry.payload_json).__name__,
            )
        try:
            raw = _loads_cache_json(entry.payload_json)
        except DataContractError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _contract_error(
                "payload_json is not valid JSON", field="payload_json", rule="json"
            ) from None
        obj = _require_mapping(raw, field="payload", required_keys=_TOP_LEVEL_KEYS)
        if obj["codec"] != self._codec_id:
            raise _contract_error("payload codec id mismatch", field="codec", rule="codec_id")
        if obj["schema_version"] != _SCHEMA_VERSION:
            raise _contract_error(
                "payload schema_version mismatch",
                field="schema_version",
                rule="schema_version",
            )
        meta = _decode_meta(obj["meta"])
        if self._expected_category is not None and meta.category is not self._expected_category:
            raise _contract_error(
                "meta.category does not match codec category",
                field="meta.category",
                rule="category",
            )
        if entry.vendor is not meta.vendor:
            raise _contract_error(
                "CacheEntry.vendor must equal meta.vendor",
                field="entry.vendor",
                rule="coherence_vendor",
            )
        if entry.category is not meta.category:
            raise _contract_error(
                "CacheEntry.category must equal meta.category",
                field="entry.category",
                rule="coherence_category",
            )
        if entry.as_of != meta.as_of:
            raise _contract_error(
                "CacheEntry.as_of must equal meta.as_of",
                field="entry.as_of",
                rule="coherence_as_of",
            )
        if entry.fetched_at != meta.fetched_at:
            raise _contract_error(
                "CacheEntry.fetched_at must equal meta.fetched_at",
                field="entry.fetched_at",
                rule="coherence_fetched_at",
            )
        value = self._decode_value(obj["value"])
        # Cache hits always surface HIT disposition.
        hit_meta = replace(meta, cache_disposition=CacheDisposition.HIT)
        return ProviderSuccess(value=value, meta=hit_meta)


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


# --- E3 codecs (§18.3) --------------------------------------------------------

from domain.a_share.enums import FinancialStatementType  # noqa: E402
from domain.a_share.models import (  # noqa: E402
    AnalystReportItem,
    AnnouncementItem,
    ConsensusEstimate,
    DividendRecord,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    NewsItem,
    UnlockRecord,
)
from domain.common.enums import ReliabilityLevel  # noqa: E402

CODEC_FUNDAMENTALS: Final[str] = "a_share_fundamentals.v1"
CODEC_F10: Final[str] = "a_share_f10.v1"
CODEC_STATEMENTS: Final[str] = "a_share_statements.v2"
CODEC_REPORTS: Final[str] = "a_share_reports.v1"
CODEC_CONSENSUS: Final[str] = "a_share_consensus.v1"
CODEC_ANNOUNCEMENTS: Final[str] = "a_share_announcements.v1"
CODEC_CORPORATE_ACTIONS: Final[str] = "a_share_corporate_actions.v1"
CODEC_NEWS: Final[str] = "a_share_news.v1"

E3_CODEC_IDS: Final[frozenset[str]] = frozenset(
    {
        CODEC_FUNDAMENTALS,
        CODEC_F10,
        CODEC_STATEMENTS,
        CODEC_REPORTS,
        CODEC_CONSENSUS,
        CODEC_ANNOUNCEMENTS,
        CODEC_CORPORATE_ACTIONS,
        CODEC_NEWS,
    }
)

_RELIABILITY_BY_VALUE: Final[Mapping[str, ReliabilityLevel]] = {
    m.value: m for m in ReliabilityLevel
}
_FIN_STMT_BY_VALUE: Final[Mapping[str, FinancialStatementType]] = {
    m.value: m for m in FinancialStatementType
}


def _encode_optional_datetime(value: datetime | None, *, field: str) -> str | None:
    if value is None:
        return None
    return _encode_datetime(value, field=field)


def _decode_optional_datetime(value: object, *, field: str) -> datetime | None:
    if value is None:
        return None
    return _decode_datetime(value, field=field)


def _encode_metric_value(value: Decimal | str | int | None, *, field: str) -> object:
    if value is None:
        return None
    if type(value) is Decimal:
        return {"t": "d", "v": _encode_decimal(value, field=field)}
    if type(value) is int:
        return {"t": "i", "v": value}
    if isinstance(value, str):
        return {"t": "s", "v": value}
    raise _contract_error(
        f"{field} has unsupported metric value type",
        field=field,
        rule="value_type",
        type=type(value).__name__,
    )


def _decode_metric_value(value: object, *, field: str) -> Decimal | str | int | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _contract_error(
            f"{field} must be a typed metric value object",
            field=field,
            rule="type",
        )
    keys = frozenset(value)
    if keys != frozenset({"t", "v"}):
        raise _contract_error(
            f"{field} metric value keys invalid",
            field=field,
            rule="keys",
        )
    t = value["t"]
    if t == "d":
        return _decode_decimal(value["v"], field=field)
    if t == "i":
        return _decode_int(value["v"], field=field)
    if t == "s":
        return _decode_str(value["v"], field=field)
    raise _contract_error(
        f"{field} unknown metric value tag",
        field=field,
        rule="tag",
    )


_FUND_KEYS: Final[frozenset[str]] = frozenset(
    {"name", "value", "unit", "period_end", "published_at"}
)


def _encode_fundamental(item: FundamentalMetric) -> dict[str, object]:
    if not isinstance(item, FundamentalMetric):
        raise _contract_error(
            "value element must be FundamentalMetric",
            field="value",
            rule="type",
            type=type(item).__name__,
        )
    return {
        "name": _encode_str(item.name, field="name"),
        "value": _encode_metric_value(item.value, field="value"),
        "unit": _encode_optional_str(item.unit, field="unit"),
        "period_end": (
            _encode_date(item.period_end, field="period_end")
            if item.period_end is not None
            else None
        ),
        "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
    }


def _decode_fundamental(raw: object) -> FundamentalMetric:
    obj = _require_mapping(raw, field="value[]", required_keys=_FUND_KEYS)
    period_raw = obj["period_end"]
    period_end = None if period_raw is None else _decode_date(period_raw, field="period_end")
    return FundamentalMetric(
        name=_decode_str(obj["name"], field="name"),
        value=_decode_metric_value(obj["value"], field="value"),
        unit=_decode_optional_str(obj["unit"], field="unit"),
        period_end=period_end,
        published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
    )


def _encode_fundamentals(value: tuple[FundamentalMetric, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_fundamental(item) for item in value]


def _decode_fundamentals(raw: object) -> tuple[FundamentalMetric, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_fundamental(item) for item in raw)


_F10_KEYS: Final[frozenset[str]] = frozenset({"section", "title", "body", "as_of"})


def _encode_f10(item: F10Section) -> dict[str, object]:
    if not isinstance(item, F10Section):
        raise _contract_error("value element must be F10Section", field="value", rule="type")
    return {
        "section": _encode_str(item.section, field="section"),
        "title": _encode_str(item.title, field="title"),
        "body": _encode_str(item.body, field="body"),
        "as_of": _encode_datetime(item.as_of, field="as_of"),
    }


def _decode_f10(raw: object) -> F10Section:
    obj = _require_mapping(raw, field="value[]", required_keys=_F10_KEYS)
    return F10Section(
        section=_decode_str(obj["section"], field="section"),
        title=_decode_str(obj["title"], field="title"),
        body=_decode_str(obj["body"], field="body"),
        as_of=_decode_datetime(obj["as_of"], field="as_of"),
    )


def _encode_f10_sections(value: tuple[F10Section, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_f10(item) for item in value]


def _decode_f10_sections(raw: object) -> tuple[F10Section, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_f10(item) for item in raw)


_STMT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "statement_type",
        "period_end",
        "published_at",
        "item_code",
        "item_name",
        "value",
        "unit",
    }
)


def _encode_statement(item: FinancialStatementLine) -> dict[str, object]:
    if not isinstance(item, FinancialStatementLine):
        raise _contract_error(
            "value element must be FinancialStatementLine",
            field="value",
            rule="type",
        )
    return {
        "statement_type": _encode_enum(
            item.statement_type, field="statement_type", table=_FIN_STMT_BY_VALUE
        ),
        "period_end": _encode_date(item.period_end, field="period_end"),
        "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
        "item_code": _encode_str(item.item_code, field="item_code"),
        "item_name": _encode_str(item.item_name, field="item_name"),
        "value": _encode_optional_decimal(item.value, field="value"),
        "unit": _encode_str(item.unit, field="unit"),
    }


def _decode_statement(raw: object) -> FinancialStatementLine:
    obj = _require_mapping(raw, field="value[]", required_keys=_STMT_KEYS)
    return FinancialStatementLine(
        statement_type=_decode_enum(
            obj["statement_type"], field="statement_type", table=_FIN_STMT_BY_VALUE
        ),
        period_end=_decode_date(obj["period_end"], field="period_end"),
        published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
        item_code=_decode_str(obj["item_code"], field="item_code"),
        item_name=_decode_str(obj["item_name"], field="item_name"),
        value=_decode_optional_decimal(obj["value"], field="value"),
        unit=_decode_str(obj["unit"], field="unit"),
    )


def _encode_statements(value: tuple[FinancialStatementLine, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_statement(item) for item in value]


def _decode_statements(raw: object) -> tuple[FinancialStatementLine, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_statement(item) for item in raw)


_CONSENSUS_KEYS: Final[frozenset[str]] = frozenset(
    {"fiscal_year", "metric", "mean", "high", "low", "institution_count"}
)


def _encode_consensus_item(item: ConsensusEstimate) -> dict[str, object]:
    if not isinstance(item, ConsensusEstimate):
        raise _contract_error("value element must be ConsensusEstimate", field="value", rule="type")
    return {
        "fiscal_year": _encode_int(item.fiscal_year, field="fiscal_year"),
        "metric": _encode_str(item.metric, field="metric"),
        "mean": _encode_optional_decimal(item.mean, field="mean"),
        "high": _encode_optional_decimal(item.high, field="high"),
        "low": _encode_optional_decimal(item.low, field="low"),
        "institution_count": _encode_optional_int(
            item.institution_count, field="institution_count"
        ),
    }


def _decode_consensus_item(raw: object) -> ConsensusEstimate:
    obj = _require_mapping(raw, field="value[]", required_keys=_CONSENSUS_KEYS)
    return ConsensusEstimate(
        fiscal_year=_decode_int(obj["fiscal_year"], field="fiscal_year"),
        metric=_decode_str(obj["metric"], field="metric"),
        mean=_decode_optional_decimal(obj["mean"], field="mean"),
        high=_decode_optional_decimal(obj["high"], field="high"),
        low=_decode_optional_decimal(obj["low"], field="low"),
        institution_count=_decode_optional_int(obj["institution_count"], field="institution_count"),
    )


def _encode_consensus(value: tuple[ConsensusEstimate, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_consensus_item(item) for item in value]


def _decode_consensus(raw: object) -> tuple[ConsensusEstimate, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_consensus_item(item) for item in raw)


_REPORT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "report_key",
        "title",
        "institution",
        "analyst_names",
        "published_at",
        "rating",
        "target_price",
        "eps_forecasts",
        "source_url",
        "pdf_url",
    }
)


def _encode_report(item: AnalystReportItem) -> dict[str, object]:
    if not isinstance(item, AnalystReportItem):
        raise _contract_error("value element must be AnalystReportItem", field="value", rule="type")
    return {
        "report_key": _encode_str(item.report_key, field="report_key"),
        "title": _encode_str(item.title, field="title"),
        "institution": _encode_optional_str(item.institution, field="institution"),
        "analyst_names": list(item.analyst_names),
        "published_at": _encode_datetime(item.published_at, field="published_at"),
        "rating": _encode_optional_str(item.rating, field="rating"),
        "target_price": _encode_optional_decimal(item.target_price, field="target_price"),
        "eps_forecasts": _encode_consensus(item.eps_forecasts),
        "source_url": _encode_optional_str(item.source_url, field="source_url"),
        "pdf_url": _encode_optional_str(item.pdf_url, field="pdf_url"),
    }


def _decode_report(raw: object) -> AnalystReportItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_REPORT_KEYS)
    names_raw = obj["analyst_names"]
    if not isinstance(names_raw, list):
        raise _contract_error("analyst_names must be an array", field="analyst_names", rule="type")
    names = tuple(_decode_str(n, field="analyst_names[]") for n in names_raw)
    return AnalystReportItem(
        report_key=_decode_str(obj["report_key"], field="report_key"),
        title=_decode_str(obj["title"], field="title"),
        institution=_decode_optional_str(obj["institution"], field="institution"),
        analyst_names=names,
        published_at=_decode_datetime(obj["published_at"], field="published_at"),
        rating=_decode_optional_str(obj["rating"], field="rating"),
        target_price=_decode_optional_decimal(obj["target_price"], field="target_price"),
        eps_forecasts=_decode_consensus(obj["eps_forecasts"]),
        source_url=_decode_optional_str(obj["source_url"], field="source_url"),
        pdf_url=_decode_optional_str(obj["pdf_url"], field="pdf_url"),
    )


def _encode_reports(value: tuple[AnalystReportItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_report(item) for item in value]


def _decode_reports(raw: object) -> tuple[AnalystReportItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_report(item) for item in raw)


_ANN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "announcement_key",
        "title",
        "published_at",
        "category",
        "source_url",
        "pdf_url",
    }
)


def _encode_announcement(item: AnnouncementItem) -> dict[str, object]:
    if not isinstance(item, AnnouncementItem):
        raise _contract_error("value element must be AnnouncementItem", field="value", rule="type")
    return {
        "announcement_key": _encode_str(item.announcement_key, field="announcement_key"),
        "title": _encode_str(item.title, field="title"),
        "published_at": _encode_datetime(item.published_at, field="published_at"),
        "category": _encode_optional_str(item.category, field="category"),
        "source_url": _encode_str(item.source_url, field="source_url"),
        "pdf_url": _encode_optional_str(item.pdf_url, field="pdf_url"),
    }


def _decode_announcement(raw: object) -> AnnouncementItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_ANN_KEYS)
    return AnnouncementItem(
        announcement_key=_decode_str(obj["announcement_key"], field="announcement_key"),
        title=_decode_str(obj["title"], field="title"),
        published_at=_decode_datetime(obj["published_at"], field="published_at"),
        category=_decode_optional_str(obj["category"], field="category"),
        source_url=_decode_str(obj["source_url"], field="source_url"),
        pdf_url=_decode_optional_str(obj["pdf_url"], field="pdf_url"),
    )


def _encode_announcements(value: tuple[AnnouncementItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_announcement(item) for item in value]


def _decode_announcements(raw: object) -> tuple[AnnouncementItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_announcement(item) for item in raw)


_NEWS_KEYS: Final[frozenset[str]] = frozenset(
    {"news_key", "title", "summary", "published_at", "source_name", "source_url"}
)


def _encode_news_item(item: NewsItem) -> dict[str, object]:
    if not isinstance(item, NewsItem):
        raise _contract_error("value element must be NewsItem", field="value", rule="type")
    return {
        "news_key": _encode_str(item.news_key, field="news_key"),
        "title": _encode_str(item.title, field="title"),
        "summary": _encode_optional_str(item.summary, field="summary"),
        "published_at": _encode_datetime(item.published_at, field="published_at"),
        "source_name": _encode_str(item.source_name, field="source_name"),
        "source_url": _encode_optional_str(item.source_url, field="source_url"),
    }


def _decode_news_item(raw: object) -> NewsItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_NEWS_KEYS)
    return NewsItem(
        news_key=_decode_str(obj["news_key"], field="news_key"),
        title=_decode_str(obj["title"], field="title"),
        summary=_decode_optional_str(obj["summary"], field="summary"),
        published_at=_decode_datetime(obj["published_at"], field="published_at"),
        source_name=_decode_str(obj["source_name"], field="source_name"),
        source_url=_decode_optional_str(obj["source_url"], field="source_url"),
    )


def _encode_news(value: tuple[NewsItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_news_item(item) for item in value]


def _decode_news(raw: object) -> tuple[NewsItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_news_item(item) for item in raw)


_UNLOCK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "kind",
        "unlock_date",
        "published_at",
        "unlock_type",
        "unlock_shares",
        "tradable_shares",
        "market_value_cny",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_DIV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "kind",
        "fiscal_year",
        "plan_status",
        "ex_date",
        "cash_per_share",
        "bonus_shares_per_share",
        "transfer_shares_per_share",
        "published_at",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)


def _encode_action(item: UnlockRecord | DividendRecord) -> dict[str, object]:
    if isinstance(item, UnlockRecord):
        return {
            "kind": "unlock",
            "unlock_date": _encode_date(item.unlock_date, field="unlock_date"),
            "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
            "unlock_type": _encode_optional_str(item.unlock_type, field="unlock_type"),
            "unlock_shares": _encode_optional_int(item.unlock_shares, field="unlock_shares"),
            "tradable_shares": _encode_optional_int(item.tradable_shares, field="tradable_shares"),
            "market_value_cny": _encode_optional_decimal(
                item.market_value_cny, field="market_value_cny"
            ),
            "source_vendor": _encode_enum(
                item.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            "reliability": _encode_enum(
                item.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            "is_authoritative": bool(item.is_authoritative),
        }
    if isinstance(item, DividendRecord):
        return {
            "kind": "dividend",
            "fiscal_year": _encode_int(item.fiscal_year, field="fiscal_year"),
            "plan_status": _encode_str(item.plan_status, field="plan_status"),
            "ex_date": (
                _encode_date(item.ex_date, field="ex_date") if item.ex_date is not None else None
            ),
            "cash_per_share": _encode_optional_decimal(item.cash_per_share, field="cash_per_share"),
            "bonus_shares_per_share": _encode_optional_decimal(
                item.bonus_shares_per_share, field="bonus_shares_per_share"
            ),
            "transfer_shares_per_share": _encode_optional_decimal(
                item.transfer_shares_per_share, field="transfer_shares_per_share"
            ),
            "published_at": _encode_optional_datetime(item.published_at, field="published_at"),
            "source_vendor": _encode_enum(
                item.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            "reliability": _encode_enum(
                item.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            "is_authoritative": bool(item.is_authoritative),
        }
    raise _contract_error(
        "value element must be UnlockRecord or DividendRecord",
        field="value",
        rule="type",
        type=type(item).__name__,
    )


def _decode_action(raw: object) -> UnlockRecord | DividendRecord:
    if not isinstance(raw, dict) or "kind" not in raw:
        raise _contract_error(
            "corporate action must be a tagged object", field="value", rule="type"
        )
    kind = raw["kind"]
    if kind == "unlock":
        obj = _require_mapping(raw, field="value[]", required_keys=_UNLOCK_KEYS)
        return UnlockRecord(
            unlock_date=_decode_date(obj["unlock_date"], field="unlock_date"),
            published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
            unlock_type=_decode_optional_str(obj["unlock_type"], field="unlock_type"),
            unlock_shares=_decode_optional_int(obj["unlock_shares"], field="unlock_shares"),
            tradable_shares=_decode_optional_int(obj["tradable_shares"], field="tradable_shares"),
            market_value_cny=_decode_optional_decimal(
                obj["market_value_cny"], field="market_value_cny"
            ),
            source_vendor=_decode_enum(
                obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            reliability=_decode_enum(
                obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            is_authoritative=bool(obj["is_authoritative"]),
        )
    if kind == "dividend":
        obj = _require_mapping(raw, field="value[]", required_keys=_DIV_KEYS)
        ex_raw = obj["ex_date"]
        ex_date = None if ex_raw is None else _decode_date(ex_raw, field="ex_date")
        return DividendRecord(
            fiscal_year=_decode_int(obj["fiscal_year"], field="fiscal_year"),
            plan_status=_decode_str(obj["plan_status"], field="plan_status"),
            ex_date=ex_date,
            cash_per_share=_decode_optional_decimal(obj["cash_per_share"], field="cash_per_share"),
            bonus_shares_per_share=_decode_optional_decimal(
                obj["bonus_shares_per_share"], field="bonus_shares_per_share"
            ),
            transfer_shares_per_share=_decode_optional_decimal(
                obj["transfer_shares_per_share"], field="transfer_shares_per_share"
            ),
            published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
            source_vendor=_decode_enum(
                obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
            ),
            reliability=_decode_enum(
                obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
            ),
            is_authoritative=bool(obj["is_authoritative"]),
        )
    raise _contract_error(
        "corporate action kind must be unlock or dividend",
        field="kind",
        rule="enum",
    )


def _encode_actions(value: tuple[UnlockRecord | DividendRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_action(item) for item in value]


def _decode_actions(raw: object) -> tuple[UnlockRecord | DividendRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_action(item) for item in raw)


def fundamentals_codec() -> AShareProviderCacheCodec[tuple[FundamentalMetric, ...]]:
    return AShareProviderCacheCodec(
        CODEC_FUNDAMENTALS,
        _encode_fundamentals,
        _decode_fundamentals,
        expected_category=DataCategory.FUNDAMENTALS,
    )


def f10_codec() -> AShareProviderCacheCodec[tuple[F10Section, ...]]:
    return AShareProviderCacheCodec(
        CODEC_F10,
        _encode_f10_sections,
        _decode_f10_sections,
        expected_category=DataCategory.FUNDAMENTALS,
    )


def statements_codec() -> AShareProviderCacheCodec[tuple[FinancialStatementLine, ...]]:
    return AShareProviderCacheCodec(
        CODEC_STATEMENTS,
        _encode_statements,
        _decode_statements,
        expected_category=DataCategory.FINANCIAL_STATEMENTS,
    )


def reports_codec() -> AShareProviderCacheCodec[tuple[AnalystReportItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_REPORTS,
        _encode_reports,
        _decode_reports,
        expected_category=DataCategory.RESEARCH_REPORTS,
    )


def consensus_codec() -> AShareProviderCacheCodec[tuple[ConsensusEstimate, ...]]:
    return AShareProviderCacheCodec(
        CODEC_CONSENSUS,
        _encode_consensus,
        _decode_consensus,
        expected_category=DataCategory.RESEARCH_REPORTS,
    )


def announcements_codec() -> AShareProviderCacheCodec[tuple[AnnouncementItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_ANNOUNCEMENTS,
        _encode_announcements,
        _decode_announcements,
        expected_category=DataCategory.ANNOUNCEMENTS,
    )


def corporate_actions_codec() -> AShareProviderCacheCodec[
    tuple[UnlockRecord | DividendRecord, ...]
]:
    return AShareProviderCacheCodec(
        CODEC_CORPORATE_ACTIONS,
        _encode_actions,
        _decode_actions,
        expected_category=DataCategory.CORPORATE_ACTIONS,
    )


def news_codec() -> AShareProviderCacheCodec[tuple[NewsItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_NEWS,
        _encode_news,
        _decode_news,
        expected_category=DataCategory.NEWS,
    )


E3_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_FUNDAMENTALS: fundamentals_codec,  # type: ignore[dict-item]
    CODEC_F10: f10_codec,  # type: ignore[dict-item]
    CODEC_STATEMENTS: statements_codec,  # type: ignore[dict-item]
    CODEC_REPORTS: reports_codec,  # type: ignore[dict-item]
    CODEC_CONSENSUS: consensus_codec,  # type: ignore[dict-item]
    CODEC_ANNOUNCEMENTS: announcements_codec,  # type: ignore[dict-item]
    CODEC_CORPORATE_ACTIONS: corporate_actions_codec,  # type: ignore[dict-item]
    CODEC_NEWS: news_codec,  # type: ignore[dict-item]
}

# ---------------------------------------------------------------------------
# E4a capital codecs (§18.3)
# ---------------------------------------------------------------------------

from domain.a_share.models import (  # noqa: E402
    BlockTradeRecord,
    ChipDistributionBin,
    ChipDistributionSnapshot,
    DragonTigerRecord,
    DragonTigerSeat,
    FundFlowPoint,
    MarginRecord,
    NorthboundFlowPoint,
    ShareholderCountRecord,
)

CODEC_INTRADAY_FLOW: Final[str] = "a_share_intraday_flow.v1"
CODEC_DAILY_FLOW: Final[str] = "a_share_daily_flow.v1"
CODEC_NORTHBOUND: Final[str] = "a_share_northbound.v1"
CODEC_DRAGON_TIGER: Final[str] = "a_share_dragon_tiger.v1"
CODEC_MARGIN: Final[str] = "a_share_margin.v1"
CODEC_BLOCK_TRADES: Final[str] = "a_share_block_trades.v1"
CODEC_SHAREHOLDER_COUNTS: Final[str] = "a_share_shareholder_counts.v1"
CODEC_CHIP_DISTRIBUTION: Final[str] = "a_share_chip_distribution.v2"

E4A_CODEC_IDS: Final[frozenset[str]] = frozenset(
    {
        CODEC_INTRADAY_FLOW,
        CODEC_DAILY_FLOW,
        CODEC_NORTHBOUND,
        CODEC_DRAGON_TIGER,
        CODEC_MARGIN,
        CODEC_BLOCK_TRADES,
        CODEC_SHAREHOLDER_COUNTS,
        CODEC_CHIP_DISTRIBUTION,
    }
)

_FUND_FLOW_KEYS: Final[frozenset[str]] = frozenset(
    {
        "occurred_at",
        "interval",
        "main_net_cny",
        "super_large_net_cny",
        "large_net_cny",
        "medium_net_cny",
        "small_net_cny",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_NORTHBOUND_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "channel",
        "net_buy_cny",
        "buy_cny",
        "sell_cny",
        "disclosure_note",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_SEAT_KEYS: Final[frozenset[str]] = frozenset(
    {"rank", "side", "branch_name", "amount_cny", "is_institution"}
)
_DRAGON_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "instrument_id",
        "reason",
        "buy_total_cny",
        "sell_total_cny",
        "net_buy_cny",
        "seats",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_MARGIN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "financing_balance_cny",
        "financing_buy_cny",
        "financing_repayment_cny",
        "securities_lending_balance_cny",
        "securities_lending_sell_shares",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_BLOCK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "price",
        "volume_shares",
        "amount_cny",
        "premium_percent",
        "buyer_branch",
        "seller_branch",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_HOLDER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "period_end",
        "published_at",
        "shareholder_count",
        "change_percent",
        "average_holding_shares",
        "source_vendor",
        "reliability",
        "is_authoritative",
    }
)
_BIN_KEYS: Final[frozenset[str]] = frozenset({"price_low", "price_high", "holding_ratio"})
_CHIP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "as_of",
        "bins",
        "profit_ratio",
        "average_cost",
        "concentration_90",
        "concentration_70",
        "source_vendor",
        "reliability",
        "is_authoritative",
        "calculation_method",
        "algorithm_version",
        "lookback_sessions",
        "input_adjustment",
        "bar_trade_date",
    }
)


def _encode_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise _contract_error(
            f"{field} must be bool", field=field, rule="bool_type", type=type(value).__name__
        )
    return value


def _decode_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise _contract_error(
            f"{field} must be bool", field=field, rule="bool_type", type=type(value).__name__
        )
    return value


def _encode_fund_flow_point(point: FundFlowPoint) -> dict[str, object]:
    if not isinstance(point, FundFlowPoint):
        raise _contract_error(
            "value element must be FundFlowPoint",
            field="value",
            rule="type",
            type=type(point).__name__,
        )
    return {
        "occurred_at": _encode_datetime(point.occurred_at, field="occurred_at"),
        "interval": _encode_enum(point.interval, field="interval", table=_BAR_INTERVAL_BY_VALUE),
        "main_net_cny": _encode_optional_decimal(point.main_net_cny, field="main_net_cny"),
        "super_large_net_cny": _encode_optional_decimal(
            point.super_large_net_cny, field="super_large_net_cny"
        ),
        "large_net_cny": _encode_optional_decimal(point.large_net_cny, field="large_net_cny"),
        "medium_net_cny": _encode_optional_decimal(point.medium_net_cny, field="medium_net_cny"),
        "small_net_cny": _encode_optional_decimal(point.small_net_cny, field="small_net_cny"),
        "source_vendor": _encode_enum(
            point.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            point.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(point.is_authoritative, field="is_authoritative"),
    }


def _decode_fund_flow_point(raw: object) -> FundFlowPoint:
    obj = _require_mapping(raw, field="value[]", required_keys=_FUND_FLOW_KEYS)
    return FundFlowPoint(
        occurred_at=_decode_datetime(obj["occurred_at"], field="occurred_at"),
        interval=_decode_enum(obj["interval"], field="interval", table=_BAR_INTERVAL_BY_VALUE),
        main_net_cny=_decode_optional_decimal(obj["main_net_cny"], field="main_net_cny"),
        super_large_net_cny=_decode_optional_decimal(
            obj["super_large_net_cny"], field="super_large_net_cny"
        ),
        large_net_cny=_decode_optional_decimal(obj["large_net_cny"], field="large_net_cny"),
        medium_net_cny=_decode_optional_decimal(obj["medium_net_cny"], field="medium_net_cny"),
        small_net_cny=_decode_optional_decimal(obj["small_net_cny"], field="small_net_cny"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_fund_flow(value: tuple[FundFlowPoint, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_fund_flow_point(p) for p in value]


def _decode_fund_flow(raw: object) -> tuple[FundFlowPoint, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_fund_flow_point(item) for item in raw)


def _encode_northbound_point(point: NorthboundFlowPoint) -> dict[str, object]:
    if not isinstance(point, NorthboundFlowPoint):
        raise _contract_error(
            "value element must be NorthboundFlowPoint",
            field="value",
            rule="type",
            type=type(point).__name__,
        )
    return {
        "trade_date": _encode_date(point.trade_date, field="trade_date"),
        "channel": _encode_str(point.channel, field="channel"),
        "net_buy_cny": _encode_optional_decimal(point.net_buy_cny, field="net_buy_cny"),
        "buy_cny": _encode_optional_decimal(point.buy_cny, field="buy_cny"),
        "sell_cny": _encode_optional_decimal(point.sell_cny, field="sell_cny"),
        "disclosure_note": _encode_optional_str(point.disclosure_note, field="disclosure_note"),
        "source_vendor": _encode_enum(
            point.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            point.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(point.is_authoritative, field="is_authoritative"),
    }


def _decode_northbound_point(raw: object) -> NorthboundFlowPoint:
    obj = _require_mapping(raw, field="value[]", required_keys=_NORTHBOUND_KEYS)
    return NorthboundFlowPoint(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        channel=_decode_str(obj["channel"], field="channel"),
        net_buy_cny=_decode_optional_decimal(obj["net_buy_cny"], field="net_buy_cny"),
        buy_cny=_decode_optional_decimal(obj["buy_cny"], field="buy_cny"),
        sell_cny=_decode_optional_decimal(obj["sell_cny"], field="sell_cny"),
        disclosure_note=_decode_optional_str(obj["disclosure_note"], field="disclosure_note"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_northbound(value: tuple[NorthboundFlowPoint, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_northbound_point(p) for p in value]


def _decode_northbound(raw: object) -> tuple[NorthboundFlowPoint, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_northbound_point(item) for item in raw)


def _encode_seat(seat: DragonTigerSeat) -> dict[str, object]:
    return {
        "rank": _encode_int(seat.rank, field="rank"),
        "side": _encode_str(seat.side, field="side"),
        "branch_name": _encode_str(seat.branch_name, field="branch_name"),
        "amount_cny": _encode_decimal(seat.amount_cny, field="amount_cny"),
        "is_institution": (
            None
            if seat.is_institution is None
            else _encode_bool(seat.is_institution, field="is_institution")
        ),
    }


def _decode_seat(raw: object) -> DragonTigerSeat:
    obj = _require_mapping(raw, field="seat", required_keys=_SEAT_KEYS)
    inst = obj["is_institution"]
    return DragonTigerSeat(
        rank=_decode_int(obj["rank"], field="rank"),
        side=_decode_str(obj["side"], field="side"),
        branch_name=_decode_str(obj["branch_name"], field="branch_name"),
        amount_cny=_decode_decimal(obj["amount_cny"], field="amount_cny"),
        is_institution=None if inst is None else _decode_bool(inst, field="is_institution"),
    )


def _encode_dragon(record: DragonTigerRecord) -> dict[str, object]:
    if not isinstance(record, DragonTigerRecord):
        raise _contract_error(
            "value element must be DragonTigerRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "trade_date": _encode_date(record.trade_date, field="trade_date"),
        "instrument_id": _encode_str(record.instrument_id, field="instrument_id"),
        "reason": _encode_str(record.reason, field="reason"),
        "buy_total_cny": _encode_decimal(record.buy_total_cny, field="buy_total_cny"),
        "sell_total_cny": _encode_decimal(record.sell_total_cny, field="sell_total_cny"),
        "net_buy_cny": _encode_decimal(record.net_buy_cny, field="net_buy_cny"),
        "seats": [_encode_seat(s) for s in record.seats],
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_dragon(raw: object) -> DragonTigerRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_DRAGON_KEYS)
    seats_raw = obj["seats"]
    if not isinstance(seats_raw, list):
        raise _contract_error("seats must be an array", field="seats", rule="type")
    return DragonTigerRecord(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        instrument_id=_decode_str(obj["instrument_id"], field="instrument_id"),
        reason=_decode_str(obj["reason"], field="reason"),
        buy_total_cny=_decode_decimal(obj["buy_total_cny"], field="buy_total_cny"),
        sell_total_cny=_decode_decimal(obj["sell_total_cny"], field="sell_total_cny"),
        net_buy_cny=_decode_decimal(obj["net_buy_cny"], field="net_buy_cny"),
        seats=tuple(_decode_seat(s) for s in seats_raw),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_dragons(value: tuple[DragonTigerRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_dragon(r) for r in value]


def _decode_dragons(raw: object) -> tuple[DragonTigerRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_dragon(item) for item in raw)


def _encode_margin(record: MarginRecord) -> dict[str, object]:
    if not isinstance(record, MarginRecord):
        raise _contract_error(
            "value element must be MarginRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "trade_date": _encode_date(record.trade_date, field="trade_date"),
        "financing_balance_cny": _encode_decimal(
            record.financing_balance_cny, field="financing_balance_cny"
        ),
        "financing_buy_cny": _encode_decimal(record.financing_buy_cny, field="financing_buy_cny"),
        "financing_repayment_cny": _encode_decimal(
            record.financing_repayment_cny, field="financing_repayment_cny"
        ),
        "securities_lending_balance_cny": _encode_optional_decimal(
            record.securities_lending_balance_cny,
            field="securities_lending_balance_cny",
        ),
        "securities_lending_sell_shares": _encode_optional_int(
            record.securities_lending_sell_shares,
            field="securities_lending_sell_shares",
        ),
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_margin(raw: object) -> MarginRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_MARGIN_KEYS)
    return MarginRecord(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        financing_balance_cny=_decode_decimal(
            obj["financing_balance_cny"], field="financing_balance_cny"
        ),
        financing_buy_cny=_decode_decimal(obj["financing_buy_cny"], field="financing_buy_cny"),
        financing_repayment_cny=_decode_decimal(
            obj["financing_repayment_cny"], field="financing_repayment_cny"
        ),
        securities_lending_balance_cny=_decode_optional_decimal(
            obj["securities_lending_balance_cny"],
            field="securities_lending_balance_cny",
        ),
        securities_lending_sell_shares=_decode_optional_int(
            obj["securities_lending_sell_shares"],
            field="securities_lending_sell_shares",
        ),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_margins(value: tuple[MarginRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_margin(r) for r in value]


def _decode_margins(raw: object) -> tuple[MarginRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_margin(item) for item in raw)


def _encode_block(record: BlockTradeRecord) -> dict[str, object]:
    if not isinstance(record, BlockTradeRecord):
        raise _contract_error(
            "value element must be BlockTradeRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "trade_date": _encode_date(record.trade_date, field="trade_date"),
        "price": _encode_decimal(record.price, field="price"),
        "volume_shares": _encode_int(record.volume_shares, field="volume_shares"),
        "amount_cny": _encode_decimal(record.amount_cny, field="amount_cny"),
        "premium_percent": _encode_optional_decimal(
            record.premium_percent, field="premium_percent"
        ),
        "buyer_branch": _encode_optional_str(record.buyer_branch, field="buyer_branch"),
        "seller_branch": _encode_optional_str(record.seller_branch, field="seller_branch"),
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_block(raw: object) -> BlockTradeRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_BLOCK_KEYS)
    return BlockTradeRecord(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        price=_decode_decimal(obj["price"], field="price"),
        volume_shares=_decode_int(obj["volume_shares"], field="volume_shares"),
        amount_cny=_decode_decimal(obj["amount_cny"], field="amount_cny"),
        premium_percent=_decode_optional_decimal(obj["premium_percent"], field="premium_percent"),
        buyer_branch=_decode_optional_str(obj["buyer_branch"], field="buyer_branch"),
        seller_branch=_decode_optional_str(obj["seller_branch"], field="seller_branch"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_blocks(value: tuple[BlockTradeRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_block(r) for r in value]


def _decode_blocks(raw: object) -> tuple[BlockTradeRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_block(item) for item in raw)


def _encode_holder(record: ShareholderCountRecord) -> dict[str, object]:
    if not isinstance(record, ShareholderCountRecord):
        raise _contract_error(
            "value element must be ShareholderCountRecord",
            field="value",
            rule="type",
            type=type(record).__name__,
        )
    return {
        "period_end": _encode_date(record.period_end, field="period_end"),
        "published_at": _encode_optional_datetime(record.published_at, field="published_at"),
        "shareholder_count": _encode_int(record.shareholder_count, field="shareholder_count"),
        "change_percent": _encode_optional_decimal(record.change_percent, field="change_percent"),
        "average_holding_shares": _encode_optional_decimal(
            record.average_holding_shares, field="average_holding_shares"
        ),
        "source_vendor": _encode_enum(
            record.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            record.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(record.is_authoritative, field="is_authoritative"),
    }


def _decode_holder(raw: object) -> ShareholderCountRecord:
    obj = _require_mapping(raw, field="value[]", required_keys=_HOLDER_KEYS)
    return ShareholderCountRecord(
        period_end=_decode_date(obj["period_end"], field="period_end"),
        published_at=_decode_optional_datetime(obj["published_at"], field="published_at"),
        shareholder_count=_decode_int(obj["shareholder_count"], field="shareholder_count"),
        change_percent=_decode_optional_decimal(obj["change_percent"], field="change_percent"),
        average_holding_shares=_decode_optional_decimal(
            obj["average_holding_shares"], field="average_holding_shares"
        ),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
    )


def _encode_holders(value: tuple[ShareholderCountRecord, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_holder(r) for r in value]


def _decode_holders(raw: object) -> tuple[ShareholderCountRecord, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_holder(item) for item in raw)


def _encode_chip(value: ChipDistributionSnapshot) -> dict[str, object]:
    if not isinstance(value, ChipDistributionSnapshot):
        raise _contract_error(
            "value must be ChipDistributionSnapshot",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    return {
        "as_of": _encode_datetime(value.as_of, field="as_of"),
        "bins": [
            {
                "price_low": _encode_decimal(b.price_low, field="price_low"),
                "price_high": _encode_decimal(b.price_high, field="price_high"),
                "holding_ratio": _encode_decimal(b.holding_ratio, field="holding_ratio"),
            }
            for b in value.bins
        ],
        "profit_ratio": _encode_optional_decimal(value.profit_ratio, field="profit_ratio"),
        "average_cost": _encode_optional_decimal(value.average_cost, field="average_cost"),
        "concentration_90": _encode_optional_decimal(
            value.concentration_90, field="concentration_90"
        ),
        "concentration_70": _encode_optional_decimal(
            value.concentration_70, field="concentration_70"
        ),
        "source_vendor": _encode_enum(
            value.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            value.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(value.is_authoritative, field="is_authoritative"),
        "calculation_method": _encode_str(value.calculation_method, field="calculation_method"),
        "algorithm_version": _encode_str(value.algorithm_version, field="algorithm_version"),
        "lookback_sessions": _encode_int(value.lookback_sessions, field="lookback_sessions"),
        "input_adjustment": _encode_enum(
            value.input_adjustment, field="input_adjustment", table=_ADJUSTMENT_BY_VALUE
        ),
        "bar_trade_date": _encode_date(value.bar_trade_date, field="bar_trade_date"),
    }


def _decode_chip(raw: object) -> ChipDistributionSnapshot:
    obj = _require_mapping(raw, field="value", required_keys=_CHIP_KEYS)
    bins_raw = obj["bins"]
    if not isinstance(bins_raw, list):
        raise _contract_error("bins must be an array", field="bins", rule="type")
    bins: list[ChipDistributionBin] = []
    for item in bins_raw:
        b = _require_mapping(item, field="bin", required_keys=_BIN_KEYS)
        bins.append(
            ChipDistributionBin(
                price_low=_decode_decimal(b["price_low"], field="price_low"),
                price_high=_decode_decimal(b["price_high"], field="price_high"),
                holding_ratio=_decode_decimal(b["holding_ratio"], field="holding_ratio"),
            )
        )
    return ChipDistributionSnapshot(
        as_of=_decode_datetime(obj["as_of"], field="as_of"),
        bins=tuple(bins),
        profit_ratio=_decode_optional_decimal(obj["profit_ratio"], field="profit_ratio"),
        average_cost=_decode_optional_decimal(obj["average_cost"], field="average_cost"),
        concentration_90=_decode_optional_decimal(
            obj["concentration_90"], field="concentration_90"
        ),
        concentration_70=_decode_optional_decimal(
            obj["concentration_70"], field="concentration_70"
        ),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
        calculation_method=_decode_str(obj["calculation_method"], field="calculation_method"),
        algorithm_version=_decode_str(obj["algorithm_version"], field="algorithm_version"),
        lookback_sessions=_decode_int(obj["lookback_sessions"], field="lookback_sessions"),
        input_adjustment=_decode_enum(
            obj["input_adjustment"], field="input_adjustment", table=_ADJUSTMENT_BY_VALUE
        ),
        bar_trade_date=_decode_date(obj["bar_trade_date"], field="bar_trade_date"),
    )


def intraday_flow_codec() -> AShareProviderCacheCodec[tuple[FundFlowPoint, ...]]:
    return AShareProviderCacheCodec(
        CODEC_INTRADAY_FLOW,
        _encode_fund_flow,
        _decode_fund_flow,
        expected_category=DataCategory.CAPITAL,
    )


def daily_flow_codec() -> AShareProviderCacheCodec[tuple[FundFlowPoint, ...]]:
    return AShareProviderCacheCodec(
        CODEC_DAILY_FLOW,
        _encode_fund_flow,
        _decode_fund_flow,
        expected_category=DataCategory.CAPITAL,
    )


def northbound_codec() -> AShareProviderCacheCodec[tuple[NorthboundFlowPoint, ...]]:
    return AShareProviderCacheCodec(
        CODEC_NORTHBOUND,
        _encode_northbound,
        _decode_northbound,
        expected_category=DataCategory.CAPITAL,
    )


def dragon_tiger_codec() -> AShareProviderCacheCodec[tuple[DragonTigerRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_DRAGON_TIGER,
        _encode_dragons,
        _decode_dragons,
        expected_category=DataCategory.CAPITAL,
    )


def margin_codec() -> AShareProviderCacheCodec[tuple[MarginRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_MARGIN,
        _encode_margins,
        _decode_margins,
        expected_category=DataCategory.CAPITAL,
    )


def block_trades_codec() -> AShareProviderCacheCodec[tuple[BlockTradeRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_BLOCK_TRADES,
        _encode_blocks,
        _decode_blocks,
        expected_category=DataCategory.CAPITAL,
    )


def shareholder_counts_codec() -> AShareProviderCacheCodec[tuple[ShareholderCountRecord, ...]]:
    return AShareProviderCacheCodec(
        CODEC_SHAREHOLDER_COUNTS,
        _encode_holders,
        _decode_holders,
        expected_category=DataCategory.CAPITAL,
    )


def chip_distribution_codec() -> AShareProviderCacheCodec[ChipDistributionSnapshot]:
    return AShareProviderCacheCodec(
        CODEC_CHIP_DISTRIBUTION,
        _encode_chip,
        _decode_chip,
        expected_category=DataCategory.CAPITAL,
    )


E4A_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_INTRADAY_FLOW: intraday_flow_codec,  # type: ignore[dict-item]
    CODEC_DAILY_FLOW: daily_flow_codec,  # type: ignore[dict-item]
    CODEC_NORTHBOUND: northbound_codec,  # type: ignore[dict-item]
    CODEC_DRAGON_TIGER: dragon_tiger_codec,  # type: ignore[dict-item]
    CODEC_MARGIN: margin_codec,  # type: ignore[dict-item]
    CODEC_BLOCK_TRADES: block_trades_codec,  # type: ignore[dict-item]
    CODEC_SHAREHOLDER_COUNTS: shareholder_counts_codec,  # type: ignore[dict-item]
    CODEC_CHIP_DISTRIBUTION: chip_distribution_codec,  # type: ignore[dict-item]
}


# ---------------------------------------------------------------------------
# E4b limit / sentiment / interactive QA codecs (§18.3)
# ---------------------------------------------------------------------------

from domain.a_share.enums import LimitPoolType, SentimentSourceType  # noqa: E402
from domain.a_share.models import (  # noqa: E402
    InteractiveQAItem,
    LimitPoolEntry,
    LimitUpContext,
    LimitUpLadderRung,
    SentimentSignal,
)

CODEC_LIMIT_CONTEXT: Final[str] = "a_share_limit_context.v1"
CODEC_SENTIMENT: Final[str] = "a_share_sentiment.v2"
CODEC_INTERACTIVE_QA: Final[str] = "a_share_interactive_qa.v1"

E4B_CODEC_IDS: Final[frozenset[str]] = frozenset(
    {
        CODEC_LIMIT_CONTEXT,
        CODEC_SENTIMENT,
        CODEC_INTERACTIVE_QA,
    }
)

_LIMIT_POOL_TYPE_BY_VALUE: Final[Mapping[str, LimitPoolType]] = {m.value: m for m in LimitPoolType}
_SENTIMENT_SOURCE_BY_VALUE: Final[Mapping[str, SentimentSourceType]] = {
    m.value: m for m in SentimentSourceType
}

_LIMIT_ENTRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "pool_type",
        "trade_date",
        "instrument_id",
        "name",
        "last",
        "change_percent",
        "consecutive_limit_count",
        "days_and_boards",
        "first_seal_at",
        "last_seal_at",
        "seal_amount_cny",
        "broken_count",
        "industry",
        "reason_tags",
        "source_vendor",
        "reliability",
    }
)
_LADDER_KEYS: Final[frozenset[str]] = frozenset(
    {"consecutive_limit_count", "instrument_count", "instrument_ids"}
)
_LIMIT_CONTEXT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "trade_date",
        "entries",
        "limit_up_count",
        "limit_down_count",
        "broken_limit_count",
        "broken_rate",
        "max_consecutive_count",
        "promotion_rate",
        "ladder",
    }
)
_SENTIMENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source_type",
        "trade_date",
        "instrument_id",
        "rank",
        "rank_change",
        "heat_value",
        "concept_tags",
        "label",
        "source_vendor",
        "reliability",
        "is_authoritative",
        "source_item_id",
        "observed_at",
    }
)
_QA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "qa_key",
        "question",
        "asked_at",
        "answer",
        "answered_at",
        "source_url",
    }
)


def _encode_str_tuple(values: tuple[str, ...], *, field: str) -> list[str]:
    if not isinstance(values, tuple):
        raise _contract_error(f"{field} must be a tuple", field=field, rule="type")
    out: list[str] = []
    for idx, item in enumerate(values):
        out.append(_encode_str(item, field=f"{field}[{idx}]"))
    return out


def _decode_str_tuple(raw: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _contract_error(f"{field} must be an array", field=field, rule="type")
    return tuple(_decode_str(item, field=f"{field}[{idx}]") for idx, item in enumerate(raw))


def _encode_limit_entry(entry: LimitPoolEntry) -> dict[str, object]:
    if not isinstance(entry, LimitPoolEntry):
        raise _contract_error(
            "value element must be LimitPoolEntry",
            field="value",
            rule="type",
            type=type(entry).__name__,
        )
    return {
        "pool_type": _encode_enum(
            entry.pool_type, field="pool_type", table=_LIMIT_POOL_TYPE_BY_VALUE
        ),
        "trade_date": _encode_date(entry.trade_date, field="trade_date"),
        "instrument_id": _encode_str(entry.instrument_id, field="instrument_id"),
        "name": _encode_str(entry.name, field="name"),
        "last": _encode_decimal(entry.last, field="last"),
        "change_percent": _encode_decimal(entry.change_percent, field="change_percent"),
        "consecutive_limit_count": _encode_optional_int(
            entry.consecutive_limit_count, field="consecutive_limit_count"
        ),
        "days_and_boards": _encode_optional_str(entry.days_and_boards, field="days_and_boards"),
        "first_seal_at": _encode_optional_datetime(entry.first_seal_at, field="first_seal_at"),
        "last_seal_at": _encode_optional_datetime(entry.last_seal_at, field="last_seal_at"),
        "seal_amount_cny": _encode_optional_decimal(entry.seal_amount_cny, field="seal_amount_cny"),
        "broken_count": _encode_optional_int(entry.broken_count, field="broken_count"),
        "industry": _encode_optional_str(entry.industry, field="industry"),
        "reason_tags": _encode_str_tuple(entry.reason_tags, field="reason_tags"),
        "source_vendor": _encode_enum(
            entry.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            entry.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
    }


def _decode_limit_entry(raw: object) -> LimitPoolEntry:
    obj = _require_mapping(raw, field="value[]", required_keys=_LIMIT_ENTRY_KEYS)
    return LimitPoolEntry(
        pool_type=_decode_enum(
            obj["pool_type"], field="pool_type", table=_LIMIT_POOL_TYPE_BY_VALUE
        ),
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        instrument_id=_decode_str(obj["instrument_id"], field="instrument_id"),
        name=_decode_str(obj["name"], field="name"),
        last=_decode_decimal(obj["last"], field="last"),
        change_percent=_decode_decimal(obj["change_percent"], field="change_percent"),
        consecutive_limit_count=_decode_optional_int(
            obj["consecutive_limit_count"], field="consecutive_limit_count"
        ),
        days_and_boards=_decode_optional_str(obj["days_and_boards"], field="days_and_boards"),
        first_seal_at=_decode_optional_datetime(obj["first_seal_at"], field="first_seal_at"),
        last_seal_at=_decode_optional_datetime(obj["last_seal_at"], field="last_seal_at"),
        seal_amount_cny=_decode_optional_decimal(obj["seal_amount_cny"], field="seal_amount_cny"),
        broken_count=_decode_optional_int(obj["broken_count"], field="broken_count"),
        industry=_decode_optional_str(obj["industry"], field="industry"),
        reason_tags=_decode_str_tuple(obj["reason_tags"], field="reason_tags"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
    )


def _encode_ladder(rung: LimitUpLadderRung) -> dict[str, object]:
    if not isinstance(rung, LimitUpLadderRung):
        raise _contract_error(
            "ladder element must be LimitUpLadderRung",
            field="ladder",
            rule="type",
            type=type(rung).__name__,
        )
    return {
        "consecutive_limit_count": _encode_int(
            rung.consecutive_limit_count, field="consecutive_limit_count"
        ),
        "instrument_count": _encode_int(rung.instrument_count, field="instrument_count"),
        "instrument_ids": _encode_str_tuple(rung.instrument_ids, field="instrument_ids"),
    }


def _decode_ladder(raw: object) -> LimitUpLadderRung:
    obj = _require_mapping(raw, field="ladder[]", required_keys=_LADDER_KEYS)
    return LimitUpLadderRung(
        consecutive_limit_count=_decode_int(
            obj["consecutive_limit_count"], field="consecutive_limit_count"
        ),
        instrument_count=_decode_int(obj["instrument_count"], field="instrument_count"),
        instrument_ids=_decode_str_tuple(obj["instrument_ids"], field="instrument_ids"),
    )


def _encode_limit_context(value: LimitUpContext) -> dict[str, object]:
    if not isinstance(value, LimitUpContext):
        raise _contract_error(
            "value must be LimitUpContext",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    return {
        "trade_date": _encode_date(value.trade_date, field="trade_date"),
        "entries": [_encode_limit_entry(e) for e in value.entries],
        "limit_up_count": _encode_int(value.limit_up_count, field="limit_up_count"),
        "limit_down_count": _encode_int(value.limit_down_count, field="limit_down_count"),
        "broken_limit_count": _encode_int(value.broken_limit_count, field="broken_limit_count"),
        "broken_rate": _encode_optional_decimal(value.broken_rate, field="broken_rate"),
        "max_consecutive_count": _encode_optional_int(
            value.max_consecutive_count, field="max_consecutive_count"
        ),
        "promotion_rate": _encode_optional_decimal(value.promotion_rate, field="promotion_rate"),
        "ladder": [_encode_ladder(r) for r in value.ladder],
    }


def _decode_limit_context(raw: object) -> LimitUpContext:
    obj = _require_mapping(raw, field="value", required_keys=_LIMIT_CONTEXT_KEYS)
    entries_raw = obj["entries"]
    ladder_raw = obj["ladder"]
    if not isinstance(entries_raw, list):
        raise _contract_error("entries must be an array", field="entries", rule="type")
    if not isinstance(ladder_raw, list):
        raise _contract_error("ladder must be an array", field="ladder", rule="type")
    return LimitUpContext(
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        entries=tuple(_decode_limit_entry(e) for e in entries_raw),
        limit_up_count=_decode_int(obj["limit_up_count"], field="limit_up_count"),
        limit_down_count=_decode_int(obj["limit_down_count"], field="limit_down_count"),
        broken_limit_count=_decode_int(obj["broken_limit_count"], field="broken_limit_count"),
        broken_rate=_decode_optional_decimal(obj["broken_rate"], field="broken_rate"),
        max_consecutive_count=_decode_optional_int(
            obj["max_consecutive_count"], field="max_consecutive_count"
        ),
        promotion_rate=_decode_optional_decimal(obj["promotion_rate"], field="promotion_rate"),
        ladder=tuple(_decode_ladder(r) for r in ladder_raw),
    )


def _encode_sentiment_signal(signal: SentimentSignal) -> dict[str, object]:
    if not isinstance(signal, SentimentSignal):
        raise _contract_error(
            "value element must be SentimentSignal",
            field="value",
            rule="type",
            type=type(signal).__name__,
        )
    return {
        "source_type": _encode_enum(
            signal.source_type, field="source_type", table=_SENTIMENT_SOURCE_BY_VALUE
        ),
        "trade_date": _encode_date(signal.trade_date, field="trade_date"),
        "instrument_id": _encode_optional_str(signal.instrument_id, field="instrument_id"),
        "rank": _encode_optional_int(signal.rank, field="rank"),
        "rank_change": _encode_optional_int(signal.rank_change, field="rank_change"),
        "heat_value": _encode_optional_decimal(signal.heat_value, field="heat_value"),
        "concept_tags": _encode_str_tuple(signal.concept_tags, field="concept_tags"),
        "label": _encode_optional_str(signal.label, field="label"),
        "source_vendor": _encode_enum(
            signal.source_vendor, field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        "reliability": _encode_enum(
            signal.reliability, field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        "is_authoritative": _encode_bool(signal.is_authoritative, field="is_authoritative"),
        "source_item_id": _encode_optional_str(signal.source_item_id, field="source_item_id"),
        "observed_at": _encode_optional_datetime(signal.observed_at, field="observed_at"),
    }


def _decode_sentiment_signal(raw: object) -> SentimentSignal:
    obj = _require_mapping(raw, field="value[]", required_keys=_SENTIMENT_KEYS)
    return SentimentSignal(
        source_type=_decode_enum(
            obj["source_type"], field="source_type", table=_SENTIMENT_SOURCE_BY_VALUE
        ),
        trade_date=_decode_date(obj["trade_date"], field="trade_date"),
        instrument_id=_decode_optional_str(obj["instrument_id"], field="instrument_id"),
        rank=_decode_optional_int(obj["rank"], field="rank"),
        rank_change=_decode_optional_int(obj["rank_change"], field="rank_change"),
        heat_value=_decode_optional_decimal(obj["heat_value"], field="heat_value"),
        concept_tags=_decode_str_tuple(obj["concept_tags"], field="concept_tags"),
        label=_decode_optional_str(obj["label"], field="label"),
        source_vendor=_decode_enum(
            obj["source_vendor"], field="source_vendor", table=_VENDOR_BY_VALUE
        ),
        reliability=_decode_enum(
            obj["reliability"], field="reliability", table=_RELIABILITY_BY_VALUE
        ),
        is_authoritative=_decode_bool(obj["is_authoritative"], field="is_authoritative"),
        source_item_id=_decode_optional_str(obj["source_item_id"], field="source_item_id"),
        observed_at=_decode_optional_datetime(obj["observed_at"], field="observed_at"),
    )


def _encode_sentiment(value: tuple[SentimentSignal, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_sentiment_signal(s) for s in value]


def _decode_sentiment(raw: object) -> tuple[SentimentSignal, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_sentiment_signal(item) for item in raw)


def _encode_qa(item: InteractiveQAItem) -> dict[str, object]:
    if not isinstance(item, InteractiveQAItem):
        raise _contract_error(
            "value element must be InteractiveQAItem",
            field="value",
            rule="type",
            type=type(item).__name__,
        )
    return {
        "qa_key": _encode_str(item.qa_key, field="qa_key"),
        "question": _encode_str(item.question, field="question"),
        "asked_at": _encode_optional_datetime(item.asked_at, field="asked_at"),
        "answer": _encode_str(item.answer, field="answer"),
        "answered_at": _encode_datetime(item.answered_at, field="answered_at"),
        "source_url": _encode_optional_str(item.source_url, field="source_url"),
    }


def _decode_qa(raw: object) -> InteractiveQAItem:
    obj = _require_mapping(raw, field="value[]", required_keys=_QA_KEYS)
    return InteractiveQAItem(
        qa_key=_decode_str(obj["qa_key"], field="qa_key"),
        question=_decode_str(obj["question"], field="question"),
        asked_at=_decode_optional_datetime(obj["asked_at"], field="asked_at"),
        answer=_decode_str(obj["answer"], field="answer"),
        answered_at=_decode_datetime(obj["answered_at"], field="answered_at"),
        source_url=_decode_optional_str(obj["source_url"], field="source_url"),
    )


def _encode_qa_tuple(value: tuple[InteractiveQAItem, ...]) -> list[object]:
    if not isinstance(value, tuple):
        raise _contract_error("value must be a tuple", field="value", rule="type")
    return [_encode_qa(item) for item in value]


def _decode_qa_tuple(raw: object) -> tuple[InteractiveQAItem, ...]:
    if not isinstance(raw, list):
        raise _contract_error("value must be an array", field="value", rule="type")
    return tuple(_decode_qa(item) for item in raw)


def limit_context_codec() -> AShareProviderCacheCodec[LimitUpContext]:
    return AShareProviderCacheCodec(
        CODEC_LIMIT_CONTEXT,
        _encode_limit_context,
        _decode_limit_context,
        expected_category=DataCategory.LIMIT_UP,
    )


def sentiment_codec() -> AShareProviderCacheCodec[tuple[SentimentSignal, ...]]:
    return AShareProviderCacheCodec(
        CODEC_SENTIMENT,
        _encode_sentiment,
        _decode_sentiment,
        expected_category=DataCategory.SENTIMENT,
    )


def interactive_qa_codec() -> AShareProviderCacheCodec[tuple[InteractiveQAItem, ...]]:
    return AShareProviderCacheCodec(
        CODEC_INTERACTIVE_QA,
        _encode_qa_tuple,
        _decode_qa_tuple,
        expected_category=DataCategory.INTERACTIVE_QA,
    )


E4B_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_LIMIT_CONTEXT: limit_context_codec,  # type: ignore[dict-item]
    CODEC_SENTIMENT: sentiment_codec,  # type: ignore[dict-item]
    CODEC_INTERACTIVE_QA: interactive_qa_codec,  # type: ignore[dict-item]
}


# ---------------------------------------------------------------------------
# E4c ETF option snapshot codec (§18.3)
# ---------------------------------------------------------------------------

from domain.a_share.enums import OptionType  # noqa: E402
from domain.a_share.models import (  # noqa: E402
    EtfOptionContract,
    EtfOptionQuote,
    EtfOptionSnapshot,
    OptionGreeks,
)

CODEC_OPTION_SNAPSHOT: Final[str] = "a_share_option_snapshot.v1"

E4C_CODEC_IDS: Final[frozenset[str]] = frozenset({CODEC_OPTION_SNAPSHOT})

_OPTION_TYPE_BY_VALUE: Final[Mapping[str, OptionType]] = {m.value: m for m in OptionType}

_OPTION_CONTRACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "instrument_id",
        "underlying_instrument_id",
        "option_type",
        "expiry",
        "strike",
        "multiplier",
    }
)
_OPTION_QUOTE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contract",
        "quote_at",
        "last",
        "bid_prices",
        "bid_volumes",
        "ask_prices",
        "ask_volumes",
        "volume_contracts",
        "open_interest",
    }
)
_OPTION_GREEKS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contract_instrument_id",
        "as_of",
        "delta",
        "gamma",
        "theta",
        "vega",
        "implied_volatility",
        "theoretical_value",
        "source_provided",
    }
)
_OPTION_SNAPSHOT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "underlying_instrument_id",
        "expiry",
        "quotes",
        "greeks",
    }
)


def _encode_decimal_tuple(values: tuple[Decimal, ...], *, field: str) -> list[str]:
    if not isinstance(values, tuple):
        raise _contract_error(f"{field} must be a tuple", field=field, rule="type")
    return [_encode_decimal(v, field=f"{field}[{i}]") for i, v in enumerate(values)]


def _decode_decimal_tuple(raw: object, *, field: str) -> tuple[Decimal, ...]:
    if not isinstance(raw, list):
        raise _contract_error(f"{field} must be an array", field=field, rule="type")
    return tuple(_decode_decimal(item, field=f"{field}[{idx}]") for idx, item in enumerate(raw))


def _encode_int_tuple(values: tuple[int, ...], *, field: str) -> list[int]:
    if not isinstance(values, tuple):
        raise _contract_error(f"{field} must be a tuple", field=field, rule="type")
    out: list[int] = []
    for idx, item in enumerate(values):
        if type(item) is not int or isinstance(item, bool):
            raise _contract_error(
                f"{field}[{idx}] must be int",
                field=f"{field}[{idx}]",
                rule="int_type",
            )
        out.append(item)
    return out


def _decode_int_tuple(raw: object, *, field: str) -> tuple[int, ...]:
    if not isinstance(raw, list):
        raise _contract_error(f"{field} must be an array", field=field, rule="type")
    out: list[int] = []
    for idx, item in enumerate(raw):
        if type(item) is not int or isinstance(item, bool):
            raise _contract_error(
                f"{field}[{idx}] must be int",
                field=f"{field}[{idx}]",
                rule="int_type",
            )
        out.append(item)
    return tuple(out)


def _encode_contract(contract: EtfOptionContract) -> dict[str, object]:
    if not isinstance(contract, EtfOptionContract):
        raise _contract_error(
            "contract must be EtfOptionContract",
            field="contract",
            rule="type",
            type=type(contract).__name__,
        )
    return {
        "instrument_id": _encode_str(contract.instrument_id, field="instrument_id"),
        "underlying_instrument_id": _encode_str(
            contract.underlying_instrument_id, field="underlying_instrument_id"
        ),
        "option_type": _encode_enum(
            contract.option_type, field="option_type", table=_OPTION_TYPE_BY_VALUE
        ),
        "expiry": _encode_date(contract.expiry, field="expiry"),
        "strike": _encode_decimal(contract.strike, field="strike"),
        "multiplier": _encode_optional_decimal(contract.multiplier, field="multiplier"),
    }


def _decode_contract(raw: object) -> EtfOptionContract:
    obj = _require_mapping(raw, field="contract", required_keys=_OPTION_CONTRACT_KEYS)
    return EtfOptionContract(
        instrument_id=_decode_str(obj["instrument_id"], field="instrument_id"),
        underlying_instrument_id=_decode_str(
            obj["underlying_instrument_id"], field="underlying_instrument_id"
        ),
        option_type=_decode_enum(
            obj["option_type"], field="option_type", table=_OPTION_TYPE_BY_VALUE
        ),
        expiry=_decode_date(obj["expiry"], field="expiry"),
        strike=_decode_decimal(obj["strike"], field="strike"),
        multiplier=_decode_optional_decimal(obj["multiplier"], field="multiplier"),
    )


def _encode_option_quote(quote: EtfOptionQuote) -> dict[str, object]:
    if not isinstance(quote, EtfOptionQuote):
        raise _contract_error(
            "quotes element must be EtfOptionQuote",
            field="quotes",
            rule="type",
            type=type(quote).__name__,
        )
    return {
        "contract": _encode_contract(quote.contract),
        "quote_at": _encode_datetime(quote.quote_at, field="quote_at"),
        "last": _encode_optional_decimal(quote.last, field="last"),
        "bid_prices": _encode_decimal_tuple(quote.bid_prices, field="bid_prices"),
        "bid_volumes": _encode_int_tuple(quote.bid_volumes, field="bid_volumes"),
        "ask_prices": _encode_decimal_tuple(quote.ask_prices, field="ask_prices"),
        "ask_volumes": _encode_int_tuple(quote.ask_volumes, field="ask_volumes"),
        "volume_contracts": _encode_optional_int(quote.volume_contracts, field="volume_contracts"),
        "open_interest": _encode_optional_int(quote.open_interest, field="open_interest"),
    }


def _decode_option_quote(raw: object) -> EtfOptionQuote:
    obj = _require_mapping(raw, field="quotes[]", required_keys=_OPTION_QUOTE_KEYS)
    return EtfOptionQuote(
        contract=_decode_contract(obj["contract"]),
        quote_at=_decode_datetime(obj["quote_at"], field="quote_at"),
        last=_decode_optional_decimal(obj["last"], field="last"),
        bid_prices=_decode_decimal_tuple(obj["bid_prices"], field="bid_prices"),
        bid_volumes=_decode_int_tuple(obj["bid_volumes"], field="bid_volumes"),
        ask_prices=_decode_decimal_tuple(obj["ask_prices"], field="ask_prices"),
        ask_volumes=_decode_int_tuple(obj["ask_volumes"], field="ask_volumes"),
        volume_contracts=_decode_optional_int(obj["volume_contracts"], field="volume_contracts"),
        open_interest=_decode_optional_int(obj["open_interest"], field="open_interest"),
    )


def _encode_greeks(greeks: OptionGreeks) -> dict[str, object]:
    if not isinstance(greeks, OptionGreeks):
        raise _contract_error(
            "greeks element must be OptionGreeks",
            field="greeks",
            rule="type",
            type=type(greeks).__name__,
        )
    return {
        "contract_instrument_id": _encode_str(
            greeks.contract_instrument_id, field="contract_instrument_id"
        ),
        "as_of": _encode_datetime(greeks.as_of, field="as_of"),
        "delta": _encode_optional_decimal(greeks.delta, field="delta"),
        "gamma": _encode_optional_decimal(greeks.gamma, field="gamma"),
        "theta": _encode_optional_decimal(greeks.theta, field="theta"),
        "vega": _encode_optional_decimal(greeks.vega, field="vega"),
        "implied_volatility": _encode_optional_decimal(
            greeks.implied_volatility, field="implied_volatility"
        ),
        "theoretical_value": _encode_optional_decimal(
            greeks.theoretical_value, field="theoretical_value"
        ),
        "source_provided": _encode_bool(greeks.source_provided, field="source_provided"),
    }


def _decode_greeks(raw: object) -> OptionGreeks:
    obj = _require_mapping(raw, field="greeks[]", required_keys=_OPTION_GREEKS_KEYS)
    return OptionGreeks(
        contract_instrument_id=_decode_str(
            obj["contract_instrument_id"], field="contract_instrument_id"
        ),
        as_of=_decode_datetime(obj["as_of"], field="as_of"),
        delta=_decode_optional_decimal(obj["delta"], field="delta"),
        gamma=_decode_optional_decimal(obj["gamma"], field="gamma"),
        theta=_decode_optional_decimal(obj["theta"], field="theta"),
        vega=_decode_optional_decimal(obj["vega"], field="vega"),
        implied_volatility=_decode_optional_decimal(
            obj["implied_volatility"], field="implied_volatility"
        ),
        theoretical_value=_decode_optional_decimal(
            obj["theoretical_value"], field="theoretical_value"
        ),
        source_provided=_decode_bool(obj["source_provided"], field="source_provided"),
    )


def _encode_option_snapshot(value: EtfOptionSnapshot) -> dict[str, object]:
    if not isinstance(value, EtfOptionSnapshot):
        raise _contract_error(
            "value must be EtfOptionSnapshot",
            field="value",
            rule="type",
            type=type(value).__name__,
        )
    return {
        "underlying_instrument_id": _encode_str(
            value.underlying_instrument_id, field="underlying_instrument_id"
        ),
        "expiry": (None if value.expiry is None else _encode_date(value.expiry, field="expiry")),
        "quotes": [_encode_option_quote(q) for q in value.quotes],
        "greeks": [_encode_greeks(g) for g in value.greeks],
    }


def _decode_option_snapshot(raw: object) -> EtfOptionSnapshot:
    obj = _require_mapping(raw, field="value", required_keys=_OPTION_SNAPSHOT_KEYS)
    quotes_raw = obj["quotes"]
    greeks_raw = obj["greeks"]
    if not isinstance(quotes_raw, list):
        raise _contract_error("quotes must be an array", field="quotes", rule="type")
    if not isinstance(greeks_raw, list):
        raise _contract_error("greeks must be an array", field="greeks", rule="type")
    expiry_raw = obj["expiry"]
    expiry = None if expiry_raw is None else _decode_date(expiry_raw, field="expiry")
    return EtfOptionSnapshot(
        underlying_instrument_id=_decode_str(
            obj["underlying_instrument_id"], field="underlying_instrument_id"
        ),
        expiry=expiry,
        quotes=tuple(_decode_option_quote(q) for q in quotes_raw),
        greeks=tuple(_decode_greeks(g) for g in greeks_raw),
    )


def option_snapshot_codec() -> AShareProviderCacheCodec[EtfOptionSnapshot]:
    return AShareProviderCacheCodec(
        CODEC_OPTION_SNAPSHOT,
        _encode_option_snapshot,
        _decode_option_snapshot,
        expected_category=DataCategory.OPTIONS,
    )


E4C_CODECS: Final[
    Mapping[
        str,
        Callable[[], AShareProviderCacheCodec[object]],
    ]
] = {
    CODEC_OPTION_SNAPSHOT: option_snapshot_codec,  # type: ignore[dict-item]
}
