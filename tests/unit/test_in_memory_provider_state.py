"""Phase 1D D8b: thread-safe in-memory Provider State stores."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from application.dto.provider_state import CacheEntry
from application.services.provider_cache_support import build_cache_key
from domain.common.enums import (
    CircuitState,
    DataCategory,
    Freshness,
    HealthState,
    Market,
    VendorId,
)
from domain.common.errors import DataContractError
from infrastructure.persistence.in_memory_provider_state import (
    InMemoryProviderCacheStore,
    InMemoryProviderHealthStore,
    InMemoryProviderRateLimitStore,
)
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _entry(
    *,
    key: str | None = None,
    payload: str = '{"price":"100.00"}',
    as_of: datetime = NOW,
    fetched_at: datetime | None = None,
    expires_at: datetime | None = None,
    instrument_id: str | None = "equity:US:NVDA",
) -> CacheEntry:
    fetched = fetched_at or NOW
    expires = expires_at or (NOW + timedelta(seconds=30))
    k = key or build_cache_key(
        Market.US,
        DataCategory.MARKET_QUOTE,
        instrument_id,
        as_of,
        "get_quote|aabbccddeeff0011",
    )
    return CacheEntry(
        key=k,
        category=DataCategory.MARKET_QUOTE,
        market=Market.US,
        instrument_id=instrument_id,
        vendor=VendorId.MOCK_US,
        payload_json=payload,
        as_of=as_of,
        fetched_at=fetched,
        expires_at=expires,
        freshness=Freshness.FRESH,
    )


@pytest.fixture
def redactor() -> DefaultSecretRedactor:
    return DefaultSecretRedactor()


@pytest.fixture
def cache_store(redactor: DefaultSecretRedactor) -> InMemoryProviderCacheStore:
    return InMemoryProviderCacheStore(redactor)


@pytest.fixture
def health_store() -> InMemoryProviderHealthStore:
    return InMemoryProviderHealthStore()


@pytest.fixture
def rate_store() -> InMemoryProviderRateLimitStore:
    return InMemoryProviderRateLimitStore()


# --- Cache ---


def test_cache_set_get_delete_roundtrip(
    cache_store: InMemoryProviderCacheStore,
) -> None:
    entry = _entry()
    assert cache_store.get(entry.key) is None
    cache_store.set(entry.key, entry)
    loaded = cache_store.get(entry.key)
    assert loaded is not None
    assert loaded.key == entry.key
    assert loaded.payload_json == entry.payload_json
    assert loaded.as_of == entry.as_of
    assert loaded.vendor is VendorId.MOCK_US
    cache_store.delete(entry.key)
    assert cache_store.get(entry.key) is None
    cache_store.delete(entry.key)  # idempotent


def test_cache_get_returns_expired_entries(
    cache_store: InMemoryProviderCacheStore,
) -> None:
    expired = _entry(
        fetched_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )
    cache_store.set(expired.key, expired)
    loaded = cache_store.get(expired.key)
    assert loaded is not None
    assert loaded.expires_at < NOW


def test_cache_set_redacts_payload_json(
    cache_store: InMemoryProviderCacheStore,
) -> None:
    secret = "sk-" + "abcdefghijklmnop"
    payload = json.dumps({"token": secret, "price": "1"})
    entry = _entry(payload=payload)
    cache_store.set(entry.key, entry)
    loaded = cache_store.get(entry.key)
    assert loaded is not None
    assert secret not in loaded.payload_json
    assert "***REDACTED***" in loaded.payload_json
    parsed = json.loads(loaded.payload_json)
    assert parsed["price"] == "1"


def test_cache_set_rejects_invalid_json_after_redaction() -> None:
    class BrokenRedactor:
        def redact_mapping(self, value: Any) -> dict[str, object]:
            return dict(value)

        def redact_text(self, value: str) -> str:
            return "not-valid-json{"

    store = InMemoryProviderCacheStore(BrokenRedactor())  # type: ignore[arg-type]
    entry = _entry()
    with pytest.raises(DataContractError, match="valid JSON after redaction"):
        store.set(entry.key, entry)
    assert store.get(entry.key) is None


def test_cache_set_key_must_match_entry(
    cache_store: InMemoryProviderCacheStore,
) -> None:
    entry = _entry()
    other = build_cache_key(
        Market.US,
        DataCategory.MARKET_QUOTE,
        "equity:US:NVDA",
        NOW,
        "other_op|0123456789abcdef",
    )
    with pytest.raises(DataContractError, match="key must match") as exc_info:
        cache_store.set(other, entry)
    err = exc_info.value
    assert other not in err.message
    assert entry.key not in err.message


def test_cache_rejects_invalid_key_without_echo(
    cache_store: InMemoryProviderCacheStore,
) -> None:
    bad = "api_key=test-secret-not-a-cache-key"
    with pytest.raises(DataContractError) as exc_info:
        cache_store.get(bad)
    err = exc_info.value
    assert err.details.get("field") == "key"
    assert bad not in err.message
    assert "test-secret-not-a-cache-key" not in err.message


def test_cache_concurrent_set_get_stable(
    cache_store: InMemoryProviderCacheStore,
) -> None:
    n = 40
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []
    entry = _entry()

    def _worker(i: int) -> None:
        barrier.wait(timeout=10)
        if i % 2 == 0:
            cache_store.set(entry.key, entry)
        else:
            cache_store.get(entry.key)

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_worker, i) for i in range(n)]
        for fut in as_completed(futures):
            try:
                fut.result()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
    assert errors == []
    loaded = cache_store.get(entry.key)
    assert loaded is not None
    assert loaded.payload_json == entry.payload_json


# --- Health ---


def test_health_virtual_snapshot_no_write(
    health_store: InMemoryProviderHealthStore,
) -> None:
    snap = health_store.get(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT)
    assert snap.state is HealthState.OK
    assert snap.success_count == 0
    assert snap.failure_count == 0
    assert snap.circuit_state is CircuitState.CLOSED
    assert snap.last_success_at is None
    assert snap.last_error_code is None


def test_health_record_success_and_failure_preserve_counters(
    health_store: InMemoryProviderHealthStore,
) -> None:
    v, c = VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT
    t1 = NOW
    t2 = NOW + timedelta(seconds=1)
    t3 = NOW + timedelta(seconds=2)

    health_store.record_success(v, c, t1)
    snap = health_store.get(v, c)
    assert snap.success_count == 1
    assert snap.failure_count == 0
    assert snap.state is HealthState.OK
    assert snap.last_success_at == t1
    assert snap.last_error_code is None

    health_store.record_failure(v, c, t2, "PROVIDER_TIMEOUT_ERROR")
    snap = health_store.get(v, c)
    assert snap.success_count == 1
    assert snap.failure_count == 1
    assert snap.state is HealthState.ERROR
    assert snap.last_success_at == t1
    assert snap.last_failure_at == t2
    assert snap.last_error_code == "PROVIDER_TIMEOUT_ERROR"

    health_store.record_success(v, c, t3)
    snap = health_store.get(v, c)
    assert snap.success_count == 2
    assert snap.failure_count == 1
    assert snap.state is HealthState.OK
    assert snap.last_success_at == t3
    assert snap.last_failure_at == t2
    assert snap.last_error_code is None


def test_health_circuit_projection_rules(
    health_store: InMemoryProviderHealthStore,
) -> None:
    v, c = VendorId.MOCK_A_SHARE, DataCategory.MARKET_QUOTE
    t = NOW

    health_store.set_circuit_state(v, c, CircuitState.OPEN, t)
    snap = health_store.get(v, c)
    assert snap.circuit_state is CircuitState.OPEN
    assert snap.state is HealthState.ERROR
    assert snap.success_count == 0

    health_store.record_failure(v, c, t + timedelta(seconds=1), "X")
    health_store.set_circuit_state(v, c, CircuitState.CLOSED, t + timedelta(seconds=2))
    snap = health_store.get(v, c)
    assert snap.circuit_state is CircuitState.CLOSED
    assert snap.state is HealthState.ERROR
    assert snap.failure_count == 1
    assert snap.last_error_code == "X"

    health_store.set_circuit_state(v, c, CircuitState.HALF_OPEN, t + timedelta(seconds=3))
    snap = health_store.get(v, c)
    assert snap.circuit_state is CircuitState.HALF_OPEN
    assert snap.state is HealthState.ERROR

    health_store.record_success(v, c, t + timedelta(seconds=4))
    health_store.set_circuit_state(v, c, CircuitState.HALF_OPEN, t + timedelta(seconds=5))
    snap = health_store.get(v, c)
    assert snap.state is HealthState.DEGRADED
    assert snap.success_count >= 1


def test_health_rejects_invalid_error_code_without_echo(
    health_store: InMemoryProviderHealthStore,
) -> None:
    bad = "api_key=sk-secret"
    with pytest.raises(DataContractError) as exc_info:
        health_store.record_failure(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, NOW, bad)
    err = exc_info.value
    assert err.details.get("field") == "error_code"
    assert bad not in err.message
    assert "sk-secret" not in err.message


def test_health_concurrent_success_and_failure_counts(
    health_store: InMemoryProviderHealthStore,
) -> None:
    n = 20
    barrier = threading.Barrier(n * 2)
    errors: list[BaseException] = []

    def _success(_: int) -> None:
        barrier.wait(timeout=10)
        health_store.record_success(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE, NOW)

    def _failure(_: int) -> None:
        barrier.wait(timeout=10)
        health_store.record_failure(
            VendorId.EASTMONEY,
            DataCategory.MARKET_QUOTE,
            NOW,
            "PROVIDER_TIMEOUT_ERROR",
        )

    with ThreadPoolExecutor(max_workers=n * 2) as pool:
        futures = [pool.submit(_success, i) for i in range(n)]
        futures += [pool.submit(_failure, i) for i in range(n)]
        for fut in as_completed(futures):
            try:
                fut.result()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    assert errors == []
    snap = health_store.get(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE)
    assert snap.success_count == n
    assert snap.failure_count == n


# --- Rate limit ---


def test_rate_consume_atomic_increment(
    rate_store: InMemoryProviderRateLimitStore,
) -> None:
    v, c = VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT
    window = NOW.replace(microsecond=0)
    first = rate_store.consume(
        vendor=v,
        category=c,
        window_start=window,
        window_seconds=1,
        limit_count=1000,
        at=NOW,
    )
    assert first.request_count == 1
    second = rate_store.consume(
        vendor=v,
        category=c,
        window_start=window,
        window_seconds=1,
        limit_count=1000,
        at=NOW + timedelta(milliseconds=10),
    )
    assert second.request_count == 2
    got = rate_store.get(v, c, window)
    assert got is not None
    assert got.request_count == 2


def test_rate_consume_overwrites_policy_fields(
    rate_store: InMemoryProviderRateLimitStore,
) -> None:
    v, c = VendorId.MOCK_A_SHARE, DataCategory.MARKET_QUOTE
    window = NOW.replace(microsecond=0)
    rate_store.consume(
        vendor=v,
        category=c,
        window_start=window,
        window_seconds=1,
        limit_count=10,
        at=NOW,
    )
    snap = rate_store.consume(
        vendor=v,
        category=c,
        window_start=window,
        window_seconds=5,
        limit_count=99,
        at=NOW + timedelta(seconds=1),
    )
    assert snap.request_count == 2
    assert snap.window_seconds == 5
    assert snap.limit_count == 99


def test_rate_get_missing_returns_none(
    rate_store: InMemoryProviderRateLimitStore,
) -> None:
    assert (
        rate_store.get(
            VendorId.MOCK_US,
            DataCategory.MARKET_SNAPSHOT,
            NOW.replace(microsecond=0),
        )
        is None
    )


def test_rate_concurrent_consume_counts(
    rate_store: InMemoryProviderRateLimitStore,
) -> None:
    n = 50
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []
    window = NOW.replace(microsecond=0)

    def _worker(_: int) -> None:
        barrier.wait(timeout=10)
        rate_store.consume(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_SNAPSHOT,
            window_start=window,
            window_seconds=1,
            limit_count=1000,
            at=NOW,
        )

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_worker, i) for i in range(n)]
        for fut in as_completed(futures):
            try:
                fut.result()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    assert errors == []
    snap = rate_store.get(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, window)
    assert snap is not None
    assert snap.request_count == n
