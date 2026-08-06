"""Thread-safe in-memory Provider State stores (Phase 1D D8b).

Used when the SQLite schema does not yet expose provider_cache /
provider_health / provider_rate_limits. Semantics match the SQL stores
for cache key/entry coherence, health projection, and atomic rate reservation.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime

from application.dto.provider_state import (
    CacheEntry,
    ProviderHealthSnapshot,
    ProviderRateLimitSnapshot,
    require_provider_error_code,
)
from application.ports.secret_redactor import SecretRedactor
from domain.common.enums import (
    CircuitState,
    DataCategory,
    HealthState,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.providers.cache_key import (
    parse_cache_key,
    require_cache_key_matches_fields,
)


def _require_positive_int(value: int, *, field_name: str) -> int:
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
    return value


def _virtual_health_snapshot(
    vendor: VendorId, category: DataCategory
) -> ProviderHealthSnapshot:
    return ProviderHealthSnapshot(
        vendor=vendor,
        category=category,
        state=HealthState.OK,
        success_count=0,
        failure_count=0,
        last_success_at=None,
        last_failure_at=None,
        last_error_code=None,
        circuit_state=CircuitState.CLOSED,
    )


def _circuit_health_for_new_row(circuit: CircuitState) -> HealthState:
    if circuit is CircuitState.OPEN:
        return HealthState.ERROR
    if circuit is CircuitState.HALF_OPEN:
        return HealthState.DEGRADED
    return HealthState.OK


def _project_health_state(
    *, current: HealthState, circuit: CircuitState
) -> HealthState:
    """Same projection rules as SqlAlchemyProviderHealthStore."""
    if circuit is CircuitState.OPEN:
        return HealthState.ERROR
    if circuit is CircuitState.HALF_OPEN:
        if current is HealthState.ERROR:
            return HealthState.ERROR
        return HealthState.DEGRADED
    return current


class _HealthRow:
    """Mutable in-memory health row (protected by store lock)."""

    __slots__ = (
        "state",
        "success_count",
        "failure_count",
        "last_success_at",
        "last_failure_at",
        "last_error_code",
        "circuit_state",
    )

    def __init__(self) -> None:
        self.state: HealthState = HealthState.OK
        self.success_count: int = 0
        self.failure_count: int = 0
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error_code: str | None = None
        self.circuit_state: CircuitState = CircuitState.CLOSED

    def to_snapshot(
        self, vendor: VendorId, category: DataCategory
    ) -> ProviderHealthSnapshot:
        return ProviderHealthSnapshot(
            vendor=vendor,
            category=category,
            state=self.state,
            success_count=self.success_count,
            failure_count=self.failure_count,
            last_success_at=self.last_success_at,
            last_failure_at=self.last_failure_at,
            last_error_code=self.last_error_code,
            circuit_state=self.circuit_state,
        )


class InMemoryProviderCacheStore:
    """Thread-safe ProviderCacheStore with v1 key/entry coherence + redaction."""

    def __init__(self, secret_redactor: SecretRedactor) -> None:
        self._secret_redactor = secret_redactor
        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}

    def get(self, key: str) -> CacheEntry | None:
        parse_cache_key(key)
        with self._lock:
            return self._entries.get(key)

    def set(self, key: str, entry: CacheEntry) -> None:
        require_cache_key_matches_fields(
            key,
            entry_key=entry.key,
            market=entry.market,
            category=entry.category,
            instrument_id=entry.instrument_id,
            as_of=entry.as_of,
        )
        require_aware_datetime(entry.as_of, field_name="as_of")
        require_aware_datetime(entry.fetched_at, field_name="fetched_at")
        require_aware_datetime(entry.expires_at, field_name="expires_at")

        redact_error_type: str | None = None
        redacted: str | None = None
        try:
            redacted = self._secret_redactor.redact_text(entry.payload_json)
        except Exception as exc:  # noqa: BLE001 — safe typed wrap
            redact_error_type = type(exc).__name__
        if redact_error_type is not None:
            raise DataContractError(
                "payload_json redaction failed",
                details={"field": "payload_json", "error_type": redact_error_type},
            )
        assert redacted is not None

        json_error_type: str | None = None
        try:
            json.loads(redacted)
        except json.JSONDecodeError as exc:
            json_error_type = type(exc).__name__
        if json_error_type is not None:
            raise DataContractError(
                "payload_json must remain valid JSON after redaction",
                details={"field": "payload_json", "error_type": json_error_type},
            )

        stored = CacheEntry(
            key=entry.key,
            category=entry.category,
            market=entry.market,
            instrument_id=entry.instrument_id,
            vendor=entry.vendor,
            payload_json=redacted,
            as_of=entry.as_of,
            fetched_at=entry.fetched_at,
            expires_at=entry.expires_at,
            freshness=entry.freshness,
        )
        with self._lock:
            self._entries[key] = stored

    def delete(self, key: str) -> None:
        parse_cache_key(key)
        with self._lock:
            self._entries.pop(key, None)


class InMemoryProviderHealthStore:
    """Thread-safe ProviderHealthStore with SQL-compatible DTO semantics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rows: dict[tuple[VendorId, DataCategory], _HealthRow] = {}

    def get(self, vendor: VendorId, category: DataCategory) -> ProviderHealthSnapshot:
        with self._lock:
            row = self._rows.get((vendor, category))
            if row is None:
                return _virtual_health_snapshot(vendor, category)
            return row.to_snapshot(vendor, category)

    def record_success(
        self, vendor: VendorId, category: DataCategory, at: datetime
    ) -> None:
        require_aware_datetime(at, field_name="at")
        with self._lock:
            row = self._rows.get((vendor, category))
            if row is None:
                row = _HealthRow()
                self._rows[(vendor, category)] = row
            row.success_count += 1
            row.state = HealthState.OK
            row.last_success_at = at
            row.last_error_code = None
            # Preserve failure_count, last_failure_at, circuit_state.

    def record_failure(
        self,
        vendor: VendorId,
        category: DataCategory,
        at: datetime,
        error_code: str,
    ) -> None:
        require_aware_datetime(at, field_name="at")
        code = require_provider_error_code(error_code)
        with self._lock:
            row = self._rows.get((vendor, category))
            if row is None:
                row = _HealthRow()
                self._rows[(vendor, category)] = row
            row.failure_count += 1
            row.state = HealthState.ERROR
            row.last_failure_at = at
            row.last_error_code = code
            # Preserve success_count, last_success_at, circuit_state.

    def set_circuit_state(
        self,
        vendor: VendorId,
        category: DataCategory,
        state: CircuitState,
        at: datetime,
    ) -> None:
        require_aware_datetime(at, field_name="at")
        if not isinstance(state, CircuitState):
            raise DataContractError(
                "state must be a CircuitState",
                details={"field": "state", "type": type(state).__name__},
            )
        with self._lock:
            row = self._rows.get((vendor, category))
            if row is None:
                row = _HealthRow()
                row.state = _circuit_health_for_new_row(state)
                row.circuit_state = state
                self._rows[(vendor, category)] = row
            else:
                row.circuit_state = state
                row.state = _project_health_state(current=row.state, circuit=state)


