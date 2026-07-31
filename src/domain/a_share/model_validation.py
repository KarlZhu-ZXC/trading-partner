"""Shared validators and constants for frozen A-share domain models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from domain.common.enums import AssetType, Market, ReliabilityLevel, VendorId
from domain.common.errors import DataContractError
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
_SOURCE_URL_MAX = 2_000

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

