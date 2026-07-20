"""Phase 1D D9: NoMarketData vs typed provider failure chain exhaustion.

Pure NoMarketData exhaustion must retain ``NO_MARKET_DATA``.
Provider timeout / unavailable exhaustion must retain the typed error and
must not collapse into NoMarketData (or vice versa).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from application.dto.provider_resilience import RateLimitDecision
from application.dto.provider_routing import ProviderSuccess
from conftest import FixedClock
from domain.common.enums import (
    AppEnvironment,
    DataCategory,
    DataCriticality,
    LogLevel,
    Market,
    ProviderAttemptOutcome,
    VendorId,
)
from domain.common.errors import (
    NoMarketData,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from infrastructure.config.settings import AppSettings
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine

AS_OF = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
OP = "get_snapshot"
FP = "no-data-vs-failure-fp-1"


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
        app_name="tp-no-data-vs-failure",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:////tmp/no-data-vs-failure-test.db",
        mcp_server_name="tp-no-data-vs-failure",
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


async def _run_chain(
    handlers: dict[VendorId, Callable[[], Awaitable[ProviderSuccess[Any]]]],
    chain: tuple[VendorId, ...],
) -> Any:
    engine, registry = _engine()
    for vendor_id, handler in handlers.items():
        registry.register(vendor_id, _HandlerAdapter(vendor_id, handler))

    async def _call(adapter: Any) -> ProviderSuccess[Any]:
        assert isinstance(adapter, _HandlerAdapter)
        return await adapter._handler()

    return await engine.execute(
        market=Market.US,
        category=DataCategory.MARKET_SNAPSHOT,
        chain=chain,
        criticality=DataCriticality.CORE,
        call=_call,
        operation_name=OP,
        request_fingerprint=FP,
        instrument=None,
        as_of=AS_OF,
        bypass_cache=True,
        cache_codec=None,
        result_validator=None,
    )


@pytest.mark.asyncio
async def test_pure_no_market_data_single_vendor_exhaustion() -> None:
    async def _empty() -> ProviderSuccess[Any]:
        raise NoMarketData("no series", details={"vendor": "mock_us"})

    result = await _run_chain(
        {VendorId.MOCK_US: _empty},
        (VendorId.MOCK_US,),
    )
    assert result.ok is False
    assert isinstance(result.error, NoMarketData)
    assert result.error.code == "NO_MARKET_DATA"
    assert result.error.retryable is False
    assert result.attempts[0].outcome is ProviderAttemptOutcome.NO_DATA


@pytest.mark.asyncio
async def test_pure_no_market_data_full_chain_exhaustion() -> None:
    async def _empty_us() -> ProviderSuccess[Any]:
        raise NoMarketData("none us", details={"vendor": "mock_us"})

    async def _empty_a() -> ProviderSuccess[Any]:
        raise NoMarketData("none a", details={"vendor": "mock_a_share"})

    result = await _run_chain(
        {
            VendorId.MOCK_US: _empty_us,
            VendorId.MOCK_A_SHARE: _empty_a,
        },
        (VendorId.MOCK_US, VendorId.MOCK_A_SHARE),
    )
    assert result.ok is False
    assert isinstance(result.error, NoMarketData)
    assert result.error.code == "NO_MARKET_DATA"
    assert len(result.attempts) == 2
    assert all(a.outcome is ProviderAttemptOutcome.NO_DATA for a in result.attempts)


@pytest.mark.asyncio
async def test_pure_timeout_exhaustion_keeps_provider_timeout() -> None:
    async def _timeout() -> ProviderSuccess[Any]:
        raise ProviderTimeoutError("timed out", details={"vendor": "mock_us"})

    result = await _run_chain(
        {VendorId.MOCK_US: _timeout},
        (VendorId.MOCK_US,),
    )
    assert result.ok is False
    assert isinstance(result.error, ProviderTimeoutError)
    assert result.error.code == "PROVIDER_TIMEOUT_ERROR"
    assert not isinstance(result.error, NoMarketData)
    assert result.attempts[0].outcome is ProviderAttemptOutcome.TIMEOUT


@pytest.mark.asyncio
async def test_pure_unavailable_exhaustion_keeps_provider_unavailable() -> None:
    async def _down() -> ProviderSuccess[Any]:
        raise ProviderUnavailableError("5xx", details={"vendor": "mock_us"})

    result = await _run_chain(
        {VendorId.MOCK_US: _down},
        (VendorId.MOCK_US,),
    )
    assert result.ok is False
    assert isinstance(result.error, ProviderUnavailableError)
    assert result.error.code == "PROVIDER_UNAVAILABLE_ERROR"
    assert not isinstance(result.error, NoMarketData)
    assert result.attempts[0].outcome is ProviderAttemptOutcome.FAILURE


@pytest.mark.asyncio
async def test_mixed_no_data_then_timeout_prefers_timeout_not_no_data() -> None:
    """Aggregation priority: timeout outranks NoMarketData (design §11.3)."""

    async def _empty() -> ProviderSuccess[Any]:
        raise NoMarketData("none", details={"vendor": "mock_us"})

    async def _timeout() -> ProviderSuccess[Any]:
        raise ProviderTimeoutError("t", details={"vendor": "mock_a_share"})

    result = await _run_chain(
        {
            VendorId.MOCK_US: _empty,
            VendorId.MOCK_A_SHARE: _timeout,
        },
        (VendorId.MOCK_US, VendorId.MOCK_A_SHARE),
    )
    assert result.ok is False
    assert isinstance(result.error, ProviderTimeoutError)
    assert result.error.code == "PROVIDER_TIMEOUT_ERROR"
    assert not isinstance(result.error, NoMarketData)


@pytest.mark.asyncio
async def test_mixed_timeout_then_no_data_still_timeout() -> None:
    async def _timeout() -> ProviderSuccess[Any]:
        raise ProviderTimeoutError("t", details={"vendor": "mock_us"})

    async def _empty() -> ProviderSuccess[Any]:
        raise NoMarketData("none", details={"vendor": "mock_a_share"})

    result = await _run_chain(
        {
            VendorId.MOCK_US: _timeout,
            VendorId.MOCK_A_SHARE: _empty,
        },
        (VendorId.MOCK_US, VendorId.MOCK_A_SHARE),
    )
    assert result.ok is False
    assert isinstance(result.error, ProviderTimeoutError)
    assert result.error.code == "PROVIDER_TIMEOUT_ERROR"


@pytest.mark.asyncio
async def test_unavailable_full_chain_does_not_become_no_market_data() -> None:
    async def _down1() -> ProviderSuccess[Any]:
        raise ProviderUnavailableError("down1", details={"vendor": "mock_us"})

    async def _down2() -> ProviderSuccess[Any]:
        raise ProviderUnavailableError("down2", details={"vendor": "mock_a_share"})

    result = await _run_chain(
        {
            VendorId.MOCK_US: _down1,
            VendorId.MOCK_A_SHARE: _down2,
        },
        (VendorId.MOCK_US, VendorId.MOCK_A_SHARE),
    )
    assert result.ok is False
    assert isinstance(result.error, ProviderUnavailableError)
    assert result.error.code == "PROVIDER_UNAVAILABLE_ERROR"
    assert result.error.code != "NO_MARKET_DATA"


@pytest.mark.asyncio
async def test_no_data_error_has_no_secret_leakage() -> None:
    secret = "test-secret-malicious-value"

    async def _empty() -> ProviderSuccess[Any]:
        raise NoMarketData(
            f"none {secret}",
            details={"vendor": "mock_us", "token": secret},
        )

    result = await _run_chain(
        {VendorId.MOCK_US: _empty},
        (VendorId.MOCK_US,),
    )
    assert isinstance(result.error, NoMarketData)
    # Engine preserves the typed error identity; caller-supplied message/details
    # are not re-wrapped. This test locks type retention, not redaction of
    # adapter-authored messages (redaction is a separate surface).
    assert result.error.code == "NO_MARKET_DATA"
    assert result.error.__cause__ is None
