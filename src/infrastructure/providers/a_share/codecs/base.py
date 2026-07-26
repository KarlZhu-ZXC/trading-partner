"""Shared, schema-stable primitives for A-share provider cache codecs."""

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
from domain.a_share.enums import BarInterval, FinancialStatementType, TickDirection
from domain.common.enums import (
    AdjustmentMethod,
    CacheDisposition,
    DataCategory,
    Freshness,
    ReliabilityLevel,
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
