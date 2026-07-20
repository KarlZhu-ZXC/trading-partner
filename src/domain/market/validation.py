"""VerifiedMarketSnapshot pure contract validation (Phase 1D D6a / D8a).

Authoritative field/rule checks for domain snapshots. Failures raise
DataContractError with stable field/rule names only — never echo payload
values (prices, secrets, raw bars).

Infrastructure keeps a compatibility re-export at
``infrastructure.providers.common.contract_validation``; callers that must
stay domain-facing (e.g. RoutedMarketSnapshotService) import from here.
"""

from __future__ import annotations

from decimal import Decimal

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.models import (
    MarketBar,
    TechnicalIndicators,
    VerifiedMarketSnapshot,
)


def _require_decimal(value: object, *, field: str) -> Decimal:
    # Reject bool/int/float; require exact Decimal (bool is int subclass).
    if type(value) is not Decimal:
        raise DataContractError(
            f"{field} must be Decimal",
            details={
                "field": field,
                "rule": "decimal_type",
                "type": type(value).__name__,
            },
        )
    # NaN / sNaN / ±Infinity must not reach OHLC comparisons (those raise
    # decimal.InvalidOperation). Never echo the non-finite payload value.
    if not value.is_finite():
        raise DataContractError(
            f"{field} must be a finite Decimal",
            details={"field": field, "rule": "finite_decimal"},
        )
    return value


def validate_verified_market_snapshot(snapshot: VerifiedMarketSnapshot) -> None:
    """Validate a verified market snapshot against the Phase 1D contract.

    Checks types, timezone-aware datetimes, Decimal numerics, OHLC/volume
    invariants, non-empty recent_closes with last-close match, bar not after
    requested_as_of, and non-blank algorithm_version.

    Raises:
        DataContractError: on any contract violation (no payload echo).
    """
    if not isinstance(snapshot, VerifiedMarketSnapshot):
        raise DataContractError(
            "snapshot must be a VerifiedMarketSnapshot",
            details={
                "field": "snapshot",
                "rule": "type",
                "type": type(snapshot).__name__,
            },
        )

    if not isinstance(snapshot.instrument, Instrument):
        raise DataContractError(
            "instrument must be an Instrument",
            details={
                "field": "instrument",
                "rule": "type",
                "type": type(snapshot.instrument).__name__,
            },
        )

    if not isinstance(snapshot.latest_market_row, MarketBar):
        raise DataContractError(
            "latest_market_row must be a MarketBar",
            details={
                "field": "latest_market_row",
                "rule": "type",
                "type": type(snapshot.latest_market_row).__name__,
            },
        )

    if not isinstance(snapshot.indicators, TechnicalIndicators):
        raise DataContractError(
            "indicators must be TechnicalIndicators",
            details={
                "field": "indicators",
                "rule": "type",
                "type": type(snapshot.indicators).__name__,
            },
        )

    require_aware_datetime(snapshot.requested_as_of, field_name="requested_as_of")
    bar = snapshot.latest_market_row
    require_aware_datetime(bar.timestamp, field_name="latest_market_row.timestamp")

    open_ = _require_decimal(bar.open, field="latest_market_row.open")
    high = _require_decimal(bar.high, field="latest_market_row.high")
    low = _require_decimal(bar.low, field="latest_market_row.low")
    close = _require_decimal(bar.close, field="latest_market_row.close")
    volume = _require_decimal(bar.volume, field="latest_market_row.volume")

    if high < max(open_, close, low):
        raise DataContractError(
            "bar high must be >= max(open, close, low)",
            details={"field": "latest_market_row.high", "rule": "ohlc_high"},
        )
    if low > min(open_, close, high):
        raise DataContractError(
            "bar low must be <= min(open, close, high)",
            details={"field": "latest_market_row.low", "rule": "ohlc_low"},
        )
    if volume < 0:
        raise DataContractError(
            "bar volume must be >= 0",
            details={
                "field": "latest_market_row.volume",
                "rule": "volume_nonnegative",
            },
        )

    closes = snapshot.recent_closes
    if not isinstance(closes, tuple):
        raise DataContractError(
            "recent_closes must be a tuple",
            details={
                "field": "recent_closes",
                "rule": "type",
                "type": type(closes).__name__,
            },
        )
    if len(closes) == 0:
        raise DataContractError(
            "recent_closes must be non-empty",
            details={"field": "recent_closes", "rule": "non_empty"},
        )
    for idx, price in enumerate(closes):
        _require_decimal(price, field=f"recent_closes[{idx}]")
    if closes[-1] != close:
        raise DataContractError(
            "recent_closes last element must equal latest_market_row.close",
            details={
                "field": "recent_closes",
                "rule": "last_close_matches_bar",
            },
        )

    if bar.timestamp > snapshot.requested_as_of:
        raise DataContractError(
            "latest_market_row.timestamp must be <= requested_as_of",
            details={
                "field": "latest_market_row.timestamp",
                "rule": "not_after_as_of",
            },
        )

    algorithm_version = snapshot.algorithm_version
    if not isinstance(algorithm_version, str) or not algorithm_version.strip():
        raise DataContractError(
            "algorithm_version must be a non-empty non-blank string",
            details={"field": "algorithm_version", "rule": "non_blank"},
        )