class InMemoryProviderRateLimitStore:
    """Thread-safe fixed-window rate-limit store with atomic reservations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Keyed by (vendor, category, window_start); policy fields are rewritten
        # on a successful reservation.
        self._rows: dict[
            tuple[VendorId, DataCategory, datetime], ProviderRateLimitSnapshot
        ] = {}

    def try_reserve(
        self,
        *,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
        window_seconds: int,
        limit_count: int,
        at: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        require_aware_datetime(window_start, field_name="window_start")
        require_aware_datetime(at, field_name="at")
        window_seconds = _require_positive_int(
            window_seconds, field_name="window_seconds"
        )
        limit_count = _require_positive_int(limit_count, field_name="limit_count")

        key = (vendor, category, window_start)
        with self._lock:
            existing = self._rows.get(key)
            if existing is not None and existing.request_count >= limit_count:
                # Denial is deliberately side-effect free.  In particular,
                # do not inflate the count while probing a full window.
                return None
            count = 1 if existing is None else existing.request_count + 1
            snapshot = ProviderRateLimitSnapshot(
                vendor=vendor,
                category=category,
                window_start=window_start,
                window_seconds=window_seconds,
                request_count=count,
                limit_count=limit_count,
                updated_at=at,
            )
            self._rows[key] = snapshot
            return snapshot

    def get(
        self,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        require_aware_datetime(window_start, field_name="window_start")
        with self._lock:
            return self._rows.get((vendor, category, window_start))
