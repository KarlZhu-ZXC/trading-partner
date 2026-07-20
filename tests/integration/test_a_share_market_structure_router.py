"""E2 Router integration: service uses Router for all six ops, codecs, fingerprints."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from a_share_fixture_transport import (
    FixtureHttpTransport,
    market_board_success_scripted,
)
from application.dto.provider_resilience import RateLimitDecision
from application.ports.a_share_providers import (
    AShareMarketStructureProvider,
    AShareOhlcvProvider,
)
from application.services.a_share_market_structure_service import (
    OP_BARS,
    OP_INDUSTRY,
    OP_MARKET_BOARD,
    OP_ORDER_BOOK,
    OP_QUOTE,
    OP_TICKS,
    AShareMarketStructureService,
    build_a_share_fingerprint,
)
from application.services.a_share_tool_policies import (
    STRUCTURE_INSTRUMENT_BARS_POLICY,
    STRUCTURE_INSTRUMENT_BOOK_TICKS_POLICY,
    STRUCTURE_MARKET_INDUSTRY_POLICY,
)
from application.services.criticality_policy import CriticalityPolicy
from application.services.provider_router import ProviderRouter
from conftest import FixedClock, SequentialIdGenerator
from domain.a_share.enums import BarInterval
from domain.a_share.models import AShareBar
from domain.common.enums import (
    AdjustmentMethod,
    AppEnvironment,
    AssetType,
    DataCategory,
    LogLevel,
    Market,
    VendorId,
)
from domain.common.errors import DataContractError, StaleMarketData
from domain.instruments.models import Instrument
from infrastructure.config.settings import AppSettings
from infrastructure.providers.a_share.codecs import (
    bars_codec,
    industry_performance_codec,
    market_board_codec,
    order_book_codec,
    quote_codec,
    ticks_codec,
)
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.tencent import TencentAShareAdapter
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
AS_OF_QUOTE = datetime(2024, 1, 16, 6, 30, 10, tzinfo=UTC)
_CALENDAR_PATH = Path(__file__).resolve().parents[2] / "config" / "a_share_trading_calendar.v1.json"


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:A_SHARE:600519.SH",
        symbol="600519.SH",
        name="贵州茅台",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
    )


class _StaticChain:
    def __init__(self, chains: Mapping[tuple[Market, DataCategory], tuple[VendorId, ...]]) -> None:
        self._chains = dict(chains)

    def chain_for(self, market: Market, category: DataCategory) -> tuple[VendorId, ...]:
        return self._chains.get((market, category), ())

    def all_categories(self, market: Market) -> Mapping[DataCategory, tuple[VendorId, ...]]:
        out: dict[DataCategory, tuple[VendorId, ...]] = {}
        for (m, c), chain in self._chains.items():
            if m is market:
                out[c] = chain
        return MappingProxyType(out)


class _MemCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self.store.get(key)

    def set(self, key: str, entry: object) -> None:
        self.store[key] = entry

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


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


def _settings() -> AppSettings:
    return AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="tp-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="tp-test",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
        provider_retry_max_attempts=1,
        enable_provider_cache=True,
        enable_circuit_breaker=False,
    )


def _gate():
    async def no_sleep(_dt: float) -> None:
        return None

    return create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.001,
        jitter_seconds=0.0,
        clock=lambda: 0.0,
        sleep=no_sleep,
        random_func=lambda: 0.0,
    )


def _calendar() -> JsonAShareTradingCalendar:
    return JsonAShareTradingCalendar.load(_CALENDAR_PATH)


def _make_service(
    *,
    registry: VendorRegistry,
    clock: FixedClock,
    chains: Mapping[tuple[Market, DataCategory], tuple[VendorId, ...]] | None = None,
) -> tuple[AShareMarketStructureService, ProviderRouter, list[dict[str, Any]]]:
    engine = ProviderRouterEngine(
        registry=registry,
        cache_store=_MemCache(),  # type: ignore[arg-type]
        health_store=_MemHealth(),  # type: ignore[arg-type]
        rate_limiter=_AllowRateLimiter(),  # type: ignore[arg-type]
        circuit_breaker=CircuitBreaker(clock),
        clock=clock,
        settings=_settings(),
    )
    default_chains = chains or {
        (Market.A_SHARE, DataCategory.MARKET_QUOTE): (
            VendorId.TENCENT,
            VendorId.EASTMONEY,
        ),
        (Market.A_SHARE, DataCategory.MARKET_OHLCV): (VendorId.EASTMONEY,),
        (Market.A_SHARE, DataCategory.MARKET_STRUCTURE): (VendorId.EASTMONEY,),
    }
    router = ProviderRouter(
        engine=engine,
        chain_config=_StaticChain(default_chains),
        clock=clock,
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )
    execute_calls: list[dict[str, Any]] = []
    original = router.execute

    async def spy_execute(**kwargs: Any) -> Any:
        execute_calls.append(kwargs)
        return await original(**kwargs)

    router.execute = spy_execute  # type: ignore[method-assign]
    service = AShareMarketStructureService(
        router=router,
        clock=clock,
        calendar=_calendar(),
        quote_codec=quote_codec(),
        bars_codec=bars_codec(),
        order_book_codec=order_book_codec(),
        ticks_codec=ticks_codec(),
        industry_codec=industry_performance_codec(),
        market_board_codec=market_board_codec(),
        freshness_window_seconds=900,
    )
    return service, router, execute_calls


def _build_service(
    *,
    tencent_case: str = "success",
    eastmoney_quote_case: str = "success",
    chain_quote: tuple[VendorId, ...] = (VendorId.TENCENT, VendorId.EASTMONEY),
    clock: FixedClock | None = None,
) -> tuple[
    AShareMarketStructureService,
    VendorRegistry,
    FixtureHttpTransport,
    FixtureHttpTransport,
    Any,
]:
    tencent_transport = FixtureHttpTransport(vendor="tencent", operation="quote", case=tencent_case)
    em_transport = FixtureHttpTransport(
        vendor="eastmoney", operation="quote", case=eastmoney_quote_case
    )
    gate = _gate()
    clock = clock or FixedClock(AS_OF_QUOTE)
    tencent = TencentAShareAdapter(
        tencent_transport,
        clock=clock,
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    eastmoney = EastmoneyAShareAdapter(
        em_transport,
        gate,
        calendar=_calendar(),
        clock=clock,
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )
    registry = VendorRegistry()
    registry.register(VendorId.TENCENT, tencent)
    registry.register(VendorId.EASTMONEY, eastmoney)
    service, _, _ = _make_service(
        registry=registry,
        clock=clock,
        chains={
            (Market.A_SHARE, DataCategory.MARKET_QUOTE): chain_quote,
            (Market.A_SHARE, DataCategory.MARKET_OHLCV): (VendorId.EASTMONEY,),
            (Market.A_SHARE, DataCategory.MARKET_STRUCTURE): (VendorId.EASTMONEY,),
        },
    )
    return service, registry, tencent_transport, em_transport, gate


@pytest.mark.asyncio
async def test_quote_uses_router_tencent_primary() -> None:
    service, _, tencent_t, em_t, _ = _build_service()
    result = await service.get_quote(_instrument(), AS_OF_QUOTE)
    assert result.ok is True
    assert result.value is not None
    from decimal import Decimal

    assert result.value.last == Decimal("1680.50")
    assert result.value.volume_shares == 12_345_600
    assert result.meta is not None
    assert result.meta.vendor is VendorId.TENCENT
    assert len(tencent_t.requests) == 1
    assert len(em_t.requests) == 0


@pytest.mark.asyncio
async def test_quote_fallback_tencent_to_eastmoney() -> None:
    service, _, tencent_t, em_t, _ = _build_service(tencent_case="no_data")
    result = await service.get_quote(_instrument(), AS_OF_QUOTE)
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.vendor is VendorId.EASTMONEY
    assert len(tencent_t.requests) == 1
    assert len(em_t.requests) == 1
    vendors = [a.vendor for a in result.attempts]
    assert VendorId.TENCENT in vendors
    assert VendorId.EASTMONEY in vendors


@pytest.mark.asyncio
async def test_tencent_claims_bars_but_not_structure() -> None:
    _, registry, _, _, _ = _build_service()
    tencent = registry.get(VendorId.TENCENT)
    assert tencent.supports(Market.A_SHARE, DataCategory.MARKET_OHLCV)
    assert not tencent.supports(Market.A_SHARE, DataCategory.MARKET_STRUCTURE)
    assert isinstance(tencent, AShareOhlcvProvider)
    assert not isinstance(tencent, AShareMarketStructureProvider)


@pytest.mark.asyncio
async def test_bars_route_eastmoney_with_codec_and_fingerprint() -> None:
    em_transport = FixtureHttpTransport(vendor="eastmoney", operation="bars", case="success")
    gate = _gate()
    clock = FixedClock(AS_OF)
    eastmoney = EastmoneyAShareAdapter(em_transport, gate, calendar=_calendar(), clock=clock)
    tencent = TencentAShareAdapter(
        FixtureHttpTransport(vendor="tencent", operation="quote", case="success"),
        clock=clock,
    )
    registry = VendorRegistry()
    registry.register(VendorId.TENCENT, tencent)
    registry.register(VendorId.EASTMONEY, eastmoney)
    service, _, execute_calls = _make_service(registry=registry, clock=clock)
    result = await service.get_bars(
        _instrument(),
        start=date(2024, 1, 15),
        end=date(2024, 1, 16),
        interval=BarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.FORWARD_ADJUSTED,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.value is not None
    assert all(isinstance(b, AShareBar) for b in result.value)
    assert len(execute_calls) == 1
    call = execute_calls[0]
    assert call["operation_name"] == OP_BARS
    assert call["cache_codec"].codec_id == "a_share_bars.v1"
    assert call["tool_policy"] is STRUCTURE_INSTRUMENT_BARS_POLICY
    assert call["result_validator"] is not None
    fp = call["request_fingerprint"]
    assert "cookie" not in fp.lower()
    assert "token" not in fp.lower()
    fp2 = build_a_share_fingerprint(
        OP_BARS,
        _instrument().instrument_id,
        {
            "adjustment": "forward_adjusted",
            "end": "2024-01-16",
            "interval": "1d",
            "start": "2024-01-15",
        },
        AS_OF,
    )
    assert fp == fp2
    assert gate.max_observed_in_flight == 1
    assert len(em_transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "op", "codec_id", "policy", "setup"),
    [
        (
            "order_book",
            OP_ORDER_BOOK,
            "a_share_order_book.v1",
            STRUCTURE_INSTRUMENT_BOOK_TICKS_POLICY,
            "order_book",
        ),
        (
            "ticks",
            OP_TICKS,
            "a_share_ticks.v1",
            STRUCTURE_INSTRUMENT_BOOK_TICKS_POLICY,
            "ticks",
        ),
        (
            "industry",
            OP_INDUSTRY,
            "a_share_industry_performance.v1",
            STRUCTURE_MARKET_INDUSTRY_POLICY,
            "industry_performance",
        ),
        (
            "market_board",
            OP_MARKET_BOARD,
            "a_share_market_board.v1",
            STRUCTURE_MARKET_INDUSTRY_POLICY,
            "market_board",
        ),
    ],
)
async def test_structure_ops_route_via_router(
    method: str,
    op: str,
    codec_id: str,
    policy: object,
    setup: str,
) -> None:
    clock = FixedClock(AS_OF)
    gate = _gate()
    if setup == "market_board":
        em_transport: Any = market_board_success_scripted()
    else:
        em_transport = FixtureHttpTransport(vendor="eastmoney", operation=setup, case="success")
    eastmoney = EastmoneyAShareAdapter(em_transport, gate, calendar=_calendar(), clock=clock)
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, eastmoney)
    service, _, execute_calls = _make_service(registry=registry, clock=clock)

    if method == "order_book":
        result = await service.get_order_book(_instrument(), AS_OF)
    elif method == "ticks":
        result = await service.get_ticks(_instrument(), limit=10, as_of=AS_OF)
    elif method == "industry":
        result = await service.get_industry_performance(
            trade_date=date(2024, 1, 16), limit=20, as_of=AS_OF
        )
    else:
        result = await service.get_market_board(trade_date=date(2024, 1, 16), as_of=AS_OF)

    assert result.ok is True
    assert len(execute_calls) == 1
    call = execute_calls[0]
    assert call["operation_name"] == op
    assert call["cache_codec"].codec_id == codec_id
    assert call["tool_policy"] is policy
    assert call["category"] is DataCategory.MARKET_STRUCTURE
    # Prove Router path — adapter was only reached via execute (spy recorded).
    assert call["result_validator"] is not None
    assert len(em_transport.requests) >= 1


def test_fingerprint_stable_under_param_reorder() -> None:
    a = build_a_share_fingerprint(
        OP_QUOTE,
        "equity:A_SHARE:600519.SH",
        {"b": "2", "a": "1"},
        AS_OF,
    )
    b = build_a_share_fingerprint(
        OP_QUOTE,
        "equity:A_SHARE:600519.SH",
        {"a": "1", "b": "2"},
        AS_OF,
    )
    assert a == b
    assert "secret" not in a
    assert a.startswith("v1|a_share.quote.v1|")


@pytest.mark.asyncio
async def test_quote_historical_replay_rejected() -> None:
    clock = FixedClock(AS_OF)
    service, _, _, _, _ = _build_service(clock=clock)
    stale_as_of = AS_OF - timedelta(hours=2)
    with pytest.raises(StaleMarketData):
        await service.get_quote(_instrument(), stale_as_of)


@pytest.mark.asyncio
async def test_industry_stale_as_of_zero_router_calls() -> None:
    """Stale as_of for current-only industry fails before Router execution."""
    em_transport = FixtureHttpTransport(
        vendor="eastmoney", operation="industry_performance", case="success"
    )
    clock = FixedClock(AS_OF)
    eastmoney = EastmoneyAShareAdapter(em_transport, _gate(), calendar=_calendar(), clock=clock)
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, eastmoney)
    service, _, execute_calls = _make_service(registry=registry, clock=clock)
    stale_as_of = AS_OF - timedelta(hours=2)
    with pytest.raises(StaleMarketData) as exc:
        await service.get_industry_performance(
            trade_date=date(2024, 1, 16), limit=20, as_of=stale_as_of
        )
    assert exc.value.details.get("rule") == "freshness_window"
    assert execute_calls == []
    assert em_transport.requests == []


@pytest.mark.asyncio
async def test_market_board_stale_as_of_zero_router_calls() -> None:
    em_transport = market_board_success_scripted()
    clock = FixedClock(AS_OF)
    eastmoney = EastmoneyAShareAdapter(em_transport, _gate(), calendar=_calendar(), clock=clock)
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, eastmoney)
    service, _, execute_calls = _make_service(registry=registry, clock=clock)
    stale_as_of = AS_OF - timedelta(hours=2)
    with pytest.raises(StaleMarketData) as exc:
        await service.get_market_board(trade_date=date(2024, 1, 16), as_of=stale_as_of)
    assert exc.value.details.get("rule") == "freshness_window"
    assert execute_calls == []
    assert em_transport.requests == []


@pytest.mark.asyncio
async def test_industry_trade_date_later_than_as_of_local_zero_router() -> None:
    em_transport = FixtureHttpTransport(
        vendor="eastmoney", operation="industry_performance", case="success"
    )
    # as_of and clock both after close on 2024-01-15 Shanghai.
    as_of = datetime(2024, 1, 15, 7, 30, tzinfo=UTC)
    clock = FixedClock(as_of)
    eastmoney = EastmoneyAShareAdapter(em_transport, _gate(), calendar=_calendar(), clock=clock)
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, eastmoney)
    service, _, execute_calls = _make_service(registry=registry, clock=clock)
    with pytest.raises(DataContractError) as exc:
        await service.get_industry_performance(trade_date=date(2024, 1, 16), limit=20, as_of=as_of)
    assert exc.value.details.get("rule") == "trade_date_not_after_as_of_local"
    assert execute_calls == []
    assert em_transport.requests == []


def test_service_constructor_requires_is_trading_day_and_previous() -> None:
    class _OnlyIsTradingDay:
        def is_trading_day(self, d: date) -> bool:
            return True

    class _OnlyPrevious:
        def previous_trading_day(self, d: date) -> date:
            return d

    class _Both:
        def is_trading_day(self, d: date) -> bool:
            return True

        def previous_trading_day(self, d: date) -> date:
            return d

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
    router = ProviderRouter(
        engine=engine,
        chain_config=_StaticChain({}),
        clock=clock,
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )
    codec_kwargs = dict(
        quote_codec=quote_codec(),
        bars_codec=bars_codec(),
        order_book_codec=order_book_codec(),
        ticks_codec=ticks_codec(),
        industry_codec=industry_performance_codec(),
        market_board_codec=market_board_codec(),
    )
    with pytest.raises(DataContractError) as exc:
        AShareMarketStructureService(
            router=router,
            clock=clock,
            calendar=_OnlyIsTradingDay(),  # type: ignore[arg-type]
            **codec_kwargs,
        )
    assert exc.value.details.get("missing") == "previous_trading_day"

    with pytest.raises(DataContractError) as exc:
        AShareMarketStructureService(
            router=router,
            clock=clock,
            calendar=_OnlyPrevious(),  # type: ignore[arg-type]
            **codec_kwargs,
        )
    assert exc.value.details.get("missing") == "is_trading_day"

    with pytest.raises(DataContractError):
        AShareMarketStructureService(
            router=router,
            clock=clock,
            calendar=None,  # type: ignore[arg-type]
            **codec_kwargs,
        )

    # Both present → constructs.
    service = AShareMarketStructureService(
        router=router,
        clock=clock,
        calendar=_Both(),  # type: ignore[arg-type]
        **codec_kwargs,
    )
    assert service is not None


@pytest.mark.asyncio
async def test_protocol_mismatch_continues_chain() -> None:
    class _NoQuoteProtocol:
        @property
        def vendor_id(self) -> VendorId:
            return VendorId.TENCENT

        @property
        def provider_name(self) -> str:
            return VendorId.TENCENT.value

        def supports(self, market: Market, category: DataCategory) -> bool:
            return market is Market.A_SHARE and category is DataCategory.MARKET_QUOTE

        def is_configured(self) -> bool:
            return True

    em_transport = FixtureHttpTransport(vendor="eastmoney", operation="quote", case="success")
    clock = FixedClock(AS_OF_QUOTE)
    eastmoney = EastmoneyAShareAdapter(em_transport, _gate(), calendar=_calendar(), clock=clock)
    registry = VendorRegistry()
    registry.register(VendorId.TENCENT, _NoQuoteProtocol())  # type: ignore[arg-type]
    registry.register(VendorId.EASTMONEY, eastmoney)
    service, _, _ = _make_service(
        registry=registry,
        clock=clock,
        chains={
            (Market.A_SHARE, DataCategory.MARKET_QUOTE): (
                VendorId.TENCENT,
                VendorId.EASTMONEY,
            )
        },
    )
    result = await service.get_quote(_instrument(), AS_OF_QUOTE)
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.vendor is VendorId.EASTMONEY
