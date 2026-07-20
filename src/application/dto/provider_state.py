"""Provider cache / health / rate-limit state DTOs (Phase 1D D5a).

Frozen slotted dataclasses. Domain/input validation raises DataContractError
before any persistence write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from domain.common.enums import (
    CircuitState,
    DataCategory,
    Freshness,
    HealthState,
    Market,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.providers.cache_key import validate_cache_instrument_id

# Design v1.11: provider health error_code grammar (no raw echo on reject).
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def require_provider_error_code(error_code: str) -> str:
    """Validate failure error_code; never echo the rejected value."""
    if not isinstance(error_code, str) or not _ERROR_CODE_RE.fullmatch(error_code):
        raise DataContractError(
            "error_code must match ^[A-Z][A-Z0-9_]{0,127}$",
            details={"field": "error_code"},
        )
    return error_code


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


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Persisted provider cache payload + metadata (safe domain/DTO JSON)."""

    key: str
    category: DataCategory
    market: Market
    instrument_id: str | None
    vendor: VendorId
    payload_json: str
    as_of: datetime
    fetched_at: datetime
    expires_at: datetime
    freshness: Freshness

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise DataContractError(
                "key must be a non-empty string",
                details={"field": "key"},
            )
        if not isinstance(self.payload_json, str):
            raise DataContractError(
                "payload_json must be a string",
                details={"field": "payload_json", "type": type(self.payload_json).__name__},
            )
        validate_cache_instrument_id(self.instrument_id, self.market)
        require_aware_datetime(self.as_of, field_name="as_of")
        require_aware_datetime(self.fetched_at, field_name="fetched_at")
        require_aware_datetime(self.expires_at, field_name="expires_at")
        if self.expires_at < self.fetched_at:
            raise DataContractError(
                "expires_at must be >= fetched_at",
                details={"field": "expires_at"},
            )


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    """Observability projection for provider health and circuit state."""

    vendor: VendorId
    category: DataCategory
    state: HealthState
    success_count: int
    failure_count: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error_code: str | None
    circuit_state: CircuitState

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.success_count, field_name="success_count")
        _require_nonnegative_int(self.failure_count, field_name="failure_count")
        if self.last_success_at is not None:
            require_aware_datetime(self.last_success_at, field_name="last_success_at")
        if self.last_failure_at is not None:
            require_aware_datetime(self.last_failure_at, field_name="last_failure_at")
        if self.last_error_code is not None:
            require_provider_error_code(self.last_error_code)


@dataclass(frozen=True, slots=True)
class ProviderRateLimitSnapshot:
    """Fixed-window rate-limit counter snapshot."""

    vendor: VendorId
    category: DataCategory
    window_start: datetime
    window_seconds: int
    request_count: int
    limit_count: int
    updated_at: datetime

    def __post_init__(self) -> None:
        require_aware_datetime(self.window_start, field_name="window_start")
        require_aware_datetime(self.updated_at, field_name="updated_at")
        _require_positive_int(self.window_seconds, field_name="window_seconds")
        _require_positive_int(self.limit_count, field_name="limit_count")
        _require_nonnegative_int(self.request_count, field_name="request_count")
