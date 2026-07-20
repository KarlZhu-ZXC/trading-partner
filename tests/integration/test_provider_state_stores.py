"""Phase 1D D5a: SqlAlchemy provider cache / health / rate-limit stores."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.dto.provider_state import CacheEntry
from application.dto.reddit_state import RedditSampleCacheEntry
from application.services.provider_cache_support import build_cache_key
from conftest import FixedClock
from domain.common.enums import (
    CircuitState,
    DataCategory,
    Freshness,
    HealthState,
    Market,
    VendorId,
)
from domain.common.errors import DataContractError, PersistenceError
from domain.us_context.enums import (
    USSentimentDirection,
    USSentimentLabelOrigin,
    USSentimentSource,
)
from domain.us_context.models import USSentimentSample
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.provider_cache_store import SqlAlchemyProviderCacheStore
from infrastructure.persistence.provider_health_store import SqlAlchemyProviderHealthStore
from infrastructure.persistence.provider_rate_limit_store import (
    SqlAlchemyProviderRateLimitStore,
)
from infrastructure.persistence.reddit_state_store import SqlAlchemyRedditStateStore
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _configure_sqlite_engine(url: str) -> Engine:
    """SQLite engine tuned for concurrent D5a store tests (WAL + busy timeout)."""
    eng = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30.0},
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_conn: Any, _connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return eng


def _exception_chain_nodes(exc: BaseException) -> list[BaseException]:
    """Walk __cause__/__context__ that would appear in a public traceback."""
    nodes: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        nodes.append(current)
        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
            stack.append(current.__context__)
    return nodes


def _assert_safe_persistence_error(
    exc: BaseException,
    *,
    forbidden_substrings: list[str],
) -> None:
    """PersistenceError must not expose raw DB/path/secret text in public chain."""
    assert isinstance(exc, PersistenceError)
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__
    for node in _exception_chain_nodes(exc):
        text_blob = f"{node!s}{node!r}"
        if isinstance(node, PersistenceError | DataContractError):
            text_blob += f"{node.message}{node.details!r}{node.details!s}"
        for secret in forbidden_substrings:
            assert secret not in text_blob
    assert isinstance(exc.details.get("error_type"), str)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = _configure_sqlite_engine(f"sqlite:///{tmp_path / 'provider_state_stores.db'}")
    Base.metadata.create_all(eng)
    yield eng  # type: ignore[misc]
    eng.dispose()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def redactor() -> DefaultSecretRedactor:
    return DefaultSecretRedactor()


@pytest.fixture
def cache_store(
    engine: Engine, clock: FixedClock, redactor: DefaultSecretRedactor
) -> SqlAlchemyProviderCacheStore:
    return SqlAlchemyProviderCacheStore(engine, clock, redactor)


@pytest.fixture
def health_store(engine: Engine) -> SqlAlchemyProviderHealthStore:
    return SqlAlchemyProviderHealthStore(engine)


@pytest.fixture
def rate_store(engine: Engine) -> SqlAlchemyProviderRateLimitStore:
    return SqlAlchemyProviderRateLimitStore(engine)


def test_reddit_state_samples_and_cooldown_roundtrip(
    engine: Engine, clock: FixedClock, redactor: DefaultSecretRedactor
) -> None:
    store = SqlAlchemyRedditStateStore(engine, clock, redactor)
    sample = USSentimentSample(
        instrument_id="equity:US:NVDA",
        source=USSentimentSource.REDDIT,
        published_at=NOW - timedelta(hours=1),
        text="NVDA bullish growth",
        direction=USSentimentDirection.BULLISH,
        label_origin=USSentimentLabelOrigin.DETERMINISTIC_INFERENCE,
        score=Decimal(1),
        likes=None,
        comments=None,
        url="https://reddit.test/post/1",
        classifier_version="reddit_lexicon_v1",
    )
    entry = RedditSampleCacheEntry(
        instrument_id=sample.instrument_id,
        config_key="01234567" + "89abcdef",
        samples=(sample,),
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )

    store.set_samples(entry)
    assert store.get_samples(entry.instrument_id, entry.config_key) == entry

    first = NOW + timedelta(minutes=5)
    later = NOW + timedelta(minutes=10)
    store.set_cooldown_until(first, updated_at=NOW)
    store.set_cooldown_until(NOW + timedelta(minutes=1), updated_at=NOW)
    assert store.get_cooldown_until() == first
    store.set_cooldown_until(later, updated_at=NOW)
    assert store.get_cooldown_until() == later


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


# --- Cache store ---


def test_cache_set_get_delete_roundtrip(
    cache_store: SqlAlchemyProviderCacheStore,
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
    # Idempotent delete
    cache_store.delete(entry.key)


def test_cache_get_returns_expired_entries(
    cache_store: SqlAlchemyProviderCacheStore,
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
    cache_store: SqlAlchemyProviderCacheStore,
) -> None:
    # Construct a credential-shaped value at runtime so scanners do not mistake
    # this deterministic fixture for a committed credential.
    secret = "sk-" + "abcdefghijklmnop"
    payload = json.dumps({"token": secret, "price": "1"})
    entry = _entry(payload=payload)
    cache_store.set(entry.key, entry)
    loaded = cache_store.get(entry.key)
    assert loaded is not None
    assert secret not in loaded.payload_json
    assert "***REDACTED***" in loaded.payload_json
    # Still valid JSON after redaction
    parsed = json.loads(loaded.payload_json)
    assert parsed["price"] == "1"


def test_cache_set_rejects_invalid_json_after_redaction(engine: Engine, clock: FixedClock) -> None:
    class BrokenRedactor:
        def redact_mapping(self, value: Any) -> dict[str, object]:
            return dict(value)

        def redact_text(self, value: str) -> str:
            return "not-valid-json{"

    store = SqlAlchemyProviderCacheStore(engine, clock, BrokenRedactor())  # type: ignore[arg-type]
    entry = _entry()
    with pytest.raises(DataContractError, match="valid JSON after redaction") as exc_info:
        store.set(entry.key, entry)
    err = exc_info.value
    assert err.__cause__ is None
    assert err.__context__ is None or err.__suppress_context__
    assert store.get(entry.key) is None


def test_cache_set_redactor_exception_sanitized(engine: Engine, clock: FixedClock) -> None:
    secret = "test-secret-redactor-boom"

    class ExplodingRedactor:
        def redact_mapping(self, value: Any) -> dict[str, object]:
            return dict(value)

        def redact_text(self, value: str) -> str:
            raise RuntimeError(f"redactor failed with {secret}")

    store = SqlAlchemyProviderCacheStore(engine, clock, ExplodingRedactor())  # type: ignore[arg-type]
    entry = _entry()
    with pytest.raises(DataContractError) as exc_info:
        store.set(entry.key, entry)
    err = exc_info.value
    assert err.details.get("field") == "payload_json"
    assert err.details.get("error_type") == "RuntimeError"
    assert err.__cause__ is None
    assert err.__context__ is None or err.__suppress_context__
    for node in _exception_chain_nodes(err):
        assert secret not in str(node)
        assert secret not in repr(node)


def test_cache_set_key_must_match_entry(
    cache_store: SqlAlchemyProviderCacheStore,
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
    assert other not in str(err.details)
    assert entry.key not in err.message


def test_cache_set_rejects_arbitrary_key_without_echo(
    cache_store: SqlAlchemyProviderCacheStore,
) -> None:
    entry = _entry()
    bad = "api_key=test-secret-not-a-cache-key"
    # entry.key is valid; pass a secret-shaped arbitrary key string.
    with pytest.raises(DataContractError) as exc_info:
        cache_store.set(bad, entry)
    err = exc_info.value
    assert err.details.get("field") == "key"
    assert bad not in err.message
    assert bad not in str(err.details)
    assert bad not in repr(err)
    assert "test-secret-not-a-cache-key" not in err.message
    assert "api_key=" not in err.message


def test_cache_set_rejects_key_entry_field_mismatches(
    cache_store: SqlAlchemyProviderCacheStore,
) -> None:
    # Category mismatch: key encodes MARKET_OHLCV, entry is MARKET_QUOTE.
    key = build_cache_key(
        Market.US,
        DataCategory.MARKET_OHLCV,
        "equity:US:NVDA",
        NOW,
        "get_quote|aabbccddeeff0011",
    )
    entry = CacheEntry(
        key=key,
        category=DataCategory.MARKET_QUOTE,
        market=Market.US,
        instrument_id="equity:US:NVDA",
        vendor=VendorId.MOCK_US,
        payload_json='{"price":"1"}',
        as_of=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        freshness=Freshness.FRESH,
    )
    with pytest.raises(DataContractError, match="cache key fields must match") as exc_info:
        cache_store.set(key, entry)
    assert key not in exc_info.value.message
    assert key not in str(exc_info.value.details)

    # Instrument mismatch
    key_inst = build_cache_key(
        Market.US,
        DataCategory.MARKET_QUOTE,
        "equity:US:AAPL",
        NOW,
        "get_quote|aabbccddeeff0011",
    )
    entry_inst = CacheEntry(
        key=key_inst,
        category=DataCategory.MARKET_QUOTE,
        market=Market.US,
        instrument_id="equity:US:NVDA",
        vendor=VendorId.MOCK_US,
        payload_json='{"price":"1"}',
        as_of=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        freshness=Freshness.FRESH,
    )
    with pytest.raises(DataContractError, match="cache key fields must match") as exc_info:
        cache_store.set(key_inst, entry_inst)
    assert key_inst not in str(exc_info.value.details)

    # Market mismatch (null instrument)
    key_mkt = build_cache_key(
        Market.A_SHARE,
        DataCategory.MARKET_QUOTE,
        None,
        NOW,
        "get_quote|aabbccddeeff0011",
    )
    entry_mkt = CacheEntry(
        key=key_mkt,
        category=DataCategory.MARKET_QUOTE,
        market=Market.US,
        instrument_id=None,
        vendor=VendorId.MOCK_US,
        payload_json='{"price":"1"}',
        as_of=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        freshness=Freshness.FRESH,
    )
    with pytest.raises(DataContractError, match="cache key fields must match") as exc_info:
        cache_store.set(key_mkt, entry_mkt)
    assert key_mkt not in str(exc_info.value.details)

    # as_of mismatch
    other_as_of = NOW - timedelta(days=1)
    key_as_of = build_cache_key(
        Market.US,
        DataCategory.MARKET_QUOTE,
        "equity:US:NVDA",
        other_as_of,
        "get_quote|aabbccddeeff0011",
    )
    entry_as_of = CacheEntry(
        key=key_as_of,
        category=DataCategory.MARKET_QUOTE,
        market=Market.US,
        instrument_id="equity:US:NVDA",
        vendor=VendorId.MOCK_US,
        payload_json='{"price":"1"}',
        as_of=NOW,
        fetched_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        freshness=Freshness.FRESH,
    )
    with pytest.raises(DataContractError, match="cache key fields must match") as exc_info:
        cache_store.set(key_as_of, entry_as_of)
    assert key_as_of not in str(exc_info.value.details)


def test_cache_get_delete_reject_invalid_secret_key_without_echo(
    cache_store: SqlAlchemyProviderCacheStore,
) -> None:
    secret_key = "api_key=test-secret-cache-lookup"
    for op in (cache_store.get, cache_store.delete):
        with pytest.raises(DataContractError) as exc_info:
            op(secret_key)
        err = exc_info.value
        assert err.details.get("field") == "key"
        assert secret_key not in err.message
        assert secret_key not in str(err.details)
        assert secret_key not in repr(err)
        assert "test-secret-cache-lookup" not in err.message
        assert "api_key=" not in err.message


def test_cache_set_rollback_on_commit_failure(
    engine: Engine,
    clock: FixedClock,
    redactor: DefaultSecretRedactor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyProviderCacheStore(engine, clock, redactor)
    entry = _entry()
    secret = "test-secret-leak-value"

    real_session_cls = Session

    class FailingSession(real_session_cls):  # type: ignore[misc,valid-type]
        def commit(self) -> None:  # type: ignore[override]
            raise RuntimeError(f"simulated commit failure with secret={secret}")

    monkeypatch.setattr(
        "infrastructure.persistence.provider_cache_store.Session",
        FailingSession,
    )
    with pytest.raises(PersistenceError) as exc_info:
        store.set(entry.key, entry)
    err = exc_info.value
    _assert_safe_persistence_error(
        err,
        forbidden_substrings=[secret, "simulated commit failure", "sk-"],
    )
    assert err.details.get("error_type") == "RuntimeError"

    # Restore real Session for verification
    monkeypatch.setattr(
        "infrastructure.persistence.provider_cache_store.Session",
        real_session_cls,
    )
    assert store.get(entry.key) is None


def test_cache_db_error_sanitized_no_url(
    clock: FixedClock, redactor: DefaultSecretRedactor, tmp_path: Path
) -> None:
    # Point at a path that cannot be a working sqlite file (directory as db path).
    bad_dir = tmp_path / "not_a_file"
    bad_dir.mkdir()
    eng = create_engine(f"sqlite:///{bad_dir}")
    store = SqlAlchemyProviderCacheStore(eng, clock, redactor)
    entry = _entry()
    with pytest.raises(PersistenceError) as exc_info:
        store.set(entry.key, entry)
    _assert_safe_persistence_error(
        exc_info.value,
        forbidden_substrings=[str(bad_dir), "password", "sk-"],
    )
    eng.dispose()


# --- Health store ---


def test_health_virtual_snapshot_no_write(
    health_store: SqlAlchemyProviderHealthStore, engine: Engine
) -> None:
    snap = health_store.get(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT)
    assert snap.state is HealthState.OK
    assert snap.success_count == 0
    assert snap.failure_count == 0
    assert snap.circuit_state is CircuitState.CLOSED
    assert snap.last_success_at is None
    assert snap.last_error_code is None
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM provider_health")).scalar()
    assert count == 0


def test_health_record_success_and_failure_preserve_counters(
    health_store: SqlAlchemyProviderHealthStore,
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
    assert snap.success_count == 1  # preserved
    assert snap.failure_count == 1
    assert snap.state is HealthState.ERROR
    assert snap.last_success_at == t1  # preserved
    assert snap.last_failure_at == t2
    assert snap.last_error_code == "PROVIDER_TIMEOUT_ERROR"

    health_store.record_success(v, c, t3)
    snap = health_store.get(v, c)
    assert snap.success_count == 2
    assert snap.failure_count == 1  # preserved
    assert snap.state is HealthState.OK
    assert snap.last_success_at == t3
    assert snap.last_failure_at == t2  # preserved
    assert snap.last_error_code is None


def test_health_circuit_projection_rules(
    health_store: SqlAlchemyProviderHealthStore,
) -> None:
    v, c = VendorId.MOCK_A_SHARE, DataCategory.MARKET_QUOTE
    t = NOW

    # No row: OPEN creates zero-count ERROR row
    health_store.set_circuit_state(v, c, CircuitState.OPEN, t)
    snap = health_store.get(v, c)
    assert snap.circuit_state is CircuitState.OPEN
    assert snap.state is HealthState.ERROR
    assert snap.success_count == 0

    # CLOSED does not wipe call results — "X" is a valid error_code
    health_store.record_failure(v, c, t + timedelta(seconds=1), "X")
    health_store.set_circuit_state(v, c, CircuitState.CLOSED, t + timedelta(seconds=2))
    snap = health_store.get(v, c)
    assert snap.circuit_state is CircuitState.CLOSED
    assert snap.state is HealthState.ERROR  # last failure result kept
    assert snap.failure_count == 1
    assert snap.last_error_code == "X"

    # HALF_OPEN while ERROR stays ERROR
    health_store.set_circuit_state(v, c, CircuitState.HALF_OPEN, t + timedelta(seconds=3))
    snap = health_store.get(v, c)
    assert snap.circuit_state is CircuitState.HALF_OPEN
    assert snap.state is HealthState.ERROR

    # Success then HALF_OPEN → DEGRADED
    health_store.record_success(v, c, t + timedelta(seconds=4))
    health_store.set_circuit_state(v, c, CircuitState.HALF_OPEN, t + timedelta(seconds=5))
    snap = health_store.get(v, c)
    assert snap.state is HealthState.DEGRADED
    assert snap.success_count >= 1


def test_health_rejects_naive_at(health_store: SqlAlchemyProviderHealthStore) -> None:
    naive = datetime(2026, 7, 17, 12, 0, 0)
    with pytest.raises(DataContractError, match="timezone-aware"):
        health_store.record_success(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, naive)
    with pytest.raises(DataContractError, match="timezone-aware"):
        health_store.record_failure(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, naive, "E")


@pytest.mark.parametrize(
    "bad_code",
    [
        "x",
        "lower",
        "has-dash",
        "api_key=sk-secret",
        "1BAD",
        "ERR CODE",
        "",
    ],
)
def test_health_rejects_invalid_error_code_without_echo(
    health_store: SqlAlchemyProviderHealthStore, bad_code: str
) -> None:
    with pytest.raises(DataContractError) as exc_info:
        health_store.record_failure(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, NOW, bad_code)
    err = exc_info.value
    assert err.details.get("field") == "error_code"
    if bad_code:
        assert bad_code not in err.message
        assert bad_code not in str(err.details)
        assert bad_code not in repr(err)
    assert "sk-secret" not in err.message
    assert "api_key=" not in err.message


def test_health_concurrent_success_and_failure_counts(
    health_store: SqlAlchemyProviderHealthStore,
) -> None:
    """Atomic upsert/increment must not lose concurrent success/failure counts."""
    n = 20
    barrier = threading.Barrier(n * 2)
    errors: list[BaseException] = []

    def _success(_: int) -> None:
        barrier.wait(timeout=10)
        health_store.record_success(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE, NOW)

    def _failure(i: int) -> None:
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

    assert errors == [], f"concurrent health updates failed: {errors!r}"
    snap = health_store.get(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE)
    assert snap.success_count == n
    assert snap.failure_count == n


def test_health_db_error_sanitized(tmp_path: Path) -> None:
    bad = tmp_path / "dir_as_db"
    bad.mkdir()
    eng = create_engine(f"sqlite:///{bad}")
    store = SqlAlchemyProviderHealthStore(eng)
    with pytest.raises(PersistenceError) as exc_info:
        store.record_success(VendorId.MOCK_US, DataCategory.NEWS, NOW)
    _assert_safe_persistence_error(
        exc_info.value,
        forbidden_substrings=[str(bad), "password", "sk-"],
    )
    eng.dispose()


# --- Rate limit store ---


def test_rate_limit_consume_and_get_roundtrip(
    rate_store: SqlAlchemyProviderRateLimitStore,
) -> None:
    v, c = VendorId.MOCK_US, DataCategory.MARKET_QUOTE
    window = NOW
    s1 = rate_store.consume(
        vendor=v,
        category=c,
        window_start=window,
        window_seconds=60,
        limit_count=100,
        at=NOW,
    )
    assert s1.request_count == 1
    assert s1.limit_count == 100
    assert s1.window_seconds == 60

    s2 = rate_store.consume(
        vendor=v,
        category=c,
        window_start=window,
        window_seconds=60,
        limit_count=100,
        at=NOW + timedelta(seconds=1),
    )
    assert s2.request_count == 2

    got = rate_store.get(v, c, window)
    assert got is not None
    assert got.request_count == 2
    assert got.updated_at == NOW + timedelta(seconds=1)


def test_rate_limit_window_isolation(
    rate_store: SqlAlchemyProviderRateLimitStore,
) -> None:
    v, c = VendorId.MOCK_US, DataCategory.MARKET_QUOTE
    w1 = NOW
    w2 = NOW + timedelta(seconds=60)
    rate_store.consume(
        vendor=v, category=c, window_start=w1, window_seconds=60, limit_count=5, at=NOW
    )
    rate_store.consume(
        vendor=v,
        category=c,
        window_start=w2,
        window_seconds=60,
        limit_count=5,
        at=NOW + timedelta(seconds=1),
    )
    assert rate_store.get(v, c, w1) is not None
    assert rate_store.get(v, c, w1).request_count == 1  # type: ignore[union-attr]
    assert rate_store.get(v, c, w2).request_count == 1  # type: ignore[union-attr]
    assert rate_store.get(v, c, NOW + timedelta(hours=1)) is None


def test_rate_limit_overwrites_limit_metadata(
    rate_store: SqlAlchemyProviderRateLimitStore,
) -> None:
    v, c = VendorId.EASTMONEY, DataCategory.MARKET_QUOTE
    window = NOW
    rate_store.consume(
        vendor=v, category=c, window_start=window, window_seconds=1, limit_count=1, at=NOW
    )
    snap = rate_store.consume(
        vendor=v,
        category=c,
        window_start=window,
        window_seconds=5,
        limit_count=10,
        at=NOW + timedelta(seconds=1),
    )
    assert snap.request_count == 2
    assert snap.window_seconds == 5
    assert snap.limit_count == 10


def test_rate_limit_rejects_nonpositive_and_naive(
    rate_store: SqlAlchemyProviderRateLimitStore,
) -> None:
    with pytest.raises(DataContractError, match="positive"):
        rate_store.consume(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_QUOTE,
            window_start=NOW,
            window_seconds=0,
            limit_count=1,
            at=NOW,
        )
    with pytest.raises(DataContractError, match="timezone-aware"):
        rate_store.consume(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_QUOTE,
            window_start=datetime(2026, 7, 17),
            window_seconds=1,
            limit_count=1,
            at=NOW,
        )


def test_rate_limit_concurrent_consume_stable(
    rate_store: SqlAlchemyProviderRateLimitStore,
) -> None:
    """Hard exit: concurrent consume must yield exact 1..n sequence, no skip."""
    v, c = VendorId.MOCK_US, DataCategory.MARKET_OHLCV
    window = NOW
    n = 20
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def _once(_: int) -> int:
        barrier.wait(timeout=10)
        snap = rate_store.consume(
            vendor=v,
            category=c,
            window_start=window,
            window_seconds=1,
            limit_count=1000,
            at=NOW,
        )
        return snap.request_count

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_once, i) for i in range(n)]
        counts: list[int] = []
        for fut in as_completed(futures):
            try:
                counts.append(fut.result())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    assert errors == [], f"concurrent consume failed: {errors!r}"
    final = rate_store.get(v, c, window)
    assert final is not None
    assert final.request_count == n
    assert sorted(counts) == list(range(1, n + 1))


def test_rate_limit_db_error_sanitized(tmp_path: Path) -> None:
    bad = tmp_path / "rl_dir"
    bad.mkdir()
    eng = create_engine(f"sqlite:///{bad}")
    store = SqlAlchemyProviderRateLimitStore(eng)
    with pytest.raises(PersistenceError) as exc_info:
        store.consume(
            vendor=VendorId.MOCK_US,
            category=DataCategory.MARKET_QUOTE,
            window_start=NOW,
            window_seconds=1,
            limit_count=1,
            at=NOW,
        )
    _assert_safe_persistence_error(
        exc_info.value,
        forbidden_substrings=[str(bad), "SuperSecret", "password"],
    )
    eng.dispose()


def test_ports_exported() -> None:
    from application.ports import (
        ProviderCacheStore,
        ProviderHealthStore,
        ProviderRateLimitStore,
    )

    assert ProviderCacheStore is not None
    assert ProviderHealthStore is not None
    assert ProviderRateLimitStore is not None


def test_provider_rate_limiter_sqlalchemy_store_integration(
    rate_store: SqlAlchemyProviderRateLimitStore,
) -> None:
    """D5b ProviderRateLimiter + D5a SqlAlchemyProviderRateLimitStore."""
    from infrastructure.providers.common.rate_limiter import ProviderRateLimiter

    clock = FixedClock(NOW)
    limiter = ProviderRateLimiter(rate_store, clock)
    d1 = limiter.check_and_consume(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE)
    assert d1.allowed is True
    assert d1.remaining == 0
    assert d1.limit_per_window == 1
    d2 = limiter.check_and_consume(VendorId.EASTMONEY, DataCategory.MARKET_QUOTE)
    assert d2.allowed is False
    assert d2.remaining == 0
    # Mock vendors keep high throughput defaults.
    for _ in range(3):
        dm = limiter.check_and_consume(VendorId.MOCK_US, DataCategory.MARKET_QUOTE)
        assert dm.allowed is True
        assert dm.limit_per_window == 1000


def test_cache_upsert_replaces_payload(
    cache_store: SqlAlchemyProviderCacheStore,
) -> None:
    entry = _entry(payload='{"v":1}')
    cache_store.set(entry.key, entry)
    updated = _entry(payload='{"v":2}', key=entry.key)
    cache_store.set(entry.key, updated)
    loaded = cache_store.get(entry.key)
    assert loaded is not None
    assert loaded.payload_json == '{"v":2}'
