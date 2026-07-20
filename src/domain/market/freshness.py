"""Freshness classification pure rules (Phase 1D D7).

Classifies market data age relative to ``now`` into Freshness.FRESH / DELAYED /
STALE / UNKNOWN. Independent of as_of historical filtering (see domain.common.as_of).
"""

from __future__ import annotations

from datetime import datetime

from domain.common.enums import Freshness, TradingSession
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime


def _require_nonnegative_int(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataContractError(
            f"{field_name} must be an int",
            details={"field": field_name, "type": type(value).__name__},
        )
    if value < 0:
        raise DataContractError(
            f"{field_name} must be nonnegative",
            details={"field": field_name, "rule": "nonnegative"},
        )
    return value


def classify_freshness(
    *,
    now: datetime,
    data_timestamp: datetime,
    session: TradingSession,
    max_fresh_seconds: int,
    max_delayed_seconds: int,
    vendor_declared_delay_seconds: int | None,
) -> Freshness:
    """Classify data age into FRESH / DELAYED / STALE / UNKNOWN.

    Boundaries use ``<=``. Vendor delay ``> 0`` never yields FRESH. Future
    ``data_timestamp`` relative to ``now`` is a contract error (not FRESH).
    """
    require_aware_datetime(now, field_name="now")
    require_aware_datetime(data_timestamp, field_name="data_timestamp")

    if not isinstance(session, TradingSession):
        raise DataContractError(
            "session must be a TradingSession",
            details={
                "field": "session",
                "rule": "type",
                "type": type(session).__name__,
            },
        )

    fresh = _require_nonnegative_int(max_fresh_seconds, field_name="max_fresh_seconds")
    delayed = _require_nonnegative_int(
        max_delayed_seconds, field_name="max_delayed_seconds"
    )
    if fresh > delayed:
        raise DataContractError(
            "max_fresh_seconds must be <= max_delayed_seconds",
            details={
                "field": "max_fresh_seconds",
                "rule": "fresh_le_delayed",
            },
        )

    if vendor_declared_delay_seconds is not None:
        _require_nonnegative_int(
            vendor_declared_delay_seconds,
            field_name="vendor_declared_delay_seconds",
        )

    if data_timestamp > now:
        raise DataContractError(
            "data_timestamp must not be after now",
            details={
                "field": "data_timestamp",
                "rule": "future_data_timestamp",
            },
        )

    # Input types validated first; UNKNOWN session short-circuits classification.
    if session is TradingSession.UNKNOWN:
        return Freshness.UNKNOWN

    age_seconds = (now - data_timestamp).total_seconds()
    vendor_blocks_fresh = (
        vendor_declared_delay_seconds is not None and vendor_declared_delay_seconds > 0
    )

    if age_seconds <= fresh and not vendor_blocks_fresh:
        return Freshness.FRESH
    if age_seconds <= delayed:
        return Freshness.DELAYED
    return Freshness.STALE
