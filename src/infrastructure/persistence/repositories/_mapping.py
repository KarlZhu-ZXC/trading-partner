"""Shared ORM ↔ domain mapping helpers for research / instrument repositories."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from domain.common.time import require_aware_datetime


def dt_to_db(value: datetime) -> str:
    require_aware_datetime(value)
    return value.isoformat()


def dt_from_db(value: str, *, field_name: str) -> datetime:
    return require_aware_datetime(datetime.fromisoformat(value), field_name=field_name)


def dt_opt_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    return dt_to_db(value)


def dt_opt_from_db(value: str | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return dt_from_db(value, field_name=field_name)


def date_to_db(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def date_from_db(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def decimal_to_db(value: Decimal | None) -> str | None:
    """Persist Decimal as a normalized fixed-point TEXT string (no sci notation)."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        msg = f"expected Decimal, got {type(value).__name__}"
        raise TypeError(msg)
    raw = str(value)
    if "E" in raw.upper() or "e" in raw:
        return format(value, "f")
    return raw


def decimal_from_db(value: str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value)


def bool_to_db(value: bool) -> int:
    return 1 if value else 0


def bool_from_db(value: int | bool) -> bool:
    return bool(value)
