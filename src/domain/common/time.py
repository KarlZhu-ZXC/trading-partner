"""Time contracts: all persisted and envelope timestamps must be timezone-aware."""

from __future__ import annotations

from datetime import UTC, datetime

from domain.common.errors import DataContractError


def require_aware_datetime(value: datetime, *, field_name: str = "datetime") -> datetime:
    """Reject naive datetimes; return the same aware datetime."""
    if not isinstance(value, datetime):
        raise DataContractError(
            f"{field_name} must be a datetime",
            details={"field": field_name, "type": type(value).__name__},
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataContractError(
            f"{field_name} must be timezone-aware ISO 8601 datetime",
            details={"field": field_name},
        )
    return value


def ensure_utc(value: datetime) -> datetime:
    """Normalize an aware datetime to UTC."""
    aware = require_aware_datetime(value)
    return aware.astimezone(UTC)
