"""E3 Router integration: announcement fallback, snapshot, report search."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from a_share_fixture_transport import FixtureHttpTransport
from application.dto.provider_resilience import RateLimitDecision
from application.dto.provider_routing import ProviderSuccess
from application.ports.a_share_providers import (
    AShareDisclosureProvider,
    AShareFinancialStatementsProvider,
)
from application.ports.category_provider import CategoryProvider
from application.services.a_share_market_structure_service import (
    build_a_share_fingerprint,
)
from application.services.a_share_snapshot_service import AShareSnapshotService
from application.services.a_share_tool_policies import SNAPSHOT_SUMMARY_POLICY
from application.services.criticality_policy import CriticalityPolicy
from application.services.provider_router import ProviderRouter
from application.services.research_report_search_service import (
    ResearchReportSearchService,
    report_text_fingerprint_hash,
)
from conftest import FixedClock, SequentialIdGenerator
from domain.a_share.enums import AShareSnapshotDetail, FinancialStatementType
from domain.a_share.models import AnnouncementItem, FinancialStatementLine
from domain.common.enums import (
    AppEnvironment,
    AssetType,
    DataCategory,
    LogLevel,
    Market,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from infrastructure.config.settings import AppSettings
from infrastructure.providers.a_share.cls import CLSAShareAdapter
from infrastructure.providers.a_share.cninfo import CninfoAShareAdapter
from infrastructure.providers.a_share.codecs import (
    announcements_codec,
    consensus_codec,
    corporate_actions_codec,
    f10_codec,
    fundamentals_codec,
    news_codec,
    quote_codec,
    reports_codec,
    statements_codec,
)
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.a_share.exchanges import SseAShareDisclosureAdapter
from infrastructure.providers.a_share.sina import SinaAShareAdapter
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


def _etf() -> Instrument:
    return Instrument(
        instrument_id="etf:A_SHARE:510300.SH",
        symbol="510300.SH",
        name="沪深300ETF",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.ETF,
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
        app_name="e3-test",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="e3-test",
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


def _build_router(
    registry: VendorRegistry,
    chains: Mapping[tuple[Market, DataCategory], tuple[VendorId, ...]],
    clock: FixedClock | None = None,
) -> ProviderRouter:
    clock = clock or FixedClock(AS_OF)
    engine = ProviderRouterEngine(
        registry=registry,
        cache_store=_MemCache(),  # type: ignore[arg-type]
        health_store=_MemHealth(),  # type: ignore[arg-type]
        rate_limiter=_AllowRateLimiter(),  # type: ignore[arg-type]
        circuit_breaker=CircuitBreaker(clock),
        clock=clock,
        settings=_settings(),
    )
    return ProviderRouter(
        engine=engine,
        chain_config=_StaticChain(chains),
        clock=clock,
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )


@pytest.mark.asyncio
async def test_announcement_cninfo_failure_falls_back_to_sse() -> None:
    cninfo_transport = FixtureHttpTransport(
        vendor="cninfo", operation="announcements", case="rate_limit"
    )
    sse_transport = FixtureHttpTransport(vendor="sse", operation="announcements", case="success")
    cninfo = CninfoAShareAdapter(cninfo_transport, clock=FixedClock(AS_OF))
    sse = SseAShareDisclosureAdapter(sse_transport, clock=FixedClock(AS_OF))
    registry = VendorRegistry()
    registry.register(VendorId.CNINFO, cninfo)
    registry.register(VendorId.SSE, sse)
    router = _build_router(
        registry,
        {
            (Market.A_SHARE, DataCategory.ANNOUNCEMENTS): (
                VendorId.CNINFO,
                VendorId.SSE,
            )
        },
    )
    instrument = _instrument()

    async def _call(
        adapter: CategoryProvider,
    ) -> ProviderSuccess[tuple[AnnouncementItem, ...]]:
        if not isinstance(adapter, AShareDisclosureProvider):
            raise DataContractError(
                "adapter does not implement required A-share protocol",
                details={"category": "announcements", "rule": "protocol"},
            )
        return await adapter.get_announcements(instrument, limit=10, as_of=AS_OF)

    result = await router.execute(
        market=Market.A_SHARE,
        category=DataCategory.ANNOUNCEMENTS,
        call=_call,
        operation_name="a_share.announcements.v1",
        request_fingerprint=build_a_share_fingerprint(
            "a_share.announcements.v1",
            instrument.instrument_id,
            {"limit": "10"},
            AS_OF,
        ),
        instrument=instrument,
        as_of=AS_OF,
        tool_policy=SNAPSHOT_SUMMARY_POLICY,
        bypass_cache=False,
        cache_codec=announcements_codec(),
        result_validator=None,
    )
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.vendor is VendorId.SSE
    assert result.value
    outcomes = [(a.vendor, a.outcome.value) for a in result.attempts]
    assert outcomes[0][0] is VendorId.CNINFO
    assert outcomes[0][1] != "success"
    assert any(v is VendorId.SSE and o == "success" for v, o in outcomes)


@pytest.mark.asyncio
async def test_announcement_cninfo_empty_is_success_not_fallback() -> None:
    cninfo_transport = FixtureHttpTransport(
        vendor="cninfo", operation="announcements", case="no_data"
    )
    sse_transport = FixtureHttpTransport(vendor="sse", operation="announcements", case="success")
    cninfo = CninfoAShareAdapter(cninfo_transport, clock=FixedClock(AS_OF))
    sse = SseAShareDisclosureAdapter(sse_transport, clock=FixedClock(AS_OF))
    registry = VendorRegistry()
    registry.register(VendorId.CNINFO, cninfo)
    registry.register(VendorId.SSE, sse)
    router = _build_router(
        registry,
        {
            (Market.A_SHARE, DataCategory.ANNOUNCEMENTS): (
                VendorId.CNINFO,
                VendorId.SSE,
            )
        },
    )
    instrument = _instrument()

    async def _call(
        adapter: CategoryProvider,
    ) -> ProviderSuccess[tuple[AnnouncementItem, ...]]:
        if not isinstance(adapter, AShareDisclosureProvider):
            raise DataContractError("bad", details={"rule": "protocol"})
        return await adapter.get_announcements(instrument, limit=10, as_of=AS_OF)

    result = await router.execute(
        market=Market.A_SHARE,
        category=DataCategory.ANNOUNCEMENTS,
        call=_call,
        operation_name="a_share.announcements.v1",
        request_fingerprint=build_a_share_fingerprint(
            "a_share.announcements.v1", instrument.instrument_id, {}, AS_OF
        ),
        instrument=instrument,
        as_of=AS_OF,
        tool_policy=None,
        bypass_cache=True,
        cache_codec=None,
        result_validator=None,
    )
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.vendor is VendorId.CNINFO
    assert result.value == ()
    assert sse_transport.requests == []


@pytest.mark.asyncio
async def test_statements_sina_primary_then_eastmoney_fallback() -> None:
    sina_transport = FixtureHttpTransport(vendor="sina", operation="statements", case="no_data")
    em_transport = FixtureHttpTransport(vendor="eastmoney", operation="statements", case="success")
    sina = SinaAShareAdapter(sina_transport, clock=FixedClock(AS_OF))
    em = EastmoneyAShareAdapter(
        em_transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
    )
    registry = VendorRegistry()
    registry.register(VendorId.SINA, sina)
    registry.register(VendorId.EASTMONEY, em)
    router = _build_router(
        registry,
        {
            (Market.A_SHARE, DataCategory.FINANCIAL_STATEMENTS): (
                VendorId.SINA,
                VendorId.EASTMONEY,
            )
        },
    )
    instrument = _instrument()

    async def _call(
        adapter: CategoryProvider,
    ) -> ProviderSuccess[tuple[FinancialStatementLine, ...]]:
        if not isinstance(adapter, AShareFinancialStatementsProvider):
            raise DataContractError("bad", details={"rule": "protocol"})
        return await adapter.get_financial_statements(
            instrument,
            statement_types=(FinancialStatementType.BALANCE_SHEET,),
            periods=4,
            as_of=AS_OF,
        )

    result = await router.execute(
        market=Market.A_SHARE,
        category=DataCategory.FINANCIAL_STATEMENTS,
        call=_call,
        operation_name="a_share.statements.v1",
        request_fingerprint=build_a_share_fingerprint(
            "a_share.statements.v1", instrument.instrument_id, {}, AS_OF
        ),
        instrument=instrument,
        as_of=AS_OF,
        tool_policy=None,
        bypass_cache=True,
        cache_codec=None,
        result_validator=None,
    )
    assert result.ok is True
    assert result.meta is not None
    assert result.meta.vendor is VendorId.EASTMONEY
    assert result.value


@pytest.mark.asyncio
async def test_snapshot_etf_does_not_require_statements() -> None:
    # Use Eastmoney quote fixture which is instrument-code driven via secid, not name.
    em = EastmoneyAShareAdapter(
        FixtureHttpTransport(vendor="eastmoney", operation="quote", case="success"),
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF_QUOTE),
        max_fresh_seconds=3600,
        max_delayed_seconds=7200,
    )
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, em)
    router = _build_router(
        registry,
        {(Market.A_SHARE, DataCategory.MARKET_QUOTE): (VendorId.EASTMONEY,)},
        clock=FixedClock(AS_OF_QUOTE),
    )
    service = AShareSnapshotService(
        router=router,
        clock=FixedClock(AS_OF_QUOTE),
        quote_codec=quote_codec(),
        fundamentals_codec=fundamentals_codec(),
        f10_codec=f10_codec(),
        statements_codec=statements_codec(),
        announcements_codec=announcements_codec(),
        news_codec=news_codec(),
        corporate_actions_codec=corporate_actions_codec(),
    )
    # Quote fixture is for 600519 — override instrument identity to match fixture
    # while keeping ETF asset type for the support matrix.
    etf_like = Instrument(
        instrument_id="etf:A_SHARE:600519.SH",
        symbol="600519.SH",
        name="ETF-like",
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.ETF,
    )
    result = await service.get_snapshot(etf_like, AS_OF_QUOTE, AShareSnapshotDetail.FULL)
    assert result.ok is True
    assert result.data is not None
    assert result.data.quote is not None
    assert result.data.statements == ()
    assert result.data.f10_sections == ()


@pytest.mark.asyncio
async def test_research_report_search_validation_and_fingerprint() -> None:
    em = EastmoneyAShareAdapter(
        FixtureHttpTransport(vendor="eastmoney", operation="reports", case="success"),
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
    )
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, em)
    router = _build_router(
        registry,
        {(Market.A_SHARE, DataCategory.RESEARCH_REPORTS): (VendorId.EASTMONEY,)},
    )
    redactor = DefaultSecretRedactor()
    service = ResearchReportSearchService(
        router=router,
        clock=FixedClock(AS_OF),
        secret_redactor=redactor,
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    with pytest.raises(DataContractError):
        await service.search(text="   ", instrument=None, industry_code=None)
    with pytest.raises(DataContractError):
        await service.search(text="x", limit=0)
    with pytest.raises(DataContractError):
        await service.search(text="x", as_of=AS_OF + timedelta(days=1))
    digest = report_text_fingerprint_hash("茅台 研究", redactor=redactor)
    assert "茅台" not in digest
    result = await service.search(
        text="茅台 研究",
        instrument=_instrument(),
        include_consensus=False,
        limit=20,
        offset=0,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.reports
    assert result.data.consensus == ()

    em2 = EastmoneyAShareAdapter(
        FixtureHttpTransport(vendor="eastmoney", operation="reports", case="success"),
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
    )
    registry2 = VendorRegistry()
    registry2.register(VendorId.EASTMONEY, em2)
    router2 = _build_router(
        registry2,
        {(Market.A_SHARE, DataCategory.RESEARCH_REPORTS): (VendorId.EASTMONEY,)},
    )
    service2 = ResearchReportSearchService(
        router=router2,
        clock=FixedClock(AS_OF),
        secret_redactor=redactor,
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    market_only = await service2.search(
        text="白酒",
        instrument=None,
        include_consensus=True,
        as_of=AS_OF,
    )
    assert market_only.data is not None
    assert market_only.data.consensus == ()


@pytest.mark.asyncio
async def test_cls_instrument_falls_through_to_eastmoney_market_uses_cls() -> None:
    """CLS cannot filter by instrument; instrument chain falls to Eastmoney.

    Market-scope (instrument=None) continues to use CLS as primary.
    """
    from application.ports.a_share_providers import AShareNewsProvider
    from domain.a_share.models import NewsItem

    cls_transport = FixtureHttpTransport(vendor="cls", operation="news", case="success")
    em_transport = FixtureHttpTransport(vendor="eastmoney", operation="news", case="success")
    cls = CLSAShareAdapter(cls_transport, clock=FixedClock(AS_OF))
    em = EastmoneyAShareAdapter(
        em_transport,
        _gate(),
        calendar=_calendar(),
        clock=FixedClock(AS_OF),
    )
    registry = VendorRegistry()
    registry.register(VendorId.CLS, cls)
    registry.register(VendorId.EASTMONEY, em)
    router = _build_router(
        registry,
        {
            (Market.A_SHARE, DataCategory.NEWS): (VendorId.CLS, VendorId.EASTMONEY),
        },
    )
    instrument = _instrument()
    start = AS_OF - timedelta(days=7)

    async def _call(
        adapter: CategoryProvider,
    ) -> ProviderSuccess[tuple[NewsItem, ...]]:
        if not isinstance(adapter, AShareNewsProvider):
            raise DataContractError("bad", details={"rule": "protocol"})
        return await adapter.get_news(instrument, start=start, end=AS_OF, limit=10, as_of=AS_OF)

    instrument_res = await router.execute(
        market=Market.A_SHARE,
        category=DataCategory.NEWS,
        call=_call,
        operation_name="a_share.news.v1",
        request_fingerprint=build_a_share_fingerprint(
            "a_share.news.v1", instrument.instrument_id, {}, AS_OF
        ),
        instrument=instrument,
        as_of=AS_OF,
        tool_policy=SNAPSHOT_SUMMARY_POLICY,
        bypass_cache=True,
        cache_codec=None,
        result_validator=None,
    )
    assert instrument_res.ok is True
    assert instrument_res.meta is not None
    assert instrument_res.meta.vendor is VendorId.EASTMONEY
    assert cls_transport.requests == []  # NoMarketData before network
    assert em_transport.requests  # fell through to Eastmoney
    outcomes = [(a.vendor, a.outcome.value) for a in instrument_res.attempts]
    assert outcomes[0][0] is VendorId.CLS
    assert outcomes[0][1] == "no_data"
    assert any(v is VendorId.EASTMONEY and o == "success" for v, o in outcomes)

    # Market scope uses CLS.
    cls_transport2 = FixtureHttpTransport(vendor="cls", operation="news", case="success")
    em_transport2 = FixtureHttpTransport(vendor="eastmoney", operation="news", case="success")
    cls2 = CLSAShareAdapter(cls_transport2, clock=FixedClock(AS_OF))
    em2 = EastmoneyAShareAdapter(
        em_transport2, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF)
    )
    registry2 = VendorRegistry()
    registry2.register(VendorId.CLS, cls2)
    registry2.register(VendorId.EASTMONEY, em2)
    router2 = _build_router(
        registry2,
        {(Market.A_SHARE, DataCategory.NEWS): (VendorId.CLS, VendorId.EASTMONEY)},
    )

    async def _call_market(
        adapter: CategoryProvider,
    ) -> ProviderSuccess[tuple[NewsItem, ...]]:
        if not isinstance(adapter, AShareNewsProvider):
            raise DataContractError("bad", details={"rule": "protocol"})
        return await adapter.get_news(None, start=start, end=AS_OF, limit=10, as_of=AS_OF)

    market_res = await router2.execute(
        market=Market.A_SHARE,
        category=DataCategory.NEWS,
        call=_call_market,
        operation_name="a_share.news.v1",
        request_fingerprint=build_a_share_fingerprint("a_share.news.v1", "market", {}, AS_OF),
        instrument=None,
        as_of=AS_OF,
        tool_policy=SNAPSHOT_SUMMARY_POLICY,
        bypass_cache=True,
        cache_codec=None,
        result_validator=None,
    )
    assert market_res.ok is True
    assert market_res.meta is not None
    assert market_res.meta.vendor is VendorId.CLS
    assert cls_transport2.requests
    assert em_transport2.requests == []
