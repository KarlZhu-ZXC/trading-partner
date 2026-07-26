"""Phase 1D D6b2: ProviderRouterEngine resilience orchestration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from typing import Any

import pytest

from application.dto.provider_resilience import RateLimitDecision
from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from conftest import FixedClock
from domain.common.enums import (
    AdjustmentMethod,
    AppEnvironment,
    AssetType,
    CacheDisposition,
    CircuitState,
    DataCategory,
    DataCriticality,
    Freshness,
    LogLevel,
    Market,
    ProviderAttemptOutcome,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderAuthenticationError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    TradingPartnerError,
)
from domain.instruments.models import Instrument
from domain.market.models import MarketBar, TechnicalIndicators, VerifiedMarketSnapshot
from infrastructure.config.settings import AppSettings
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.common.rate_limiter import ProviderRateLimiter
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
BAR_TS = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)
INSTRUMENT_ID = "equity:US:NVDA"
SECRET = "test-secret-malicious-value"
OP = "get_snapshot"
FP = "request-fingerprint-1"


# --- fakes -------------------------------------------------------------------


@dataclass
class _MemCache:
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    get_calls: int = 0
    set_calls: int = 0
    delete_calls: int = 0
    fail_get: bool = False
    fail_set: bool = False
    fail_delete: bool = False

    def get(self, key: str) -> CacheEntry | None:
        self.get_calls += 1
        if self.fail_get:
            raise RuntimeError(f"cache get boom {SECRET}")
        return self.entries.get(key)

    def set(self, key: str, entry: CacheEntry) -> None:
        self.set_calls += 1
        if self.fail_set:
            raise RuntimeError(f"cache set boom {SECRET}")
        self.entries[key] = entry

    def delete(self, key: str) -> None:
        self.delete_calls += 1
        if self.fail_delete:
            raise RuntimeError(f"cache delete boom {SECRET}")
        self.entries.pop(key, None)


@dataclass
class _MemHealth:
    successes: list[tuple[VendorId, DataCategory]] = field(default_factory=list)
    failures: list[tuple[VendorId, DataCategory, str]] = field(default_factory=list)
    circuit_states: list[tuple[VendorId, DataCategory, CircuitState]] = field(default_factory=list)
    fail_writes: bool = False
    fail_set_circuit: bool = False

    def record_success(self, vendor: VendorId, category: DataCategory, at: datetime) -> None:
        if self.fail_writes:
            raise RuntimeError(f"health success boom {SECRET}")
        self.successes.append((vendor, category))

    def record_failure(
        self,
        vendor: VendorId,
        category: DataCategory,
        at: datetime,
        error_code: str,
    ) -> None:
        if self.fail_writes:
            raise RuntimeError(f"health failure boom {SECRET}")
        self.failures.append((vendor, category, error_code))

    def set_circuit_state(
        self,
        vendor: VendorId,
        category: DataCategory,
        state: CircuitState,
        at: datetime,
    ) -> None:
        if self.fail_writes or self.fail_set_circuit:
            raise RuntimeError(f"health circuit boom {SECRET}")
        self.circuit_states.append((vendor, category, state))

    def get(self, vendor: VendorId, category: DataCategory) -> Any:
        raise NotImplementedError


@dataclass
class _MemRateStore:
    counts: dict[tuple[VendorId, DataCategory], int] = field(default_factory=dict)

    def consume(
        self,
        *,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
        window_seconds: int,
        limit_count: int,
        at: datetime,
    ) -> Any:
        from application.dto.provider_state import ProviderRateLimitSnapshot

        key = (vendor, category)
        self.counts[key] = self.counts.get(key, 0) + 1
        return ProviderRateLimitSnapshot(
            vendor=vendor,
            category=category,
            window_start=window_start,
            window_seconds=window_seconds,
            request_count=self.counts[key],
            limit_count=limit_count,
            updated_at=at,
        )


class _DenyRateLimiter:
    """Always deny without requiring a store."""

    def check_and_consume(self, vendor: VendorId, category: DataCategory) -> RateLimitDecision:
        return RateLimitDecision(
            allowed=False,
            remaining=0,
            reset_at=AS_OF + timedelta(seconds=1),
            limit_per_window=1,
        )


class _StubAdapter:
    def __init__(
        self,
        vendor_id: VendorId,
        *,
        configured: object = True,
        supported: object = True,
        handler: Callable[[], Awaitable[ProviderSuccess[Any]]] | None = None,
        raise_on_supports: BaseException | None = None,
        raise_on_configured: BaseException | None = None,
    ) -> None:
        self._vendor_id = vendor_id
        self._configured = configured
        self._supported = supported
        self._handler = handler
        self._raise_on_supports = raise_on_supports
        self._raise_on_configured = raise_on_configured
        self.call_count = 0

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        return self._vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> object:
        if self._raise_on_supports is not None:
            raise self._raise_on_supports
        return self._supported

    def is_configured(self) -> object:
        if self._raise_on_configured is not None:
            raise self._raise_on_configured
        return self._configured


def _instrument() -> Instrument:
    return Instrument(
        instrument_id=INSTRUMENT_ID,
        symbol="NVDA",
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
        country="US",
        mic="XNAS",
        multiplier=Decimal("1"),
        tick_size=Decimal("0.01"),
        lot_size=Decimal("1"),
        metadata_version=1,
    )


def _snapshot(
    *,
    bar_ts: datetime = BAR_TS,
    session: TradingSession = TradingSession.REGULAR,
) -> VerifiedMarketSnapshot:
    bar = MarketBar(
        timestamp=bar_ts,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("99"),
        close=Decimal("105"),
        volume=Decimal("1000"),
    )
    return VerifiedMarketSnapshot(
        instrument=_instrument(),
        requested_as_of=AS_OF,
        latest_market_row=bar,
        indicators=TechnicalIndicators.empty(),
        recent_closes=(Decimal("100"), Decimal("102"), Decimal("105")),
        adjustment=AdjustmentMethod.NONE,
        session=session,
        algorithm_version="mock-1.0.0",
    )


def _meta(
    vendor: VendorId = VendorId.MOCK_US,
    **overrides: object,
) -> ProviderResultMeta:
    base: dict[str, object] = {
        "vendor": vendor,
        "category": DataCategory.MARKET_SNAPSHOT,
        "role": SourceRole.SUPPLEMENTAL,  # engine must rewrite
        "as_of": AS_OF,
        "fetched_at": AS_OF,
        "freshness": Freshness.FRESH,
        "session": TradingSession.REGULAR,
        "latency_ms": 5,
        "cache_disposition": CacheDisposition.HIT,  # engine must rewrite
        "adjustment": AdjustmentMethod.NONE,
        "data_delay_seconds": 0,
        "warnings": ("MOCK_DATA",),
    }
    base.update(overrides)
    return ProviderResultMeta(**base)  # type: ignore[arg-type]


def _success(
    vendor: VendorId = VendorId.MOCK_US,
    *,
    snapshot: VerifiedMarketSnapshot | None = None,
    meta: ProviderResultMeta | None = None,
) -> ProviderSuccess[VerifiedMarketSnapshot]:
    return ProviderSuccess(
        value=snapshot if snapshot is not None else _snapshot(),
        meta=meta if meta is not None else _meta(vendor),
    )


class _SnapshotCodec:
    """Small in-process codec fixture; production snapshot mocks were retired."""

    codec_id = "test.snapshot.v1"
    _sequence = count(1)
    _values: dict[str, ProviderSuccess[VerifiedMarketSnapshot]] = {}

    def encode(self, success: ProviderSuccess[VerifiedMarketSnapshot]) -> str:
        token = f"snapshot-{next(self._sequence)}"
        self._values[token] = success
        return json.dumps({"codec": self.codec_id, "token": token})

    def decode(self, entry: CacheEntry) -> ProviderSuccess[VerifiedMarketSnapshot]:
        payload = json.loads(entry.payload_json)
        if payload.get("codec") != self.codec_id:
            raise DataContractError("test cache codec mismatch")
        success = self._values[payload["token"]]
        return replace(
            success,
            meta=replace(success.meta, cache_disposition=CacheDisposition.HIT),
        )


def _settings(**overrides: object) -> AppSettings:
    base: dict[str, object] = {
        "app_name": "tp",
        "app_env": AppEnvironment.TEST,
        "log_level": LogLevel.DEBUG,
        "database_url": "sqlite:////tmp/d6b2-router-test.db",
        "mcp_server_name": "tp",
        "default_timezone": "UTC",
        "provider_timeout_seconds": 30.0,
        "provider_timeout_market_seconds": 15.0,
        "provider_retry_max_attempts": 2,
        "provider_retry_base_delay_seconds": 0.0,
        "provider_retry_max_delay_seconds": 0.0,
        "enable_provider_cache": True,
        "enable_circuit_breaker": True,
        "auth_failure_fallback": False,
        "stale_guard_max_age_seconds": 86400,
        "stale_guard_respect_session": True,
        "stale_guard_allow_closed_last_bar": True,
        "circuit_failure_threshold": 5,
        "circuit_recovery_timeout_seconds": 60.0,
        "circuit_half_open_max_calls": 1,
    }
    base.update(overrides)
    return AppSettings(**base)  # type: ignore[arg-type]


def _engine(
    *,
    registry: VendorRegistry | None = None,
    cache: _MemCache | None = None,
    health: _MemHealth | None = None,
    rate_limiter: Any | None = None,
    clock: FixedClock | None = None,
    settings: AppSettings | None = None,
    circuit: CircuitBreaker | None = None,
) -> tuple[ProviderRouterEngine, _MemCache, _MemHealth, FixedClock, VendorRegistry]:
    clock = clock or FixedClock(AS_OF)
    cache = cache or _MemCache()
    health = health or _MemHealth()
    registry = registry or VendorRegistry()
    settings = settings or _settings()
    if rate_limiter is None:
        rate_limiter = ProviderRateLimiter(_MemRateStore(), clock)
    if circuit is None:
        circuit = CircuitBreaker(
            clock,
            failure_threshold=settings.circuit_failure_threshold,
            recovery_timeout_seconds=settings.circuit_recovery_timeout_seconds,
            half_open_max_calls=settings.circuit_half_open_max_calls,
        )
    engine = ProviderRouterEngine(
        registry=registry,
        cache_store=cache,
        health_store=health,
        rate_limiter=rate_limiter,
        circuit_breaker=circuit,
        clock=clock,
        settings=settings,
    )
    return engine, cache, health, clock, registry


def _register(
    registry: VendorRegistry,
    vendor: VendorId,
    **kwargs: Any,
) -> _StubAdapter:
    adapter = _StubAdapter(vendor, **kwargs)
    registry.register(vendor, adapter)
    return adapter


async def _run(
    engine: ProviderRouterEngine,
    *,
    chain: tuple[VendorId, ...] = (VendorId.MOCK_US,),
    category: DataCategory = DataCategory.MARKET_SNAPSHOT,
    criticality: DataCriticality = DataCriticality.CORE,
    call: Callable[[Any], Awaitable[ProviderSuccess[Any]]] | None = None,
    bypass_cache: bool = False,
    cache_codec: Any | None = ...,
    result_validator: Callable[[ProviderSuccess[Any]], None] | None = None,
    operation_name: str = OP,
    request_fingerprint: str = FP,
    instrument: Instrument | None = ...,
) -> Any:
    if cache_codec is ...:
        cache_codec = _SnapshotCodec()
    if instrument is ...:
        instrument = _instrument()

    async def _default_call(adapter: Any) -> ProviderSuccess[VerifiedMarketSnapshot]:
        assert isinstance(adapter, _StubAdapter)
        adapter.call_count += 1
        if adapter._handler is not None:
            return await adapter._handler()
        return _success(adapter.vendor_id)

    return await engine.execute(
        market=Market.US,
        category=category,
        chain=chain,
        criticality=criticality,
        call=call if call is not None else _default_call,
        operation_name=operation_name,
        request_fingerprint=request_fingerprint,
        instrument=instrument,
        as_of=AS_OF,
        bypass_cache=bypass_cache,
        cache_codec=cache_codec,
        result_validator=result_validator,
    )


def _codes(result: Any) -> list[str]:
    return [w.code for w in result.warnings]


def _blob(err: BaseException) -> str:
    parts = [str(err), repr(err)]
    if isinstance(err, TradingPartnerError):
        parts.append(repr(err.details))
        parts.append(str(err.details))
    return "".join(parts)


# --- fingerprint / empty chain -----------------------------------------------


@pytest.mark.asyncio
async def test_request_metadata_contract_rejects_invalid_inputs_without_echo() -> None:
    cases = [
        ("operation_name", {"operation_name": "bad name with space"}),
        ("request_fingerprint", {"request_fingerprint": ""}),
    ]
    for invalid_field, overrides in cases:
        engine, *_ = _engine()
        with pytest.raises(DataContractError) as exc_info:
            await _run(engine, **overrides)
        assert SECRET not in _blob(exc_info.value)
        assert exc_info.value.details.get("field") == invalid_field
        if invalid_field == "operation_name":
            assert "bad name" not in _blob(exc_info.value)
        if invalid_field == "request_fingerprint":
            assert exc_info.value.details.get("value") is None


@pytest.mark.asyncio
async def test_empty_chain_returns_not_configured() -> None:
    engine, *_ = _engine()
    result = await _run(engine, chain=())
    assert result.ok is False
    assert isinstance(result.error, ProviderNotConfigured)
    assert result.attempts == ()


@pytest.mark.asyncio
async def test_optional_empty_chain_adds_optional_warning() -> None:
    engine, *_ = _engine()
    result = await _run(
        engine,
        chain=(),
        criticality=DataCriticality.OPTIONAL,
        category=DataCategory.NEWS,
        cache_codec=None,
    )
    assert result.ok is False
    assert "OPTIONAL_DATA_UNAVAILABLE" in _codes(result)
    assert result.criticality is DataCriticality.OPTIONAL


# --- success / meta rewrite / fallback ---------------------------------------


@pytest.mark.asyncio
async def test_primary_success_rewrites_role_and_cache_disposition() -> None:
    engine, cache, health, _, registry = _engine()
    _register(registry, VendorId.MOCK_US)
    result = await _run(engine, cache_codec=None, bypass_cache=True)
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.role is SourceRole.PRIMARY
    assert result.meta.cache_disposition is CacheDisposition.BYPASS
    assert result.attempts[0].outcome is ProviderAttemptOutcome.SUCCESS
    assert cache.get_calls == 0
    assert cache.set_calls == 0
    assert health.successes == [(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT)]


@pytest.mark.asyncio
async def test_fallback_success_after_primary_failure() -> None:
    engine, _, _, _, registry = _engine()

    async def _fail() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise ProviderUnavailableError("down", details={"vendor": "mock_us"})

    _register(registry, VendorId.MOCK_US, handler=_fail)
    _register(registry, VendorId.NULL)
    result = await _run(
        engine,
        chain=(VendorId.MOCK_US, VendorId.NULL),
        cache_codec=None,
    )
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.vendor is VendorId.NULL
    assert result.meta.role is SourceRole.FALLBACK
    assert "FALLBACK_VENDOR_USED" in _codes(result)
    assert result.attempts[0].outcome is ProviderAttemptOutcome.FAILURE
    assert result.attempts[1].outcome is ProviderAttemptOutcome.SUCCESS


@pytest.mark.asyncio
async def test_auth_failure_legacy_and_fallback() -> None:
    for fallback in (False, True):
        settings = _settings(auth_failure_fallback=fallback)
        engine, _, _, _, registry = _engine(settings=settings)

        async def _auth() -> ProviderSuccess[VerifiedMarketSnapshot]:
            raise ProviderAuthenticationError("auth", details={"vendor": "mock_us"})

        primary = _register(registry, VendorId.MOCK_US, handler=_auth)
        secondary = _register(registry, VendorId.NULL)

        result = await _run(
            engine,
            chain=(VendorId.MOCK_US, VendorId.NULL),
            cache_codec=None,
        )

        if fallback:
            assert result.ok is True
            assert result.meta is not None
            assert result.meta.vendor is VendorId.NULL
            assert "FALLBACK_VENDOR_USED" in _codes(result)
            assert result.attempts[0].outcome is ProviderAttemptOutcome.AUTH_ERROR
            assert result.attempts[1].outcome is ProviderAttemptOutcome.SUCCESS
            assert secondary.call_count == 1
            assert primary.call_count >= 1
        else:
            assert result.ok is False
            assert isinstance(result.error, ProviderAuthenticationError)
            assert result.attempts[0].outcome is ProviderAttemptOutcome.AUTH_ERROR
            assert len(result.attempts) == 1
            assert primary.call_count >= 1
            assert secondary.call_count == 0


# --- skips -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_behavior_for_unsupported_and_configured_vendors() -> None:
    scenarios = [
        {
            "name": "missing_registry",
            "chain": (VendorId.ALPHA_VANTAGE, VendorId.MOCK_US),
            "registrations": [],
            "outcomes": (ProviderAttemptOutcome.SKIPPED_UNSUPPORTED,),
            "expect_partial": True,
        },
        {
            "name": "not_configured_then_unsupported",
            "chain": (VendorId.MOCK_US, VendorId.NULL),
            "registrations": [
                (VendorId.MOCK_US, {"configured": False}),
                (VendorId.NULL, {"supported": False}),
            ],
            "outcomes": (
                ProviderAttemptOutcome.SKIPPED_NOT_CONFIGURED,
                ProviderAttemptOutcome.SKIPPED_UNSUPPORTED,
            ),
            "expect_partial": False,
        },
    ]

    for scenario in scenarios:
        engine, _, _, _, registry = _engine()
        for vendor, kwargs in scenario["registrations"]:
            _register(registry, vendor, **kwargs)

        result = await _run(
            engine,
            chain=scenario["chain"],  # type: ignore[arg-type]
            cache_codec=None,
        )
        assert result.ok is False
        for idx, outcome in enumerate(scenario["outcomes"]):
            assert result.attempts[idx].outcome is outcome
        assert ("PARTIAL_VENDOR_CHAIN" in _codes(result)) is scenario["expect_partial"]


@pytest.mark.asyncio
async def test_circuit_states_skip_or_ignore_open_state() -> None:
    clock = FixedClock(AS_OF)
    circuit = CircuitBreaker(clock, failure_threshold=1, recovery_timeout_seconds=60.0)
    permit = circuit.before_call(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT)
    circuit.record_failure(permit)
    assert circuit.state(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT) is CircuitState.OPEN

    for disabled in (False, True):
        if disabled:
            engine, _, _, _, registry = _engine(
                clock=clock,
                circuit=circuit,
                settings=_settings(enable_circuit_breaker=False),
            )
        else:
            engine, _, _, _, registry = _engine(clock=clock, circuit=circuit)
        adapter = _register(registry, VendorId.MOCK_US)
        result = await _run(engine, cache_codec=None)
        if disabled:
            assert result.ok is True
            assert result.meta is not None
            assert result.meta.vendor is VendorId.MOCK_US
            assert adapter.call_count == 1
        else:
            assert result.ok is False
            assert result.attempts[0].outcome is ProviderAttemptOutcome.SKIPPED_CIRCUIT_OPEN
            assert "CIRCUIT_OPEN_SKIPPED" in _codes(result)
            assert adapter.call_count == 0


# --- timeout / retry / unknown wrap ------------------------------------------


@pytest.mark.asyncio
async def test_timeout_maps_to_timeout_outcome_and_retries() -> None:
    engine, _, health, _, registry = _engine(
        settings=_settings(provider_retry_max_attempts=2, provider_timeout_market_seconds=0.05)
    )
    calls = {"n": 0}

    async def _hang() -> ProviderSuccess[VerifiedMarketSnapshot]:
        calls["n"] += 1
        await asyncio.sleep(1.0)
        return _success()

    _register(registry, VendorId.MOCK_US, handler=_hang)
    result = await _run(engine, cache_codec=None)
    assert result.ok is False
    assert isinstance(result.error, ProviderTimeoutError)
    assert result.attempts[0].outcome is ProviderAttemptOutcome.TIMEOUT
    assert calls["n"] == 2
    assert len(health.failures) == 2


@pytest.mark.asyncio
async def test_unknown_exceptions_and_surface_errors_map_to_unavailable() -> None:
    for use_surface in (False, True):
        engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))
        if use_surface:
            _register(
                registry,
                VendorId.MOCK_US,
                raise_on_configured=RuntimeError(f"cfg {SECRET}"),
            )
        else:

            async def _boom() -> ProviderSuccess[VerifiedMarketSnapshot]:
                raise RuntimeError(f"provider raw {SECRET}")

            _register(registry, VendorId.MOCK_US, handler=_boom)
        result = await _run(engine, cache_codec=None)
        assert result.ok is False
        assert isinstance(result.error, ProviderUnavailableError)
        assert result.error.code == "PROVIDER_UNAVAILABLE_ERROR"
        assert SECRET not in _blob(result.error)
        assert result.error.__cause__ is None


@pytest.mark.asyncio
async def test_no_market_data_continues_fallback() -> None:
    engine, _, _, _, registry = _engine()

    async def _empty() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise NoMarketData("none", details={"vendor": "mock_us"})

    _register(registry, VendorId.MOCK_US, handler=_empty)
    _register(registry, VendorId.NULL)
    result = await _run(
        engine,
        chain=(VendorId.MOCK_US, VendorId.NULL),
        cache_codec=None,
    )
    assert result.ok is True
    assert result.attempts[0].outcome is ProviderAttemptOutcome.NO_DATA


# --- contract / stale / meta coherence ---------------------------------------


@pytest.mark.asyncio
async def test_meta_vendor_mismatch_is_contract_error() -> None:
    engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))

    async def _bad() -> ProviderSuccess[VerifiedMarketSnapshot]:
        return _success(VendorId.NULL)  # wrong vendor for MOCK_US slot

    _register(registry, VendorId.MOCK_US, handler=_bad)
    result = await _run(engine, cache_codec=None)
    assert result.ok is False
    assert isinstance(result.error, DataContractError)
    assert result.attempts[0].outcome is ProviderAttemptOutcome.CONTRACT_ERROR


@pytest.mark.asyncio
async def test_stale_guard_behavior() -> None:
    old_bar = AS_OF - timedelta(hours=2)

    async def _stale() -> ProviderSuccess[VerifiedMarketSnapshot]:
        return _success(
            VendorId.MOCK_US,
            snapshot=_snapshot(bar_ts=old_bar),
        )

    async def _fresh() -> ProviderSuccess[VerifiedMarketSnapshot]:
        return _success(
            VendorId.NULL,
            snapshot=_snapshot(bar_ts=AS_OF - timedelta(seconds=10)),
        )

    engine, _, _, _, registry = _engine(
        settings=_settings(
            provider_retry_max_attempts=1,
            stale_guard_max_age_seconds=60,
            stale_guard_respect_session=False,
            stale_guard_allow_closed_last_bar=True,
        )
    )
    _register(registry, VendorId.MOCK_US, handler=_stale)
    _register(registry, VendorId.NULL, handler=_fresh)
    result = await _run(
        engine,
        chain=(VendorId.MOCK_US, VendorId.NULL),
        cache_codec=None,
    )
    assert result.ok is True
    assert result.attempts[0].outcome is ProviderAttemptOutcome.CONTRACT_ERROR
    assert "STALE_DATA_REJECTED" in _codes(result)
    assert result.meta is not None
    assert result.meta.vendor is VendorId.NULL

    engine, _, _, _, registry = _engine(
        settings=_settings(
            provider_retry_max_attempts=1,
            stale_guard_max_age_seconds=1,
            stale_guard_respect_session=False,
        )
    )

    async def _news(_adapter: Any) -> ProviderSuccess[str]:
        return ProviderSuccess(
            value="headline",
            meta=_meta(
                VendorId.MOCK_US,
                category=DataCategory.NEWS,
                cache_disposition=CacheDisposition.MISS,
            ),
        )

    _register(registry, VendorId.MOCK_US)
    result = await _run(
        engine,
        category=DataCategory.NEWS,
        cache_codec=None,
        call=_news,
        instrument=None,
    )
    assert result.ok is True


# --- cache -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_then_write_on_success() -> None:
    engine, cache, _, _, registry = _engine()
    _register(registry, VendorId.MOCK_US)
    result = await _run(engine)
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.cache_disposition is CacheDisposition.MISS
    assert cache.get_calls == 1
    assert cache.set_calls == 1
    assert len(cache.entries) == 1


@pytest.mark.asyncio
async def test_cache_hit_serves_without_adapter_call() -> None:
    codec = _SnapshotCodec()
    engine, cache, _, clock, registry = _engine()
    adapter = _register(registry, VendorId.MOCK_US)
    # Seed via first call
    first = await _run(engine, cache_codec=codec)
    assert first.ok is True
    assert adapter.call_count == 1
    key = next(iter(cache.entries))
    # Second call should hit
    second = await _run(engine, cache_codec=codec)
    assert second.ok is True
    assert adapter.call_count == 1  # no new call
    assert second.attempts == ()
    assert "CACHE_SERVED" in _codes(second)
    assert second.meta is not None
    assert second.meta.cache_disposition is CacheDisposition.HIT
    assert key in cache.entries


@pytest.mark.asyncio
async def test_cache_bypass_modes() -> None:
    for settings, cache_codec, bypass_cache in [
        (None, None, False),
        (None, None, True),
        (_settings(enable_provider_cache=False), None, False),
    ]:
        engine, cache, _, _, registry = _engine(
            **({"settings": settings} if settings is not None else {})
        )
        _register(registry, VendorId.MOCK_US)

        kwargs = {"cache_codec": cache_codec if cache_codec is not None else None}
        if bypass_cache:
            kwargs["bypass_cache"] = True

        result = await _run(engine, **kwargs)
        assert result.ok is True
        assert result.meta is not None
        assert result.meta.cache_disposition is CacheDisposition.BYPASS
        assert cache.get_calls == 0
        assert cache.set_calls == 0


@pytest.mark.asyncio
async def test_cache_failures_continue_workflow() -> None:
    # get() failure does not poison lookup path
    cache = _MemCache(fail_get=True)
    engine, _, _, _, registry = _engine(cache=cache)
    _register(registry, VendorId.MOCK_US)
    result = await _run(engine)
    assert result.ok is True
    assert "CACHE_UNAVAILABLE" in _codes(result)
    assert SECRET not in "".join(w.message for w in result.warnings)

    # expired entries are deleted and workflow proceeds via adapter
    codec = _SnapshotCodec()
    clock = FixedClock(AS_OF)
    engine, cache, _, _, registry = _engine(clock=clock)
    adapter = _register(registry, VendorId.MOCK_US)
    await _run(engine, cache_codec=codec)
    assert adapter.call_count == 1
    for key, entry in list(cache.entries.items()):
        cache.entries[key] = replace(
            entry,
            fetched_at=AS_OF - timedelta(seconds=30),
            expires_at=AS_OF - timedelta(seconds=1),
        )
    clock.set(AS_OF)
    result = await _run(engine, cache_codec=codec)
    assert result.ok is True
    assert adapter.call_count == 2
    assert result.attempts[0].outcome is ProviderAttemptOutcome.SUCCESS

    # corrupt payload rejects cached entry and continues into adapter
    engine, cache, _, _, registry = _engine()
    adapter = _register(registry, VendorId.MOCK_US)
    await _run(engine, cache_codec=codec)
    for key, entry in list(cache.entries.items()):
        cache.entries[key] = replace(entry, payload_json='{"codec":"nope"}')
    result = await _run(engine, cache_codec=codec)
    assert result.ok is True
    assert "CACHE_ENTRY_REJECTED" in _codes(result)
    assert adapter.call_count == 2


# --- health nonblocking / result_validator -----------------------------------


@pytest.mark.asyncio
async def test_health_write_failure_does_not_block_success() -> None:
    health = _MemHealth(fail_writes=True)
    engine, _, _, _, registry = _engine(health=health)
    _register(registry, VendorId.MOCK_US)
    result = await _run(engine, cache_codec=None)
    assert result.ok is True
    assert "PROVIDER_HEALTH_UNAVAILABLE" in _codes(result)
    assert SECRET not in "".join(w.message + str(w.details) for w in result.warnings)


@pytest.mark.asyncio
async def test_result_validator_runs_before_success() -> None:
    engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))
    _register(registry, VendorId.MOCK_US)
    seen: list[str] = []

    def _validator(success: ProviderSuccess[Any]) -> None:
        seen.append("v")
        raise DataContractError(
            "validator rejected",
            details={"field": "value", "rule": "test"},
        )

    result = await _run(engine, cache_codec=None, result_validator=_validator)
    assert result.ok is False
    assert isinstance(result.error, DataContractError)
    assert seen == ["v"]


@pytest.mark.asyncio
async def test_error_priority_prefers_data_contract_over_timeout() -> None:
    engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))

    async def _timeout() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise ProviderTimeoutError("t", details={"vendor": "mock_us"})

    async def _contract() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise DataContractError("c", details={"field": "x", "rule": "y"})

    _register(registry, VendorId.MOCK_US, handler=_timeout)
    _register(registry, VendorId.NULL, handler=_contract)
    result = await _run(
        engine,
        chain=(VendorId.MOCK_US, VendorId.NULL),
        cache_codec=None,
    )
    assert result.ok is False
    assert isinstance(result.error, DataContractError)


@pytest.mark.asyncio
async def test_core_exhaustion_has_no_optional_warning() -> None:
    engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))

    async def _fail() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise ProviderUnavailableError("down", details={"vendor": "mock_us"})

    _register(registry, VendorId.MOCK_US, handler=_fail)
    result = await _run(engine, cache_codec=None)
    assert result.ok is False
    assert "OPTIONAL_DATA_UNAVAILABLE" not in _codes(result)


@pytest.mark.asyncio
async def test_warning_details_only_stable_keys() -> None:
    engine, _, _, _, registry = _engine()

    async def _fail() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise ProviderUnavailableError(
            f"down {SECRET}", details={"vendor": "mock_us", "token": SECRET}
        )

    _register(registry, VendorId.MOCK_US, handler=_fail)
    _register(registry, VendorId.NULL)
    result = await _run(
        engine,
        chain=(VendorId.MOCK_US, VendorId.NULL),
        cache_codec=None,
    )
    assert result.ok is True
    for w in result.warnings:
        assert set(w.details.keys()) <= {"vendor", "category", "operation_name"}
        assert SECRET not in w.message
        assert SECRET not in str(w.details)


@pytest.mark.asyncio
async def test_one_attempt_record_per_vendor_with_nonnegative_duration() -> None:
    engine, _, _, _, registry = _engine(
        settings=_settings(provider_retry_max_attempts=2, provider_timeout_market_seconds=0.05)
    )

    async def _hang() -> ProviderSuccess[VerifiedMarketSnapshot]:
        await asyncio.sleep(1.0)
        return _success()

    _register(registry, VendorId.MOCK_US, handler=_hang)
    result = await _run(engine, cache_codec=None)
    assert len(result.attempts) == 1
    assert result.attempts[0].duration_ms >= 0
    assert result.attempts[0].vendor is VendorId.MOCK_US


@pytest.mark.asyncio
async def test_appsettings_structurally_satisfies_router_settings() -> None:
    """AppSettings must satisfy ProviderRouterSettings without new config keys."""
    settings = _settings()
    engine, _, _, _, registry = _engine(settings=settings)
    _register(registry, VendorId.MOCK_US)
    result = await _run(engine, cache_codec=None)
    assert result.ok is True
    # Structural use of Protocol methods/fields already exercised above.
    assert settings.enable_provider_cache is True
    assert settings.timeout_for(DataCategory.MARKET_SNAPSHOT) > 0
    assert settings.cache_ttl_for(DataCategory.MARKET_SNAPSHOT) > 0


@pytest.mark.asyncio
async def test_rate_limit_paths_are_skips() -> None:
    clock = FixedClock(AS_OF)
    circuit = CircuitBreaker(clock, failure_threshold=1)
    engine, _, health, _, registry = _engine(
        clock=clock,
        circuit=circuit,
        rate_limiter=_DenyRateLimiter(),
    )
    adapter = _register(registry, VendorId.MOCK_US)
    result = await _run(engine, cache_codec=None)
    assert result.ok is False
    assert result.attempts[0].outcome is ProviderAttemptOutcome.SKIPPED_RATE_LIMITED
    assert "RATE_LIMIT_DEGRADED" in _codes(result)
    assert adapter.call_count == 0
    assert health.failures == []
    assert circuit.state(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT) is CircuitState.CLOSED

    engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))

    async def _rl() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise ProviderRateLimitError("rl", details={"vendor": "mock_us"})

    _register(registry, VendorId.MOCK_US, handler=_rl)
    result = await _run(engine, cache_codec=None)
    assert result.ok is False
    assert result.attempts[0].outcome is ProviderAttemptOutcome.SKIPPED_RATE_LIMITED
    assert "RATE_LIMIT_DEGRADED" in _codes(result)
    assert isinstance(result.error, ProviderRateLimitError)


# --- D6b2 acceptance defects: cancel permit / circuit projection / exact bool -


@pytest.mark.asyncio
async def test_cancellation_after_permit_records_failure_and_opens_breaker() -> None:
    """Issued permit must be recorded once on CancelledError; threshold=1 → OPEN."""
    clock = FixedClock(AS_OF)
    circuit = CircuitBreaker(clock, failure_threshold=1, recovery_timeout_seconds=60.0)
    engine, _, health, _, registry = _engine(
        clock=clock,
        circuit=circuit,
        settings=_settings(provider_retry_max_attempts=1),
    )
    admitted = asyncio.Event()

    async def _block() -> ProviderSuccess[VerifiedMarketSnapshot]:
        admitted.set()
        await asyncio.Event().wait()  # block until cancelled
        return _success()

    _register(registry, VendorId.MOCK_US, handler=_block)
    task = asyncio.create_task(_run(engine, cache_codec=None))
    await admitted.wait()
    # Handler started ⇒ rate limit + before_call already issued a permit.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert circuit.state(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT) is CircuitState.OPEN
    # Health failure uses stable safe code (CancelledError is not TradingPartnerError).
    assert health.failures == [
        (VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, "PROVIDER_UNAVAILABLE_ERROR")
    ]
    assert health.circuit_states == [
        (VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, CircuitState.OPEN)
    ]


@pytest.mark.asyncio
async def test_success_projects_circuit_state_closed() -> None:
    health = _MemHealth()
    engine, _, _, _, registry = _engine(health=health)
    _register(registry, VendorId.MOCK_US)
    result = await _run(engine, cache_codec=None)
    assert result.ok is True
    assert health.circuit_states == [
        (VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, CircuitState.CLOSED)
    ]
    assert health.successes == [(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT)]


@pytest.mark.asyncio
async def test_failure_projects_circuit_state_open() -> None:
    clock = FixedClock(AS_OF)
    circuit = CircuitBreaker(clock, failure_threshold=1, recovery_timeout_seconds=60.0)
    health = _MemHealth()
    engine, _, _, _, registry = _engine(
        clock=clock,
        circuit=circuit,
        health=health,
        settings=_settings(provider_retry_max_attempts=1),
    )

    async def _fail() -> ProviderSuccess[VerifiedMarketSnapshot]:
        raise ProviderUnavailableError("down", details={"vendor": "mock_us"})

    _register(registry, VendorId.MOCK_US, handler=_fail)
    result = await _run(engine, cache_codec=None)
    assert result.ok is False
    assert circuit.state(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT) is CircuitState.OPEN
    assert health.circuit_states == [
        (VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, CircuitState.OPEN)
    ]
    assert health.failures == [
        (VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT, "PROVIDER_UNAVAILABLE_ERROR")
    ]


@pytest.mark.asyncio
async def test_circuit_projection_failure_is_nonblocking() -> None:
    health = _MemHealth(fail_set_circuit=True)
    engine, _, _, _, registry = _engine(health=health)
    _register(registry, VendorId.MOCK_US)
    result = await _run(engine, cache_codec=None)
    assert result.ok is True
    assert "PROVIDER_HEALTH_UNAVAILABLE" in _codes(result)
    # Projection failed; record_success still best-effort and may succeed.
    assert health.successes == [(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT)]
    assert health.circuit_states == []
    blob = "".join(w.message + str(w.details) for w in result.warnings)
    assert SECRET not in blob
    # Dedup: single warning even if later health paths also fail.
    assert _codes(result).count("PROVIDER_HEALTH_UNAVAILABLE") == 1


@pytest.mark.asyncio
async def test_circuit_projection_failure_does_not_alter_cancellation() -> None:
    clock = FixedClock(AS_OF)
    circuit = CircuitBreaker(clock, failure_threshold=1, recovery_timeout_seconds=60.0)
    health = _MemHealth(fail_set_circuit=True)
    engine, _, _, _, registry = _engine(
        clock=clock,
        circuit=circuit,
        health=health,
        settings=_settings(provider_retry_max_attempts=1),
    )
    admitted = asyncio.Event()

    async def _block() -> ProviderSuccess[VerifiedMarketSnapshot]:
        admitted.set()
        await asyncio.Event().wait()
        return _success()

    _register(registry, VendorId.MOCK_US, handler=_block)
    task = asyncio.create_task(_run(engine, cache_codec=None))
    await admitted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    # Breaker still recorded failure despite projection store error.
    assert circuit.state(VendorId.MOCK_US, DataCategory.MARKET_SNAPSHOT) is CircuitState.OPEN


@pytest.mark.asyncio
async def test_exact_bool_contract_enforcement_for_configured_and_supports() -> None:
    cases = (
        ("configured", {"configured": f"yes-{SECRET}"}, ProviderAttemptOutcome.CONTRACT_ERROR),
        ("supports", {"supported": ""}, ProviderAttemptOutcome.CONTRACT_ERROR),
        ("configured_false", {"configured": False}, ProviderAttemptOutcome.SKIPPED_NOT_CONFIGURED),
    )

    for mode, kwargs, expected in cases:
        engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))
        _register(registry, VendorId.MOCK_US, **kwargs)
        result = await _run(engine, cache_codec=None)
        assert result.ok is False

        if mode == "configured_false":
            assert result.attempts[0].outcome is ProviderAttemptOutcome.SKIPPED_NOT_CONFIGURED
            assert not isinstance(result.error, DataContractError)
            continue

        assert isinstance(result.error, DataContractError)
        assert result.attempts[0].outcome is expected
        assert result.error.code == "DATA_CONTRACT_ERROR"
        if mode == "configured":
            assert result.error.details.get("field") == "adapter.is_configured"
            assert "yes-" not in _blob(result.error)
        else:
            assert result.error.details.get("field") == "adapter.supports"
            assert "value" not in result.error.details
        assert result.error.details.get("rule") == "exact_bool"
        assert SECRET not in _blob(result.error)


# --- D8a: typed surface errors must not become PROVIDER_UNAVAILABLE ---------


@pytest.mark.asyncio
async def test_typed_data_contract_errors_are_preserved() -> None:
    for raise_on in ("supports", "configured"):
        engine, _, _, _, registry = _engine(settings=_settings(provider_retry_max_attempts=1))
        contract = DataContractError(
            "provider contract",
            details={
                "field": (
                    "provider.supports" if raise_on == "supports" else "adapter.is_configured"
                ),
                "rule": "exact_bool" if raise_on == "supports" else "exception_safe",
            },
        )
        if raise_on == "supports":
            _register(registry, VendorId.MOCK_US, raise_on_supports=contract)
        else:
            _register(registry, VendorId.MOCK_US, raise_on_configured=contract)

        result = await _run(engine, cache_codec=None)
        assert result.ok is False
        assert result.error is contract
        assert result.error.code == "DATA_CONTRACT_ERROR"
        assert result.attempts[0].outcome is ProviderAttemptOutcome.CONTRACT_ERROR
        assert result.attempts[0].error_code == "DATA_CONTRACT_ERROR"
        assert not isinstance(result.error, ProviderUnavailableError)


@pytest.mark.asyncio
async def test_supports_errors_stop_or_fallback_as_expected() -> None:
    for mode in ("auth_stop", "auth_fallback", "supports_exception"):
        settings = _settings()
        if mode == "auth_fallback":
            settings = _settings(auth_failure_fallback=True)
        if mode == "supports_exception":
            support_error: BaseException = RuntimeError(f"supports boom {SECRET}")
        else:
            support_error = ProviderAuthenticationError(
                "auth on supports", details={"vendor": "mock_us"}
            )

        engine, _, _, _, registry = _engine(
            settings=settings,
        )
        primary = _register(
            registry,
            VendorId.MOCK_US,
            raise_on_supports=support_error,  # type: ignore[arg-type]
        )
        secondary = _register(registry, VendorId.NULL)

        result = await _run(
            engine,
            chain=(
                (VendorId.MOCK_US, VendorId.NULL)
                if mode != "supports_exception"
                else (VendorId.MOCK_US,)
            ),
            cache_codec=None,
        )
        if mode == "supports_exception":
            assert result.ok is False
            assert isinstance(result.error, ProviderUnavailableError)
            assert result.error.code == "PROVIDER_UNAVAILABLE_ERROR"
            assert result.attempts[0].outcome is ProviderAttemptOutcome.FAILURE
            assert SECRET not in _blob(result.error)
            continue
        if mode == "auth_fallback":
            assert result.ok is True
            assert result.meta is not None
            assert result.meta.vendor is VendorId.NULL
            assert "FALLBACK_VENDOR_USED" in _codes(result)
            assert result.attempts[0].outcome is ProviderAttemptOutcome.AUTH_ERROR
            assert result.attempts[1].outcome is ProviderAttemptOutcome.SUCCESS
        else:
            assert result.ok is False
            assert result.error is support_error
            assert isinstance(result.error, ProviderAuthenticationError)
            assert result.error.code == "PROVIDER_AUTHENTICATION_ERROR"
            assert result.attempts[0].outcome is ProviderAttemptOutcome.AUTH_ERROR
            assert len(result.attempts) == 1
            assert primary.call_count == 0
            assert secondary.call_count == 0
