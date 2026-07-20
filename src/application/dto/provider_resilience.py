"""Provider resilience DTOs (Phase 1D D5b).

Frozen slotted dataclasses for rate-limit policy/decisions and circuit permits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.common.enums import DataCategory, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime


def _require_positive_int(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataContractError(
            f"{field_name} must be an int",
            details={"field": field_name, "type": type(value).__name__},
        )
    if value <= 0:
        raise DataContractError(
            f"{field_name} must be positive",
            details={"field": field_name},
        )


def _require_nonnegative_int(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataContractError(
            f"{field_name} must be an int",
            details={"field": field_name, "type": type(value).__name__},
        )
    if value < 0:
        raise DataContractError(
            f"{field_name} must be nonnegative",
            details={"field": field_name},
        )


@dataclass(frozen=True, slots=True)
class CircuitCallPermit:
    """Admission token for one circuit-breaker call.

    CLOSED admits with ``half_open_generation=None``. HALF_OPEN admits carry the
    bucket's current positive generation so success/failure can be attributed
    unambiguously even after concurrent probes complete out of order.
    """

    vendor: VendorId
    category: DataCategory
    call_id: int
    half_open_generation: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.vendor, VendorId):
            raise DataContractError(
                "vendor must be a VendorId",
                details={"field": "vendor", "type": type(self.vendor).__name__},
            )
        if not isinstance(self.category, DataCategory):
            raise DataContractError(
                "category must be a DataCategory",
                details={
                    "field": "category",
                    "type": type(self.category).__name__,
                },
            )
        _require_positive_int(self.call_id, field_name="call_id")
        if self.half_open_generation is not None:
            _require_positive_int(
                self.half_open_generation, field_name="half_open_generation"
            )


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Fixed-window rate-limit policy for a vendor (window length + max count)."""

    window_seconds: int
    limit_count: int

    def __post_init__(self) -> None:
        _require_positive_int(self.window_seconds, field_name="window_seconds")
        _require_positive_int(self.limit_count, field_name="limit_count")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a fixed-window check-and-consume."""

    allowed: bool
    remaining: int | None
    reset_at: datetime | None
    limit_per_window: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise DataContractError(
                "allowed must be a bool",
                details={"field": "allowed", "type": type(self.allowed).__name__},
            )
        if self.remaining is not None:
            _require_nonnegative_int(self.remaining, field_name="remaining")
        if self.limit_per_window is not None:
            _require_positive_int(self.limit_per_window, field_name="limit_per_window")
        if self.reset_at is not None:
            require_aware_datetime(self.reset_at, field_name="reset_at")
