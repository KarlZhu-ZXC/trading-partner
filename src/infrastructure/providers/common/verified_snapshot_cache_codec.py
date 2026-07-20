"""VerifiedMarketSnapshot provider cache codec (Phase 1D D6b1).

Canonical JSON envelope for MARKET_SNAPSHOT cache payloads. Manual strict
parsing only — never surface JSON/Pydantic/enum/decimal exception chains or
echo payload values into DataContractError.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.market.models import (
    MarketBar,
    TechnicalIndicators,
    VerifiedMarketSnapshot,
)
from infrastructure.providers.common.contract_validation import (
    validate_verified_market_snapshot,
)

# Encoder fixed-point form only: optional leading '-', integer without leading
# zeros (except bare 0), optional fractional digits. Rejects exponent, leading
# '+', leading zeros, bare '.5' / '1.', and any whitespace.
_CANONICAL_DECIMAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
)


class _StrictJsonError(Exception):
    """Internal strict-JSON failure; never surface type or message publicly."""


CODEC_ID: Final[str] = "verified_market_snapshot.v1"
_SCHEMA_VERSION: Final[int] = 1

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {"codec", "meta", "schema_version", "value"}
)
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
_VALUE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "instrument",
        "requested_as_of",
        "latest_market_row",
        "indicators",
        "recent_closes",
        "adjustment",
        "session",
        "algorithm_version",
    }
)
_INSTRUMENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "instrument_id",
        "symbol",
        "name",
        "market",
        "exchange",
        "currency",
        "timezone",
        "asset_type",
        "is_active",
        "listing_status",
        "country",
        "mic",
        "underlying_instrument_id",
        "multiplier",
        "tick_size",
        "lot_size",
        "metadata_version",
    }
)
_BAR_KEYS: Final[frozenset[str]] = frozenset(
    {"timestamp", "open", "high", "low", "close", "volume"}
)
_INDICATOR_KEYS: Final[frozenset[str]] = frozenset(
    {
        "ema_10",
        "sma_50",
        "sma_200",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_histogram",
        "atr_14",
        "bollinger_mid",
        "bollinger_upper",
        "bollinger_lower",
        "vwma",
        "mfi",
    }
)

# Strict wire maps — never Enum(raw) constructor chains that can leak values.
_VENDOR_BY_VALUE: Final[Mapping[str, VendorId]] = {m.value: m for m in VendorId}
_CATEGORY_BY_VALUE: Final[Mapping[str, DataCategory]] = {
    m.value: m for m in DataCategory
}
_ROLE_BY_VALUE: Final[Mapping[str, SourceRole]] = {m.value: m for m in SourceRole}
_FRESHNESS_BY_VALUE: Final[Mapping[str, Freshness]] = {m.value: m for m in Freshness}
_SESSION_BY_VALUE: Final[Mapping[str, TradingSession]] = {
    m.value: m for m in TradingSession
}
_CACHE_DISP_BY_VALUE: Final[Mapping[str, CacheDisposition]] = {
    m.value: m for m in CacheDisposition
}
_ADJUSTMENT_BY_VALUE: Final[Mapping[str, AdjustmentMethod]] = {
    m.value: m for m in AdjustmentMethod
}
_MARKET_BY_VALUE: Final[Mapping[str, Market]] = {m.value: m for m in Market}
_ASSET_TYPE_BY_VALUE: Final[Mapping[str, AssetType]] = {
    m.value: m for m in AssetType
}


def _contract_error(
    message: str,
    *,
    field: str,
    rule: str,
    **extra: object,
) -> DataContractError:
    details: dict[str, object] = {"field": field, "rule": rule}
    for key, value in extra.items():
        details[key] = value
    return DataContractError(message, details=details)


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
    # JSON object keys are always str; reject non-str keys without echoing them.
    if any(not isinstance(k, str) for k in value):
        raise _contract_error(
            f"{field} keys must be strings",
            field=field,
            rule="key_type",
        )
    keys = frozenset(value)
    if keys != required_keys:
        if not keys <= required_keys:
            raise _contract_error(
                f"{field} contains unknown keys",
                field=field,
                rule="extra_keys",
            )
        raise _contract_error(
            f"{field} is missing required keys",
            field=field,
            rule="missing_keys",
        )
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
            f"{field} must be a finite Decimal",
            field=field,
            rule="finite_decimal",
        )
    raw = str(value)
    encoded = format(value, "f") if "E" in raw.upper() else raw
    # Encoder output is always grammar-canonical (including -0 / trailing zeros).
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
    # JSON numbers are rejected (int/float); only encoder canonical strings.
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
            f"{field} must be a finite Decimal",
            field=field,
            rule="finite_decimal",
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
        raise _contract_error(
            f"{field} must be timezone-aware",
            field=field,
            rule="timezone_aware",
        )
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
        raise _contract_error(
            f"{field} must be timezone-aware",
            field=field,
            rule="timezone_aware",
        )
    # Canonical form: re-encoding must equal the stored string exactly.
    if parsed.isoformat() != value:
        raise _contract_error(
            f"{field} must be canonical ISO 8601 (isoformat round-trip)",
            field=field,
            rule="canonical_isoformat",
        )
    return parsed


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


def _encode_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise _contract_error(
            f"{field} must be a bool",
            field=field,
            rule="bool_type",
            type=type(value).__name__,
        )
    return value


def _encode_positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _contract_error(
            f"{field} must be a positive int",
            field=field,
            rule="int_type",
            type=type(value).__name__,
        )
    if value < 1:
        raise _contract_error(
            f"{field} must be a positive int",
            field=field,
            rule="positive",
        )
    return value


def _encode_optional_nonnegative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise _contract_error(
            f"{field} must be None or a non-negative int",
            field=field,
            rule="int_type",
            type=type(value).__name__,
        )
    if value < 0:
        raise _contract_error(
            f"{field} must be nonnegative",
            field=field,
            rule="nonnegative",
        )
    return value


def _encode_warning_codes(warnings: object, *, field: str) -> list[str]:
    if not isinstance(warnings, Sequence) or isinstance(warnings, (str, bytes)):
        raise _contract_error(
            f"{field} must be a sequence of strings",
            field=field,
            rule="type",
            type=type(warnings).__name__,
        )
    encoded: list[str] = []
    for idx, code in enumerate(warnings):
        if not isinstance(code, str):
            raise _contract_error(
                f"{field} elements must be strings",
                field=field,
                rule="element_type",
                index=idx,
            )
        encoded.append(code)
    return encoded


def _encode_enum[E](
    value: object,
    *,
    field: str,
    table: Mapping[str, E],
) -> str:
    """Encode only exact frozen enum members present in ``table`` (identity).

    Arbitrary objects with a ``.value`` attribute are rejected even when the
    string matches a known wire value.
    """
    for member in table.values():
        if value is member:
            wire = getattr(member, "value", None)
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


def _encode_optional_enum[E](
    value: object,
    *,
    field: str,
    table: Mapping[str, E],
) -> str | None:
    if value is None:
        return None
    return _encode_enum(value, field=field, table=table)


def _decode_enum[E](
    value: object,
    *,
    field: str,
    table: Mapping[str, E],
) -> E:
    if not isinstance(value, str):
        raise _contract_error(
            f"{field} must be a string enum value",
            field=field,
            rule="enum_type",
            type=type(value).__name__,
        )
    mapped = table.get(value)
    if mapped is None:
        # Never echo the rejected wire value (may be malicious free text).
        raise _contract_error(
            f"{field} is not a known enum value",
            field=field,
            rule="enum_value",
        )
    return mapped


def _decode_optional_enum[E](
    value: object,
    *,
    field: str,
    table: Mapping[str, E],
) -> E | None:
    if value is None:
        return None
    return _decode_enum(value, field=field, table=table)


def _decode_optional_nonnegative_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise _contract_error(
            f"{field} must be None or a non-negative int",
            field=field,
            rule="int_type",
            type=type(value).__name__,
        )
    if value < 0:
        raise _contract_error(
            f"{field} must be nonnegative",
            field=field,
            rule="nonnegative",
        )
    return value


def _decode_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise _contract_error(
            f"{field} must be a bool",
            field=field,
            rule="bool_type",
            type=type(value).__name__,
        )
    return value


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


def _decode_positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _contract_error(
            f"{field} must be a positive int",
            field=field,
            rule="int_type",
            type=type(value).__name__,
        )
    if value < 1:
        raise _contract_error(
            f"{field} must be a positive int",
            field=field,
            rule="positive",
        )
    return value


def _encode_instrument(instrument: Instrument) -> dict[str, object]:
    return {
        "instrument_id": _encode_str(
            instrument.instrument_id, field="instrument.instrument_id"
        ),
        "symbol": _encode_str(instrument.symbol, field="instrument.symbol"),
        "name": _encode_str(instrument.name, field="instrument.name"),
        "market": _encode_enum(
            instrument.market, field="instrument.market", table=_MARKET_BY_VALUE
        ),
        "exchange": _encode_str(instrument.exchange, field="instrument.exchange"),
        "currency": _encode_str(instrument.currency, field="instrument.currency"),
        "timezone": _encode_str(instrument.timezone, field="instrument.timezone"),
        "asset_type": _encode_enum(
            instrument.asset_type,
            field="instrument.asset_type",
            table=_ASSET_TYPE_BY_VALUE,
        ),
        "is_active": _encode_bool(instrument.is_active, field="instrument.is_active"),
        "listing_status": _encode_str(
            instrument.listing_status, field="instrument.listing_status"
        ),
        "country": _encode_optional_str(
            instrument.country, field="instrument.country"
        ),
        "mic": _encode_optional_str(instrument.mic, field="instrument.mic"),
        "underlying_instrument_id": _encode_optional_str(
            instrument.underlying_instrument_id,
            field="instrument.underlying_instrument_id",
        ),
        "multiplier": _encode_optional_decimal(
            instrument.multiplier, field="instrument.multiplier"
        ),
        "tick_size": _encode_optional_decimal(
            instrument.tick_size, field="instrument.tick_size"
        ),
        "lot_size": _encode_optional_decimal(
            instrument.lot_size, field="instrument.lot_size"
        ),
        "metadata_version": _encode_positive_int(
            instrument.metadata_version, field="instrument.metadata_version"
        ),
    }


def _decode_instrument(raw: object) -> Instrument:
    obj = _require_mapping(raw, field="value.instrument", required_keys=_INSTRUMENT_KEYS)
    instrument_id = _decode_str(
        obj["instrument_id"], field="value.instrument.instrument_id"
    )
    symbol = _decode_str(obj["symbol"], field="value.instrument.symbol")
    name = _decode_str(obj["name"], field="value.instrument.name")
    market = _decode_enum(
        obj["market"], field="value.instrument.market", table=_MARKET_BY_VALUE
    )
    exchange = _decode_str(obj["exchange"], field="value.instrument.exchange")
    currency = _decode_str(obj["currency"], field="value.instrument.currency")
    timezone = _decode_str(obj["timezone"], field="value.instrument.timezone")
    asset_type = _decode_enum(
        obj["asset_type"],
        field="value.instrument.asset_type",
        table=_ASSET_TYPE_BY_VALUE,
    )
    is_active = _decode_bool(obj["is_active"], field="value.instrument.is_active")
    listing_status = _decode_str(
        obj["listing_status"], field="value.instrument.listing_status"
    )
    country = _decode_optional_str(obj["country"], field="value.instrument.country")
    mic = _decode_optional_str(obj["mic"], field="value.instrument.mic")
    underlying_instrument_id = _decode_optional_str(
        obj["underlying_instrument_id"],
        field="value.instrument.underlying_instrument_id",
    )
    multiplier = _decode_optional_decimal(
        obj["multiplier"], field="value.instrument.multiplier"
    )
    tick_size = _decode_optional_decimal(
        obj["tick_size"], field="value.instrument.tick_size"
    )
    lot_size = _decode_optional_decimal(
        obj["lot_size"], field="value.instrument.lot_size"
    )
    metadata_version = _decode_positive_int(
        obj["metadata_version"], field="value.instrument.metadata_version"
    )
    # Domain Instrument may echo identity fields in DataContractError.details;
    # never surface those — sanitize to field/rule only.
    try:
        return Instrument(
            instrument_id=instrument_id,
            symbol=symbol,
            name=name,
            market=market,
            exchange=exchange,
            currency=currency,
            timezone=timezone,
            asset_type=asset_type,
            is_active=is_active,
            listing_status=listing_status,
            country=country,
            mic=mic,
            underlying_instrument_id=underlying_instrument_id,
            multiplier=multiplier,
            tick_size=tick_size,
            lot_size=lot_size,
            metadata_version=metadata_version,
        )
    except Exception:
        raise _contract_error(
            "value.instrument failed contract construction",
            field="value.instrument",
            rule="construct",
        ) from None


def _encode_bar(bar: MarketBar) -> dict[str, object]:
    return {
        "timestamp": _encode_datetime(bar.timestamp, field="latest_market_row.timestamp"),
        "open": _encode_decimal(bar.open, field="latest_market_row.open"),
        "high": _encode_decimal(bar.high, field="latest_market_row.high"),
        "low": _encode_decimal(bar.low, field="latest_market_row.low"),
        "close": _encode_decimal(bar.close, field="latest_market_row.close"),
        "volume": _encode_decimal(bar.volume, field="latest_market_row.volume"),
    }


def _decode_bar(raw: object) -> MarketBar:
    obj = _require_mapping(
        raw, field="value.latest_market_row", required_keys=_BAR_KEYS
    )
    timestamp = _decode_datetime(
        obj["timestamp"], field="value.latest_market_row.timestamp"
    )
    open_ = _decode_decimal(obj["open"], field="value.latest_market_row.open")
    high = _decode_decimal(obj["high"], field="value.latest_market_row.high")
    low = _decode_decimal(obj["low"], field="value.latest_market_row.low")
    close = _decode_decimal(obj["close"], field="value.latest_market_row.close")
    volume = _decode_decimal(obj["volume"], field="value.latest_market_row.volume")
    try:
        return MarketBar(
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
    except Exception:
        raise _contract_error(
            "value.latest_market_row failed contract construction",
            field="value.latest_market_row",
            rule="construct",
        ) from None


def _encode_indicators(indicators: TechnicalIndicators) -> dict[str, object]:
    return {
        "ema_10": _encode_optional_decimal(indicators.ema_10, field="indicators.ema_10"),
        "sma_50": _encode_optional_decimal(indicators.sma_50, field="indicators.sma_50"),
        "sma_200": _encode_optional_decimal(
            indicators.sma_200, field="indicators.sma_200"
        ),
        "rsi_14": _encode_optional_decimal(indicators.rsi_14, field="indicators.rsi_14"),
        "macd": _encode_optional_decimal(indicators.macd, field="indicators.macd"),
        "macd_signal": _encode_optional_decimal(
            indicators.macd_signal, field="indicators.macd_signal"
        ),
        "macd_histogram": _encode_optional_decimal(
            indicators.macd_histogram, field="indicators.macd_histogram"
        ),
        "atr_14": _encode_optional_decimal(indicators.atr_14, field="indicators.atr_14"),
        "bollinger_mid": _encode_optional_decimal(
            indicators.bollinger_mid, field="indicators.bollinger_mid"
        ),
        "bollinger_upper": _encode_optional_decimal(
            indicators.bollinger_upper, field="indicators.bollinger_upper"
        ),
        "bollinger_lower": _encode_optional_decimal(
            indicators.bollinger_lower, field="indicators.bollinger_lower"
        ),
        "vwma": _encode_optional_decimal(indicators.vwma, field="indicators.vwma"),
        "mfi": _encode_optional_decimal(indicators.mfi, field="indicators.mfi"),
    }


def _decode_indicators(raw: object) -> TechnicalIndicators:
    obj = _require_mapping(raw, field="value.indicators", required_keys=_INDICATOR_KEYS)
    return TechnicalIndicators(
        ema_10=_decode_optional_decimal(obj["ema_10"], field="value.indicators.ema_10"),
        sma_50=_decode_optional_decimal(obj["sma_50"], field="value.indicators.sma_50"),
        sma_200=_decode_optional_decimal(
            obj["sma_200"], field="value.indicators.sma_200"
        ),
        rsi_14=_decode_optional_decimal(obj["rsi_14"], field="value.indicators.rsi_14"),
        macd=_decode_optional_decimal(obj["macd"], field="value.indicators.macd"),
        macd_signal=_decode_optional_decimal(
            obj["macd_signal"], field="value.indicators.macd_signal"
        ),
        macd_histogram=_decode_optional_decimal(
            obj["macd_histogram"], field="value.indicators.macd_histogram"
        ),
        atr_14=_decode_optional_decimal(obj["atr_14"], field="value.indicators.atr_14"),
        bollinger_mid=_decode_optional_decimal(
            obj["bollinger_mid"], field="value.indicators.bollinger_mid"
        ),
        bollinger_upper=_decode_optional_decimal(
            obj["bollinger_upper"], field="value.indicators.bollinger_upper"
        ),
        bollinger_lower=_decode_optional_decimal(
            obj["bollinger_lower"], field="value.indicators.bollinger_lower"
        ),
        vwma=_decode_optional_decimal(obj["vwma"], field="value.indicators.vwma"),
        mfi=_decode_optional_decimal(obj["mfi"], field="value.indicators.mfi"),
    )


def _encode_meta(meta: ProviderResultMeta) -> dict[str, object]:
    return {
        "vendor": _encode_enum(
            meta.vendor, field="meta.vendor", table=_VENDOR_BY_VALUE
        ),
        "category": _encode_enum(
            meta.category, field="meta.category", table=_CATEGORY_BY_VALUE
        ),
        "role": _encode_enum(meta.role, field="meta.role", table=_ROLE_BY_VALUE),
        "as_of": _encode_datetime(meta.as_of, field="meta.as_of"),
        "fetched_at": _encode_datetime(meta.fetched_at, field="meta.fetched_at"),
        "freshness": _encode_enum(
            meta.freshness, field="meta.freshness", table=_FRESHNESS_BY_VALUE
        ),
        "session": _encode_enum(
            meta.session, field="meta.session", table=_SESSION_BY_VALUE
        ),
        "latency_ms": _encode_optional_nonnegative_int(
            meta.latency_ms, field="meta.latency_ms"
        ),
        "cache_disposition": _encode_enum(
            meta.cache_disposition,
            field="meta.cache_disposition",
            table=_CACHE_DISP_BY_VALUE,
        ),
        "adjustment": _encode_optional_enum(
            meta.adjustment,
            field="meta.adjustment",
            table=_ADJUSTMENT_BY_VALUE,
        ),
        "data_delay_seconds": _encode_optional_nonnegative_int(
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
    vendor = _decode_enum(obj["vendor"], field="meta.vendor", table=_VENDOR_BY_VALUE)
    category = _decode_enum(
        obj["category"], field="meta.category", table=_CATEGORY_BY_VALUE
    )
    role = _decode_enum(obj["role"], field="meta.role", table=_ROLE_BY_VALUE)
    as_of = _decode_datetime(obj["as_of"], field="meta.as_of")
    fetched_at = _decode_datetime(obj["fetched_at"], field="meta.fetched_at")
    freshness = _decode_enum(
        obj["freshness"], field="meta.freshness", table=_FRESHNESS_BY_VALUE
    )
    session = _decode_enum(obj["session"], field="meta.session", table=_SESSION_BY_VALUE)
    latency_ms = _decode_optional_nonnegative_int(
        obj["latency_ms"], field="meta.latency_ms"
    )
    cache_disposition = _decode_enum(
        obj["cache_disposition"],
        field="meta.cache_disposition",
        table=_CACHE_DISP_BY_VALUE,
    )
    adjustment = _decode_optional_enum(
        obj["adjustment"],
        field="meta.adjustment",
        table=_ADJUSTMENT_BY_VALUE,
    )
    data_delay_seconds = _decode_optional_nonnegative_int(
        obj["data_delay_seconds"], field="meta.data_delay_seconds"
    )
    # ProviderResultMeta may raise DataContractError for warning grammar etc.
    # Those details are already non-echoing; still strip cause chain.
    try:
        return ProviderResultMeta(
            vendor=vendor,
            category=category,
            role=role,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            session=session,
            latency_ms=latency_ms,
            cache_disposition=cache_disposition,
            adjustment=adjustment,
            data_delay_seconds=data_delay_seconds,
            warnings=tuple(warnings),
        )
    except DataContractError as exc:
        # Re-raise without cause; keep stable field/rule from meta DTO when present.
        field = exc.details.get("field", "meta")
        rule = exc.details.get("rule", "construct")
        if not isinstance(field, str):
            field = "meta"
        if not isinstance(rule, str):
            rule = "construct"
        raise _contract_error(
            "meta failed contract construction",
            field=f"meta.{field}" if not str(field).startswith("meta") else str(field),
            rule=str(rule),
        ) from None
    except Exception:
        raise _contract_error(
            "meta failed contract construction",
            field="meta",
            rule="construct",
        ) from None


def _encode_snapshot(snapshot: VerifiedMarketSnapshot) -> dict[str, object]:
    return {
        "instrument": _encode_instrument(snapshot.instrument),
        "requested_as_of": _encode_datetime(
            snapshot.requested_as_of, field="value.requested_as_of"
        ),
        "latest_market_row": _encode_bar(snapshot.latest_market_row),
        "indicators": _encode_indicators(snapshot.indicators),
        "recent_closes": [
            _encode_decimal(price, field=f"value.recent_closes[{idx}]")
            for idx, price in enumerate(snapshot.recent_closes)
        ],
        "adjustment": _encode_enum(
            snapshot.adjustment,
            field="value.adjustment",
            table=_ADJUSTMENT_BY_VALUE,
        ),
        "session": _encode_enum(
            snapshot.session, field="value.session", table=_SESSION_BY_VALUE
        ),
        "algorithm_version": _encode_str(
            snapshot.algorithm_version, field="value.algorithm_version"
        ),
    }


def _decode_snapshot(raw: object) -> VerifiedMarketSnapshot:
    obj = _require_mapping(raw, field="value", required_keys=_VALUE_KEYS)
    closes_raw = obj["recent_closes"]
    if not isinstance(closes_raw, list):
        raise _contract_error(
            "value.recent_closes must be an array",
            field="value.recent_closes",
            rule="type",
            type=type(closes_raw).__name__,
        )
    closes: list[Decimal] = []
    for idx, item in enumerate(closes_raw):
        closes.append(_decode_decimal(item, field=f"value.recent_closes[{idx}]"))
    instrument = _decode_instrument(obj["instrument"])
    requested_as_of = _decode_datetime(
        obj["requested_as_of"], field="value.requested_as_of"
    )
    latest_market_row = _decode_bar(obj["latest_market_row"])
    indicators = _decode_indicators(obj["indicators"])
    adjustment = _decode_enum(
        obj["adjustment"],
        field="value.adjustment",
        table=_ADJUSTMENT_BY_VALUE,
    )
    session = _decode_enum(
        obj["session"], field="value.session", table=_SESSION_BY_VALUE
    )
    algorithm_version = _decode_str(
        obj["algorithm_version"], field="value.algorithm_version"
    )
    try:
        return VerifiedMarketSnapshot(
            instrument=instrument,
            requested_as_of=requested_as_of,
            latest_market_row=latest_market_row,
            indicators=indicators,
            recent_closes=tuple(closes),
            adjustment=adjustment,
            session=session,
            algorithm_version=algorithm_version,
        )
    except DataContractError:
        # Nested decode helpers already raised safe errors; domain post_init only.
        raise _contract_error(
            "value failed contract construction",
            field="value",
            rule="construct",
        ) from None
    except Exception:
        raise _contract_error(
            "value failed contract construction",
            field="value",
            rule="construct",
        ) from None


def _require_coherence(
    entry: CacheEntry,
    meta: ProviderResultMeta,
    snapshot: VerifiedMarketSnapshot,
) -> None:
    if entry.category is not DataCategory.MARKET_SNAPSHOT:
        raise _contract_error(
            "CacheEntry.category must be MARKET_SNAPSHOT",
            field="entry.category",
            rule="category_market_snapshot",
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
    if entry.freshness is not meta.freshness:
        raise _contract_error(
            "CacheEntry.freshness must equal meta.freshness",
            field="entry.freshness",
            rule="coherence_freshness",
        )
    if entry.market is not snapshot.instrument.market:
        raise _contract_error(
            "CacheEntry.market must equal value.instrument.market",
            field="entry.market",
            rule="coherence_market",
        )
    if entry.instrument_id != snapshot.instrument.instrument_id:
        raise _contract_error(
            "CacheEntry.instrument_id must equal value.instrument.instrument_id",
            field="entry.instrument_id",
            rule="coherence_instrument_id",
        )
    if snapshot.requested_as_of != meta.as_of:
        raise _contract_error(
            "value.requested_as_of must equal meta.as_of",
            field="value.requested_as_of",
            rule="coherence_requested_as_of",
        )


def _dumps_canonical(payload: dict[str, object]) -> str:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        # Reject non-JSON-safe structures without custom serializers or content echo.
        raise _contract_error(
            "payload is not JSON-serializable under codec rules",
            field="payload",
            rule="json_serialize",
        ) from None


def _strict_object_pairs_hook(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate object keys at any nesting depth."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJsonError("duplicate object key")
        result[key] = value
    return result


def _strict_parse_constant(_name: str) -> object:
    """Reject non-standard JSON constants (NaN / Infinity / -Infinity)."""
    raise _StrictJsonError("nonstandard constant")


def _loads_strict(text: str) -> object:
    """Strict JSON parse: no duplicate keys, no NaN/Infinity tokens.

    Internal parse failures map to a safe DataContractError with no cause,
    context, or payload echo.
    """
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object_pairs_hook,
            parse_constant=_strict_parse_constant,
        )
    except _StrictJsonError:
        raise _contract_error(
            "payload_json is not valid JSON",
            field="payload_json",
            rule="malformed_json",
        ) from None
    except json.JSONDecodeError:
        raise _contract_error(
            "payload_json is not valid JSON",
            field="payload_json",
            rule="malformed_json",
        ) from None


class VerifiedMarketSnapshotCacheCodec:
    """Phase 1D built-in codec for VerifiedMarketSnapshot cache payloads."""

    codec_id: str = CODEC_ID

    def encode(self, success: ProviderSuccess[VerifiedMarketSnapshot]) -> str:
        if not isinstance(success, ProviderSuccess):
            raise _contract_error(
                "success must be a ProviderSuccess",
                field="success",
                rule="type",
                type=type(success).__name__,
            )
        if not isinstance(success.meta, ProviderResultMeta):
            raise _contract_error(
                "success.meta must be a ProviderResultMeta",
                field="success.meta",
                rule="type",
                type=type(success.meta).__name__,
            )
        if not isinstance(success.value, VerifiedMarketSnapshot):
            raise _contract_error(
                "success.value must be a VerifiedMarketSnapshot",
                field="success.value",
                rule="type",
                type=type(success.value).__name__,
            )

        meta = success.meta
        snapshot = success.value

        if meta.category is not DataCategory.MARKET_SNAPSHOT:
            raise _contract_error(
                "meta.category must be MARKET_SNAPSHOT for this codec",
                field="meta.category",
                rule="category_market_snapshot",
            )
        if meta.cache_disposition is not CacheDisposition.MISS:
            raise _contract_error(
                "meta.cache_disposition must be MISS on encode",
                field="meta.cache_disposition",
                rule="cache_disposition_miss",
            )
        if snapshot.requested_as_of != meta.as_of:
            raise _contract_error(
                "value.requested_as_of must equal meta.as_of on encode",
                field="value.requested_as_of",
                rule="requested_as_of_matches_meta",
            )

        validate_verified_market_snapshot(snapshot)

        envelope: dict[str, object] = {
            "codec": self.codec_id,
            "meta": _encode_meta(meta),
            "schema_version": _SCHEMA_VERSION,
            "value": _encode_snapshot(snapshot),
        }
        return _dumps_canonical(envelope)

    def decode(
        self, entry: CacheEntry
    ) -> ProviderSuccess[VerifiedMarketSnapshot]:
        if not isinstance(entry, CacheEntry):
            raise _contract_error(
                "entry must be a CacheEntry",
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

        loaded = _loads_strict(entry.payload_json)

        root = _require_mapping(loaded, field="payload", required_keys=_TOP_LEVEL_KEYS)

        codec = root["codec"]
        if not isinstance(codec, str) or codec != CODEC_ID:
            raise _contract_error(
                "payload.codec must be verified_market_snapshot.v1",
                field="payload.codec",
                rule="codec_id",
            )

        schema_version = root["schema_version"]
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise _contract_error(
                "payload.schema_version must be an int",
                field="payload.schema_version",
                rule="type",
                type=type(schema_version).__name__,
            )
        if schema_version != _SCHEMA_VERSION:
            raise _contract_error(
                "payload.schema_version must be 1",
                field="payload.schema_version",
                rule="schema_version",
            )

        meta = _decode_meta(root["meta"])
        # Cached payloads are written as MISS; only then rewrite disposition to HIT.
        if meta.cache_disposition is not CacheDisposition.MISS:
            raise _contract_error(
                "meta.cache_disposition must be MISS in cache payload",
                field="meta.cache_disposition",
                rule="cache_disposition_miss",
            )
        snapshot = _decode_snapshot(root["value"])

        validate_verified_market_snapshot(snapshot)
        _require_coherence(entry, meta, snapshot)

        # Preserve all meta fields except cache disposition → HIT.
        hit_meta = replace(meta, cache_disposition=CacheDisposition.HIT)
        return ProviderSuccess(value=snapshot, meta=hit_meta)
