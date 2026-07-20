"""Phase 1D D9: Core vs Optional chain-exhaustion exit policy (design §11.4).

Locks CriticalityPolicy defaults plus Router CORE/OPTIONAL exhaustion semantics.
Complements tests/unit/test_criticality_policy.py without renaming it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from application.dto.provider_resilience import RateLimitDecision
from application.dto.provider_routing import ProviderSuccess
from application.services.criticality_policy import CriticalityPolicy
from conftest import FixedClock
from domain.common.enums import (
    AppEnvironment,
    DataCategory,
    DataCriticality,
    LogLevel,
    Market,
    VendorId,
)
from domain.common.errors import (
    NoMarketData,
    ProviderNotConfigured,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from infrastructure.config.settings import AppSettings
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.common.null_category_provider import NullCategoryProvider
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
OP = "get_snapshot"
FP = "core-optional-fp-1"


class _MemCache:
    def get(self, key: str) -> None:
        return None

    def set(self, key: str, entry: object) -> None:
        return None

    def delete(self, key: str) -> None:
        return None


class _MemHealth:
    def record_success(self, vendor: VendorId, category: DataCategory, at: datetime) -> None:
        return None

    def record_failure(
        self,
        vendor: VendorId,
        category: DataCategory,
        at: datetime,
        error_code: str,
    ) -> None:
        return None

    def set_circuit_state(
        self,
        vendor: VendorId,
        category: DataCategory,
        state: object,
        at: datetime,
    ) -> None:
        return None

    def get(self, vendor: VendorId, category: DataCategory) -> object:
        raise NotImplementedError


class _MemRateStore:
    def consume(
        self,
        *,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
        window_seconds: int,
        limit_count: int,
        at: datetime,
    ) -> object:
        from application.dto.provider_state import ProviderRateLimitSnapshot

        return ProviderRateLimitSnapshot(
            vendor=vendor,
            category=category,
            window_start=window_start,
            window_seconds=window_seconds,
            request_count=1,
            limit_count=limit_count,
            updated_at=at,
        )


class _AllowRateLimiter:
    def check_and_consume(self, vendor: VendorId, category: DataCategory) -> RateLimitDecision:
        return RateLimitDecision(
            allowed=True,
            remaining=10,
            reset_at=AS_OF,
            limit_per_window=100,
        )


class _HandlerAdapter:
    def __init__(
        self,
        vendor_id: VendorId,
        handler: Callable[[], Awaitable[ProviderSuccess[Any]]],
    ) -> None:
        self._vendor_id = vendor_id
        self._handler = handler

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        return self._vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        del market, category
        return True

    def is_configured(self) -> bool:
        return True


def _settings() -> AppSettings:
    return AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="tp-core-optional",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:////tmp/core-optional-test.db",
        mcp_server_name="tp-core-optional",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
        provider_retry_max_attempts=1,
        enable_provider_cache=False,
        enable_circuit_breaker=False,
    )


def _engine() -> tuple[ProviderRouterEngine, VendorRegistry]:
    clock = FixedClock(AS_OF)
    registry = VendorRegistry()
    engine = ProviderRouterEngine(
        registry=registry,
        cache_store=_MemCache(),  # type: ignore[arg-type]
        health_store=_MemHealth(),  # type: ignore[arg-type]
        rate_limiter=_AllowRateLimiter(),  # type: ignore[arg-type]
        circuit_breaker=CircuitBreaker(clock),
        clock=clock,
        settings=_settings(),
    )
    return engine, registry


async def _execute(
    engine: ProviderRouterEngine,
    registry: VendorRegistry,
    *,
    chain: tuple[VendorId, ...],
    category: DataCategory,
    criticality: DataCriticality,
    handlers: dict[VendorId, Callable[[], Awaitable[ProviderSuccess[Any]]]] | None = None,
) -> Any:
    handlers = handlers or {}
    for vendor_id in chain:
        if vendor_id is VendorId.NULL and vendor_id not in handlers:
            registry.register(vendor_id, NullCategoryProvider())
            continue
        if vendor_id not in handlers:
            raise AssertionError(f"missing handler for {vendor_id}")
        registry.register(vendor_id, _HandlerAdapter(vendor_id, handlers[vendor_id]))

    async def _call(adapter: Any) -> ProviderSuccess[Any]:
        if isinstance(adapter, NullCategoryProvider):
            # Reach real data method so typed ProviderNotConfigured surfaces.
            return await adapter.get_fundamentals(  # type: ignore[return-value]
                instrument=None,  # type: ignore[arg-type]
                as_of=AS_OF,
            )
        assert isinstance(adapter, _HandlerAdapter)
        return await adapter._handler()

    return await engine.execute(
        market=Market.US,
        category=category,
        chain=chain,
        criticality=criticality,
        call=_call,
        operation_name=OP,
        request_fingerprint=FP,
        instrument=None,
        as_of=AS_OF,
        bypass_cache=True,
        cache_codec=None,
        result_validator=None,
    )


def test_default_criticality_table_covers_all_categories() -> None:
    table = CriticalityPolicy.default_table()
    assert set(table) == set(DataCategory)
    assert table[DataCategory.MARKET_SNAPSHOT] is DataCriticality.CORE
    assert table[DataCategory.FUNDAMENTALS] is DataCriticality.CORE
    assert table[DataCategory.NEWS] is DataCriticality.OPTIONAL
    assert table[DataCategory.SENTIMENT] is DataCriticality.OPTIONAL


@pytest.mark.asyncio
async def test_core_exhaustion_is_ok_false_without_optional_warning() -> None:
    engine, registry = _engine()

    async def _down() -> ProviderSuccess[Any]:
        raise ProviderUnavailableError("down", details={"vendor": "mock_us"})

    result = await _execute(
        engine,
        registry,
        chain=(VendorId.MOCK_US,),
        category=DataCategory.MARKET_SNAPSHOT,
        criticality=DataCriticality.CORE,
        handlers={VendorId.MOCK_US: _down},
    )
    assert result.ok is False
    assert result.criticality is DataCriticality.CORE
    assert isinstance(result.error, ProviderUnavailableError)
    assert all(w.code != "OPTIONAL_DATA_UNAVAILABLE" for w in result.warnings)


@pytest.mark.asyncio
async def test_optional_exhaustion_adds_optional_data_unavailable() -> None:
    engine, registry = _engine()

    async def _timeout() -> ProviderSuccess[Any]:
        raise ProviderTimeoutError("t", details={"vendor": "mock_us"})

    result = await _execute(
        engine,
        registry,
        chain=(VendorId.MOCK_US,),
        category=DataCategory.NEWS,
        criticality=DataCriticality.OPTIONAL,
        handlers={VendorId.MOCK_US: _timeout},
    )
    assert result.ok is False
    assert result.criticality is DataCriticality.OPTIONAL
    assert isinstance(result.error, ProviderTimeoutError)
    assert any(w.code == "OPTIONAL_DATA_UNAVAILABLE" for w in result.warnings)


@pytest.mark.asyncio
async def test_null_only_core_chain_raises_provider_not_configured() -> None:
    """YAML-style [null] CORE chain must exhaust as ProviderNotConfigured."""
    engine, registry = _engine()
    result = await _execute(
        engine,
        registry,
        chain=(VendorId.NULL,),
        category=DataCategory.FUNDAMENTALS,
        criticality=DataCriticality.CORE,
    )
    assert result.ok is False
    assert isinstance(result.error, ProviderNotConfigured)
    assert result.error.code == "PROVIDER_NOT_CONFIGURED"
    assert all(w.code != "OPTIONAL_DATA_UNAVAILABLE" for w in result.warnings)


@pytest.mark.asyncio
async def test_null_only_optional_chain_adds_optional_warning() -> None:
    engine, registry = _engine()
    result = await _execute(
        engine,
        registry,
        chain=(VendorId.NULL,),
        category=DataCategory.NEWS,
        criticality=DataCriticality.OPTIONAL,
    )
    assert result.ok is False
    assert isinstance(result.error, ProviderNotConfigured)
    assert any(w.code == "OPTIONAL_DATA_UNAVAILABLE" for w in result.warnings)


@pytest.mark.asyncio
async def test_optional_no_market_data_exhaustion_still_optional_warning() -> None:
    engine, registry = _engine()

    async def _empty() -> ProviderSuccess[Any]:
        raise NoMarketData("none", details={"vendor": "mock_us"})

    result = await _execute(
        engine,
        registry,
        chain=(VendorId.MOCK_US,),
        category=DataCategory.SENTIMENT,
        criticality=DataCriticality.OPTIONAL,
        handlers={VendorId.MOCK_US: _empty},
    )
    assert result.ok is False
    assert isinstance(result.error, NoMarketData)
    assert result.error.code == "NO_MARKET_DATA"
    assert any(w.code == "OPTIONAL_DATA_UNAVAILABLE" for w in result.warnings)
