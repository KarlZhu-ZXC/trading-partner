"""Phase 1E E3 hardening regression tests (blocking Codex findings)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs

import pytest

from a_share_fixture_transport import ScriptedHttpTransport
from application.dto.a_share_provenance import provenance_dtos
from application.dto.provider_resilience import RateLimitDecision
from application.dto.provider_routing import (
    ProviderResultMeta,
    ProviderSuccess,
    RouterExecutionResult,
)
from application.dto.tool_envelope import WarningInfo
from application.ports.http_transport import HttpRequest, HttpResponse
from application.services.a_share_snapshot_service import (
    OP_ANNOUNCEMENTS,
    OP_CORPORATE_ACTIONS,
    OP_F10,
    OP_FUNDAMENTALS,
    OP_NEWS,
    OP_QUOTE,
    OP_STATEMENTS,
    AShareSnapshotResult,
    AShareSnapshotService,
)
from application.services.a_share_tool_policies import (
    SNAPSHOT_FULL_POLICY,
    SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY,
    SNAPSHOT_SUMMARY_POLICY,
)
from application.services.criticality_policy import CriticalityPolicy
from application.services.provider_router import ProviderRouter
from application.services.research_report_search_service import (
    OP_CONSENSUS,
    OP_REPORTS,
    ResearchReportSearchResult,
    ResearchReportSearchService,
)
from conftest import FixedClock, SequentialIdGenerator
from domain.a_share.enums import AShareSnapshotDetail, FinancialStatementType
from domain.a_share.models import (
    AnalystReportItem,
    AnnouncementItem,
    AShareQuote,
    ConsensusEstimate,
    DividendRecord,
    F10Section,
    FinancialStatementLine,
    FundamentalMetric,
    NewsItem,
    UnlockRecord,
)
from domain.common.enums import (
    AppEnvironment,
    AssetType,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    LogLevel,
    Market,
    ProviderAttemptOutcome,
    ReliabilityLevel,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError, NoMarketData, PartialDataError
from domain.instruments.models import Instrument
from infrastructure.config.settings import (
    PACKAGED_CNINFO_ORG_MAP_PATH,
    PROJECT_ROOT,
    AppSettings,
)
from infrastructure.providers.a_share.cls import CLSAShareAdapter
from infrastructure.providers.a_share.cninfo import CninfoAShareAdapter
from infrastructure.providers.a_share.cninfo_org_map import (
    MIN_ENTRY_COUNT,
    load_cninfo_org_map,
    validate_org_map_document,
)
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
from infrastructure.providers.a_share.exchanges import SzseAShareDisclosureAdapter
from infrastructure.providers.a_share.iwencai import IwencaiAShareAdapter
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
AS_OF_QUOTE = datetime(2024, 1, 16, 6, 30, 10, tzinfo=UTC)
_CALENDAR_PATH = PROJECT_ROOT / "config" / "a_share_trading_calendar.v1.json"
_ORG_MAP_PATH = PROJECT_ROOT / "config" / "cninfo_org_map.v1.json"
_JSON = {"content-type": "application/json; charset=utf-8"}


def _equity(symbol: str = "600519.SH", exchange: str = "SSE") -> Instrument:
    return Instrument(
        instrument_id=f"equity:A_SHARE:{symbol}",
        symbol=symbol,
        name="test",
        market=Market.A_SHARE,
        exchange=exchange,
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
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


def _quote(instrument: Instrument) -> AShareQuote:
    return AShareQuote(
        instrument_id=instrument.instrument_id,
        quote_at=AS_OF_QUOTE,
        session=TradingSession.CLOSED,
        last=Decimal("1600"),
        open=None,
        high=None,
        low=None,
        previous_close=None,
        change=None,
        change_percent=None,
        volume_shares=None,
        turnover_amount_cny=None,
        turnover_rate=None,
        pe_ttm=None,
        pb=None,
        total_market_cap_cny=None,
        float_market_cap_cny=None,
        limit_up_price=None,
        limit_down_price=None,
    )


class _StaticChain:
    def __init__(self, chains: dict[tuple[Market, DataCategory], tuple[VendorId, ...]]) -> None:
        self._chains = chains

    def chain_for(self, market: Market, category: DataCategory) -> tuple[VendorId, ...]:
        return self._chains.get((market, category), ())

    def all_categories(self, market: Market) -> MappingProxyType:
        out: dict[DataCategory, tuple[VendorId, ...]] = {}
        for (m, c), chain in self._chains.items():
            if m is market:
                out[c] = chain
        return MappingProxyType(out)


class _MemCache:
    def get(self, key: str) -> Any:
        return None

    def set(self, key: str, entry: object) -> None:
        return None

    def delete(self, key: str) -> None:
        return None


class _MemHealth:
    def record_success(self, *a: object, **k: object) -> None:
        return None

    def record_failure(self, *a: object, **k: object) -> None:
        return None

    def set_circuit_state(self, *a: object, **k: object) -> None:
        return None

    def get(self, *a: object, **k: object) -> object:
        raise NotImplementedError


class _AllowRateLimiter:
    async def acquire(
        self,
        vendor: VendorId,
        category: DataCategory,
        max_wait_seconds: float,
    ) -> RateLimitDecision:
        del vendor, category, max_wait_seconds
        return RateLimitDecision(allowed=True, remaining=10, reset_at=AS_OF, limit_per_window=100)


def _settings() -> AppSettings:
    return AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="e3-harden",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="e3-harden",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
        provider_retry_max_attempts=1,
        enable_provider_cache=False,
        enable_circuit_breaker=False,
    )


def _meta(
    category: DataCategory,
    *,
    as_of: datetime = AS_OF,
    vendor: VendorId = VendorId.EASTMONEY,
) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=vendor,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=as_of,
        fetched_at=as_of,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.CLOSED,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=(),
    )


# --- HttpRequest secret-safe repr ---------------------------------------------


def test_http_request_repr_never_exposes_values_or_body() -> None:
    secret = "access_token_super_secret_xyz"
    req = HttpRequest(
        method="POST",
        url="https://openapi.iwencai.com/v1/report/search?token=should-not-show",
        params={"q": "白酒", "access_token": secret},
        headers={"X-Api-Key": secret, "Content-Type": "application/json"},
        body=json.dumps({"access_token": secret, "query": "白酒"}).encode(),
        timeout_seconds=15.0,
    )
    text = repr(req)
    assert secret not in text
    assert "白酒" not in text
    assert "should-not-show" not in text
    assert "token=" not in text
    assert "body_len=" in text
    assert "param_keys=" in text
    assert "header_keys=" in text
    assert "openapi.iwencai.com" in text
    assert "/v1/report/search" in text
    assert req.params["access_token"] == secret  # values still available for wire


# --- Criticality policies -----------------------------------------------------


def test_snapshot_policies_criticality_semantics() -> None:
    policy = CriticalityPolicy()
    assert (
        policy.for_category(DataCategory.FUNDAMENTALS, SNAPSHOT_SUMMARY_POLICY)
        is DataCriticality.OPTIONAL
    )
    assert (
        policy.for_category(DataCategory.FUNDAMENTALS, SNAPSHOT_FULL_POLICY)
        is DataCriticality.OPTIONAL
    )
    assert (
        policy.for_category(DataCategory.FINANCIAL_STATEMENTS, SNAPSHOT_FULL_POLICY)
        is DataCriticality.OPTIONAL
    )
    assert (
        policy.for_category(DataCategory.FUNDAMENTALS, SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY)
        is DataCriticality.OPTIONAL
    )
    for pol in (SNAPSHOT_SUMMARY_POLICY, SNAPSHOT_FULL_POLICY):
        assert policy.for_category(DataCategory.ANNOUNCEMENTS, pol) is DataCriticality.OPTIONAL
        assert policy.for_category(DataCategory.NEWS, pol) is DataCriticality.OPTIONAL
    assert (
        policy.for_category(DataCategory.CORPORATE_ACTIONS, SNAPSHOT_FULL_POLICY)
        is DataCriticality.OPTIONAL
    )


def test_snapshot_service_rejects_codec_without_codec_id() -> None:
    class BadCodec:
        pass

    with pytest.raises(DataContractError) as ei:
        AShareSnapshotService(
            router=object(),  # type: ignore[arg-type]
            clock=FixedClock(AS_OF),
            quote_codec=BadCodec(),  # type: ignore[arg-type]
            fundamentals_codec=fundamentals_codec(),
            f10_codec=f10_codec(),
            statements_codec=statements_codec(),
            announcements_codec=announcements_codec(),
            news_codec=news_codec(),
            corporate_actions_codec=corporate_actions_codec(),
        )
    assert ei.value.details["field"] == "quote_codec"


# --- TaskGroup structured concurrency ----------------------------------------


class _TrackingAdapter:
    """Fake multi-category adapter that tracks completion of each op."""

    vendor_id = VendorId.EASTMONEY
    provider_name = VendorId.EASTMONEY.value

    def __init__(
        self,
        *,
        quote_delay: float = 0.0,
        quote_fail: bool = False,
        fund_delay: float = 0.05,
        fund_fail: bool = False,
        stmt_delay: float = 0.05,
        stmt_fail: bool = False,
        empty_fund: bool = False,
        empty_stmt: bool = False,
    ) -> None:
        self.quote_delay = quote_delay
        self.quote_fail = quote_fail
        self.fund_delay = fund_delay
        self.fund_fail = fund_fail
        self.stmt_delay = stmt_delay
        self.stmt_fail = stmt_fail
        self.empty_fund = empty_fund
        self.empty_stmt = empty_stmt
        self.completed: list[str] = []
        self.started: list[str] = []
        self.active = 0

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in {
            DataCategory.MARKET_QUOTE,
            DataCategory.FUNDAMENTALS,
            DataCategory.FINANCIAL_STATEMENTS,
            DataCategory.ANNOUNCEMENTS,
            DataCategory.NEWS,
            DataCategory.CORPORATE_ACTIONS,
        }

    def is_configured(self) -> bool:
        return True

    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[AShareQuote]:
        self.started.append("quote")
        self.active += 1
        try:
            await asyncio.sleep(self.quote_delay)
            if self.quote_fail:
                raise NoMarketData(
                    "quote fail",
                    details={"vendor": self.vendor_id.value, "operation": "quote"},
                )
            return ProviderSuccess(
                value=_quote(instrument),
                meta=_meta(DataCategory.MARKET_QUOTE, as_of=as_of),
            )
        finally:
            self.active -= 1
            self.completed.append("quote")

    async def get_fundamentals(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[tuple[FundamentalMetric, ...]]:
        self.started.append("fundamentals")
        self.active += 1
        try:
            await asyncio.sleep(self.fund_delay)
            if self.fund_fail:
                raise NoMarketData(
                    "fund fail",
                    details={"vendor": self.vendor_id.value, "operation": "fundamentals"},
                )
            value: tuple[FundamentalMetric, ...] = ()
            if not self.empty_fund:
                value = (
                    FundamentalMetric(
                        name="eps",
                        value=Decimal("1"),
                        unit="CNY",
                        period_end=None,
                        published_at=as_of - timedelta(days=1),
                    ),
                )
            return ProviderSuccess(value=value, meta=_meta(DataCategory.FUNDAMENTALS, as_of=as_of))
        finally:
            self.active -= 1
            self.completed.append("fundamentals")

    async def get_f10_sections(
        self, instrument: Instrument, *, sections: tuple[str, ...], as_of: datetime
    ) -> ProviderSuccess[tuple[Any, ...]]:
        self.started.append("f10")
        self.active += 1
        try:
            await asyncio.sleep(0.01)
            return ProviderSuccess(value=(), meta=_meta(DataCategory.FUNDAMENTALS, as_of=as_of))
        finally:
            self.active -= 1
            self.completed.append("f10")

    async def get_financial_statements(
        self,
        instrument: Instrument,
        *,
        statement_types: tuple[FinancialStatementType, ...],
        periods: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[FinancialStatementLine, ...]]:
        self.started.append("statements")
        self.active += 1
        try:
            await asyncio.sleep(self.stmt_delay)
            if self.stmt_fail:
                raise NoMarketData(
                    "stmt fail",
                    details={"vendor": self.vendor_id.value, "operation": "statements"},
                )
            value: tuple[FinancialStatementLine, ...] = ()
            if not self.empty_stmt:
                value = (
                    FinancialStatementLine(
                        statement_type=FinancialStatementType.INCOME_STATEMENT,
                        period_end=datetime(2023, 12, 31, tzinfo=UTC).date(),
                        published_at=as_of - timedelta(days=1),
                        item_code="NETPROFIT",
                        item_name="净利润",
                        value=Decimal("1"),
                        unit="CNY",
                    ),
                )
            return ProviderSuccess(
                value=value,
                meta=_meta(DataCategory.FINANCIAL_STATEMENTS, as_of=as_of),
            )
        finally:
            self.active -= 1
            self.completed.append("statements")

    async def get_announcements(
        self, instrument: Instrument, *, limit: int, as_of: datetime
    ) -> ProviderSuccess[tuple[Any, ...]]:
        self.started.append("announcements")
        self.active += 1
        try:
            await asyncio.sleep(0.02)
            return ProviderSuccess(value=(), meta=_meta(DataCategory.ANNOUNCEMENTS, as_of=as_of))
        finally:
            self.active -= 1
            self.completed.append("announcements")

    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        start: datetime,
        end: datetime,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Any, ...]]:
        self.started.append("news")
        self.active += 1
        try:
            await asyncio.sleep(0.02)
            return ProviderSuccess(value=(), meta=_meta(DataCategory.NEWS, as_of=as_of))
        finally:
            self.active -= 1
            self.completed.append("news")

    async def get_corporate_actions(
        self,
        instrument: Instrument,
        *,
        start: object,
        end: object,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[UnlockRecord | DividendRecord, ...]]:
        self.started.append("actions")
        self.active += 1
        try:
            await asyncio.sleep(0.02)
            return ProviderSuccess(
                value=(), meta=_meta(DataCategory.CORPORATE_ACTIONS, as_of=as_of)
            )
        finally:
            self.active -= 1
            self.completed.append("actions")


def _service_with_adapter(adapter: _TrackingAdapter) -> AShareSnapshotService:
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, adapter)
    engine = ProviderRouterEngine(
        registry=registry,
        cache_store=_MemCache(),  # type: ignore[arg-type]
        health_store=_MemHealth(),  # type: ignore[arg-type]
        rate_limiter=_AllowRateLimiter(),  # type: ignore[arg-type]
        circuit_breaker=CircuitBreaker(FixedClock(AS_OF)),
        clock=FixedClock(AS_OF),
        settings=_settings(),
    )
    router = ProviderRouter(
        engine=engine,
        chain_config=_StaticChain(
            {
                (Market.A_SHARE, DataCategory.MARKET_QUOTE): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.FUNDAMENTALS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.FINANCIAL_STATEMENTS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.ANNOUNCEMENTS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.NEWS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.CORPORATE_ACTIONS): (VendorId.EASTMONEY,),
            }
        ),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )
    return AShareSnapshotService(
        router=router,
        clock=FixedClock(AS_OF),
        quote_codec=quote_codec(),
        fundamentals_codec=fundamentals_codec(),
        f10_codec=f10_codec(),
        statements_codec=statements_codec(),
        announcements_codec=announcements_codec(),
        news_codec=news_codec(),
        corporate_actions_codec=corporate_actions_codec(),
    )


@pytest.mark.asyncio
async def test_taskgroup_quote_fail_awaits_optional_tasks() -> None:
    adapter = _TrackingAdapter(quote_fail=True, fund_delay=0.05, fund_fail=True)
    service = _service_with_adapter(adapter)
    result = await service.get_snapshot(_equity(), AS_OF_QUOTE, AShareSnapshotDetail.SUMMARY)
    assert result.ok is False
    # All started tasks completed (no orphans) before return.
    assert adapter.active == 0
    assert "quote" in adapter.completed
    assert "fundamentals" in adapter.completed
    assert "announcements" in adapter.completed
    assert "news" in adapter.completed


@pytest.mark.asyncio
async def test_taskgroup_full_required_component_fail_no_orphans() -> None:
    adapter = _TrackingAdapter(stmt_fail=True, fund_delay=0.03, stmt_delay=0.04)
    service = _service_with_adapter(adapter)
    result = await service.get_snapshot(_equity(), AS_OF_QUOTE, AShareSnapshotDetail.FULL)
    assert result.ok is True
    assert result.error is None
    assert adapter.active == 0
    # FULL components were started and finished.
    for name in (
        "quote",
        "fundamentals",
        "statements",
        "f10",
        "announcements",
        "news",
        "actions",
    ):
        assert name in adapter.completed
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)


@pytest.mark.asyncio
async def test_full_equity_rejects_empty_fundamentals_and_statements() -> None:
    adapter = _TrackingAdapter(empty_fund=True, empty_stmt=True)
    service = _service_with_adapter(adapter)
    result = await service.get_snapshot(_equity(), AS_OF_QUOTE, AShareSnapshotDetail.FULL)
    assert result.ok is True
    assert result.data is not None
    assert result.error is None
    assert result.data.fundamentals == ()
    assert result.data.statements == ()
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    # Criticality on failed CORE category surfaces via router result path.
    assert adapter.active == 0


@pytest.mark.asyncio
async def test_summary_fundamentals_optional_criticality_on_router_result() -> None:
    """Optional fundamentals failure must not fail the summary snapshot."""
    adapter = _TrackingAdapter(fund_fail=True)
    service = _service_with_adapter(adapter)
    result = await service.get_snapshot(_equity(), AS_OF_QUOTE, AShareSnapshotDetail.SUMMARY)
    assert result.ok is True
    assert result.data is not None
    assert result.data.fundamentals == ()
    assert tuple(item.component.value for item in result.provenance) == (
        "quote",
        "announcements",
        "news",
    )
    assert result.data.provenance == provenance_dtos(result.provenance)
    assert all(item.meta.vendor is VendorId.EASTMONEY for item in result.provenance)
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)


# --- Warning propagation (required success + required failure) ----------------


def _wi(code: str, message: str = "test warning") -> WarningInfo:
    return WarningInfo(code=code, message=message, details={})


def _ok_result[T](
    value: T,
    category: DataCategory,
    *,
    result_warnings: tuple[WarningInfo, ...] = (),
    meta_warnings: tuple[str, ...] = (),
) -> RouterExecutionResult[T]:
    meta = ProviderResultMeta(
        vendor=VendorId.EASTMONEY,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.UNKNOWN,
        session=TradingSession.CLOSED,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=meta_warnings,
    )
    return RouterExecutionResult(
        value=value,
        ok=True,
        criticality=DataCriticality.CORE,
        meta=meta,
        attempts=(),
        warnings=result_warnings,
        error=None,
    )


def _fail_result(
    *,
    category: DataCategory,
    result_warnings: tuple[WarningInfo, ...] = (),
    message: str = "component failed",
) -> RouterExecutionResult[object]:
    del category  # criticality path only; failure has meta=None
    return RouterExecutionResult(
        value=None,
        ok=False,
        criticality=DataCriticality.CORE,
        meta=None,
        attempts=(),
        warnings=result_warnings,
        error=NoMarketData(message, details={"vendor": "eastmoney"}),
    )


class _ScriptedOpRouter:
    """Return canned RouterExecutionResult by operation_name (no network)."""

    def __init__(self, by_op: dict[str, RouterExecutionResult[object]]) -> None:
        self.by_op = by_op
        self.calls: list[str] = []

    async def execute(self, **kwargs: object) -> RouterExecutionResult[object]:
        op = kwargs["operation_name"]
        assert isinstance(op, str)
        self.calls.append(op)
        if op not in self.by_op:
            raise AssertionError(f"unexpected operation {op!r}; known={sorted(self.by_op)}")
        return self.by_op[op]


def _service_with_scripted_router(
    by_op: dict[str, RouterExecutionResult[object]],
) -> tuple[AShareSnapshotService, _ScriptedOpRouter]:
    router = _ScriptedOpRouter(by_op)
    service = AShareSnapshotService(
        router=router,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        quote_codec=quote_codec(),
        fundamentals_codec=fundamentals_codec(),
        f10_codec=f10_codec(),
        statements_codec=statements_codec(),
        announcements_codec=announcements_codec(),
        news_codec=news_codec(),
        corporate_actions_codec=corporate_actions_codec(),
    )
    return service, router


def _empty_optional_ops() -> dict[str, RouterExecutionResult[object]]:
    """Successful empty payloads for optional snapshot components."""
    return {
        OP_ANNOUNCEMENTS: _ok_result((), DataCategory.ANNOUNCEMENTS),
        OP_NEWS: _ok_result((), DataCategory.NEWS),
        OP_F10: _ok_result((), DataCategory.FUNDAMENTALS),
        OP_CORPORATE_ACTIONS: _ok_result((), DataCategory.CORPORATE_ACTIONS),
    }


@pytest.mark.asyncio
async def test_successful_quote_warnings_and_meta_warnings_reach_snapshot() -> None:
    """Required quote RouterExecutionResult.warnings + meta.warnings merge on success."""
    instrument = _equity()
    quote_w = _wi("QUOTE_RESULT_WARNING", "from router result.warnings")
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_QUOTE: _ok_result(
            _quote(instrument),
            DataCategory.MARKET_QUOTE,
            result_warnings=(quote_w,),
            meta_warnings=("PUBLICATION_TIME_UNKNOWN_EXCLUDED",),
        ),
        OP_FUNDAMENTALS: _ok_result((), DataCategory.FUNDAMENTALS),
        **_empty_optional_ops(),
    }
    service, _router = _service_with_scripted_router(by_op)
    result = await service.get_snapshot(instrument, AS_OF_QUOTE, AShareSnapshotDetail.SUMMARY)
    assert result.ok is True
    codes = [w.code for w in result.warnings]
    assert "QUOTE_RESULT_WARNING" in codes
    assert "PUBLICATION_TIME_UNKNOWN_EXCLUDED" in codes
    # Deterministic de-dupe: re-merge must not double-count identical WarningInfo.
    assert codes.count("QUOTE_RESULT_WARNING") == 1
    assert codes.count("PUBLICATION_TIME_UNKNOWN_EXCLUDED") == 1
    expected_ops = (OP_QUOTE, OP_FUNDAMENTALS, OP_ANNOUNCEMENTS, OP_NEWS)
    assert tuple(item.component.value for item in result.provenance) == (
        "quote",
        "fundamentals",
        "announcements",
        "news",
    )
    assert tuple(item.meta for item in result.provenance) == tuple(
        by_op[operation].meta for operation in expected_ops
    )
    assert result.data is not None
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_snapshot_provenance_order_ignores_async_completion_order() -> None:
    instrument = _equity()
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_QUOTE: _ok_result(_quote(instrument), DataCategory.MARKET_QUOTE),
        OP_FUNDAMENTALS: _ok_result((), DataCategory.FUNDAMENTALS),
        OP_ANNOUNCEMENTS: _ok_result((), DataCategory.ANNOUNCEMENTS),
        OP_NEWS: _ok_result((), DataCategory.NEWS),
    }

    class _OutOfOrderRouter(_ScriptedOpRouter):
        async def execute(self, **kwargs: object) -> RouterExecutionResult[object]:
            operation = kwargs["operation_name"]
            if operation == OP_QUOTE:
                await asyncio.sleep(0.01)
            return await super().execute(**kwargs)

    router = _OutOfOrderRouter(by_op)
    service = AShareSnapshotService(
        router=router,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        quote_codec=quote_codec(),
        fundamentals_codec=fundamentals_codec(),
        f10_codec=f10_codec(),
        statements_codec=statements_codec(),
        announcements_codec=announcements_codec(),
        news_codec=news_codec(),
        corporate_actions_codec=corporate_actions_codec(),
    )
    result = await service.get_snapshot(instrument, AS_OF_QUOTE, AShareSnapshotDetail.SUMMARY)
    assert result.ok is True and result.data is not None
    assert tuple(item.component.value for item in result.provenance) == (
        "quote",
        "fundamentals",
        "announcements",
        "news",
    )
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_consensus_meta_publication_warning_reaches_report_search_result() -> None:
    """Consensus meta.warnings elevate like report path; recognized code once only.

    Publication code is meta-only (not in result.warnings) so this fails if
    consensus meta.warnings are omitted. Duplicate meta codes must not double.
    """
    instrument = _equity()
    consensus_w = _wi("CONSENSUS_RESULT_WARNING", "from router result.warnings")
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_REPORTS: _ok_result((), DataCategory.RESEARCH_REPORTS),
        OP_CONSENSUS: _ok_result(
            (),
            DataCategory.RESEARCH_REPORTS,
            result_warnings=(consensus_w,),
            meta_warnings=(
                "PUBLICATION_TIME_UNKNOWN_EXCLUDED",
                "PUBLICATION_TIME_UNKNOWN_EXCLUDED",
                "UNRECOGNIZED_META_WARNING",
            ),
        ),
    }
    router = _ScriptedOpRouter(by_op)
    service = ResearchReportSearchService(
        router=router,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        secret_redactor=DefaultSecretRedactor(),
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    result = await service.search(
        text="白酒",
        instrument=instrument,
        include_consensus=True,
        as_of=AS_OF,
    )
    assert result.ok is True
    codes = [w.code for w in result.warnings]
    assert "CONSENSUS_RESULT_WARNING" in codes
    assert "PUBLICATION_TIME_UNKNOWN_EXCLUDED" in codes
    assert codes.count("CONSENSUS_RESULT_WARNING") == 1
    assert codes.count("PUBLICATION_TIME_UNKNOWN_EXCLUDED") == 1
    # Only the recognized publication meta warning is elevated.
    assert "UNRECOGNIZED_META_WARNING" not in codes
    assert OP_REPORTS in router.calls
    assert OP_CONSENSUS in router.calls
    assert tuple(item.component.value for item in result.provenance) == (
        "reports",
        "consensus",
    )
    assert tuple(item.meta for item in result.provenance) == (
        by_op[OP_REPORTS].meta,
        by_op[OP_CONSENSUS].meta,
    )
    assert result.data is not None
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_failed_required_quote_warnings_reach_failed_result() -> None:
    quote_w = _wi("QUOTE_FAIL_WARNING", "quote chain degraded then failed")
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_QUOTE: _fail_result(
            category=DataCategory.MARKET_QUOTE,
            result_warnings=(quote_w,),
            message="quote fail",
        ),
        # TaskGroup still starts optional components for SUMMARY.
        OP_FUNDAMENTALS: _ok_result((), DataCategory.FUNDAMENTALS),
        OP_ANNOUNCEMENTS: _ok_result((), DataCategory.ANNOUNCEMENTS),
        OP_NEWS: _ok_result((), DataCategory.NEWS),
    }
    service, _router = _service_with_scripted_router(by_op)
    result = await service.get_snapshot(_equity(), AS_OF_QUOTE, AShareSnapshotDetail.SUMMARY)
    assert result.ok is False
    assert result.error is not None
    assert any(w.code == "QUOTE_FAIL_WARNING" for w in result.warnings)
    assert result.warnings.count(quote_w) == 1
    assert tuple(item.component.value for item in result.provenance) == (
        "fundamentals",
        "announcements",
        "news",
    )
    assert tuple(item.meta for item in result.provenance) == (
        by_op[OP_FUNDAMENTALS].meta,
        by_op[OP_ANNOUNCEMENTS].meta,
        by_op[OP_NEWS].meta,
    )


@pytest.mark.asyncio
async def test_failed_required_fundamentals_warnings_reach_failed_full_result() -> None:
    instrument = _equity()
    fund_w = _wi("FUND_FAIL_WARNING", "required fundamentals failed")
    # Quote succeeds (and may carry its own warning that must still be present).
    quote_w = _wi("QUOTE_OK_WARNING")
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_QUOTE: _ok_result(
            _quote(instrument),
            DataCategory.MARKET_QUOTE,
            result_warnings=(quote_w,),
        ),
        OP_FUNDAMENTALS: _fail_result(
            category=DataCategory.FUNDAMENTALS,
            result_warnings=(fund_w,),
            message="fund fail",
        ),
        # Other FULL components still start under TaskGroup; canned ok is fine.
        OP_STATEMENTS: _ok_result(
            (
                FinancialStatementLine(
                    statement_type=FinancialStatementType.INCOME_STATEMENT,
                    period_end=datetime(2023, 12, 31, tzinfo=UTC).date(),
                    published_at=AS_OF - timedelta(days=1),
                    item_code="NETPROFIT",
                    item_name="净利润",
                    value=Decimal("1"),
                    unit="CNY",
                ),
            ),
            DataCategory.FINANCIAL_STATEMENTS,
        ),
        **_empty_optional_ops(),
    }
    service, _router = _service_with_scripted_router(by_op)
    result = await service.get_snapshot(instrument, AS_OF_QUOTE, AShareSnapshotDetail.FULL)
    assert result.ok is True
    assert result.error is None
    assert result.data is not None
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    codes = [w.code for w in result.warnings]
    assert "FUND_FAIL_WARNING" in codes
    assert "QUOTE_OK_WARNING" in codes  # earlier success still propagated
    assert codes.count("FUND_FAIL_WARNING") == 1


@pytest.mark.asyncio
async def test_failed_required_statements_warnings_reach_failed_full_result() -> None:
    instrument = _equity()
    stmt_w = _wi("STMT_FAIL_WARNING", "required statements failed")
    fund_ok_w = _wi("FUND_OK_WARNING")
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_QUOTE: _ok_result(_quote(instrument), DataCategory.MARKET_QUOTE),
        OP_FUNDAMENTALS: _ok_result(
            (
                FundamentalMetric(
                    name="eps",
                    value=Decimal("1"),
                    unit="CNY",
                    period_end=None,
                    published_at=AS_OF - timedelta(days=1),
                ),
            ),
            DataCategory.FUNDAMENTALS,
            result_warnings=(fund_ok_w,),
        ),
        OP_STATEMENTS: _fail_result(
            category=DataCategory.FINANCIAL_STATEMENTS,
            result_warnings=(stmt_w,),
            message="stmt fail",
        ),
        **_empty_optional_ops(),
    }
    service, _router = _service_with_scripted_router(by_op)
    result = await service.get_snapshot(instrument, AS_OF_QUOTE, AShareSnapshotDetail.FULL)
    assert result.ok is True
    assert result.error is None
    assert result.data is not None
    assert any(w.code == "PARTIAL_A_SHARE_SNAPSHOT" for w in result.warnings)
    codes = [w.code for w in result.warnings]
    assert "STMT_FAIL_WARNING" in codes
    assert "FUND_OK_WARNING" in codes
    assert codes.count("STMT_FAIL_WARNING") == 1


# --- Service publication defense ----------------------------------------------


@pytest.mark.asyncio
async def test_service_rejects_future_and_historical_unknown_publication() -> None:
    class _MaliciousFund:
        vendor_id = VendorId.EASTMONEY
        provider_name = "eastmoney"

        def supports(self, market: Market, category: DataCategory) -> bool:
            return category is DataCategory.FUNDAMENTALS

        def is_configured(self) -> bool:
            return True

        async def get_fundamentals(self, instrument: Instrument, as_of: datetime):
            return ProviderSuccess(
                value=(
                    FundamentalMetric(
                        name="eps",
                        value=Decimal("1"),
                        unit="CNY",
                        period_end=None,
                        published_at=as_of + timedelta(days=1),
                    ),
                ),
                meta=_meta(DataCategory.FUNDAMENTALS),
            )

    class _UnknownHist:
        vendor_id = VendorId.EASTMONEY
        provider_name = "eastmoney"

        def supports(self, market: Market, category: DataCategory) -> bool:
            return category is DataCategory.FUNDAMENTALS

        def is_configured(self) -> bool:
            return True

        async def get_fundamentals(self, instrument: Instrument, as_of: datetime):
            return ProviderSuccess(
                value=(
                    FundamentalMetric(
                        name="eps",
                        value=Decimal("1"),
                        unit="CNY",
                        period_end=None,
                        published_at=None,
                    ),
                ),
                meta=_meta(DataCategory.FUNDAMENTALS),
            )

    async def _run(adapter: object, as_of: datetime) -> RouterExecutionResult[object]:
        registry = VendorRegistry()
        registry.register(VendorId.EASTMONEY, adapter)  # type: ignore[arg-type]
        engine = ProviderRouterEngine(
            registry=registry,
            cache_store=_MemCache(),  # type: ignore[arg-type]
            health_store=_MemHealth(),  # type: ignore[arg-type]
            rate_limiter=_AllowRateLimiter(),  # type: ignore[arg-type]
            circuit_breaker=CircuitBreaker(FixedClock(AS_OF)),
            clock=FixedClock(AS_OF),
            settings=_settings(),
        )
        router = ProviderRouter(
            engine=engine,
            chain_config=_StaticChain(
                {(Market.A_SHARE, DataCategory.FUNDAMENTALS): (VendorId.EASTMONEY,)}
            ),
            clock=FixedClock(AS_OF),
            id_generator=SequentialIdGenerator(),
            secret_redactor=DefaultSecretRedactor(),
            criticality_policy=CriticalityPolicy(),
        )
        service = AShareSnapshotService(
            router=router,
            clock=FixedClock(AS_OF),
            quote_codec=quote_codec(),
            fundamentals_codec=fundamentals_codec(),
            f10_codec=f10_codec(),
            statements_codec=statements_codec(),
            announcements_codec=announcements_codec(),
            news_codec=news_codec(),
            corporate_actions_codec=corporate_actions_codec(),
        )
        # Call private fetch to isolate validator (quote not needed).
        return await service._fetch_fundamentals(  # noqa: SLF001
            _equity(),
            as_of,
            now=AS_OF,
            require_non_empty=False,
            tool_policy=SNAPSHOT_SUMMARY_POLICY,
        )

    future_res = await _run(_MaliciousFund(), AS_OF)
    assert future_res.ok is False
    assert future_res.criticality is DataCriticality.OPTIONAL
    assert any(a.outcome is ProviderAttemptOutcome.CONTRACT_ERROR for a in future_res.attempts)

    hist = datetime(2020, 1, 1, tzinfo=UTC)
    hist_res = await _run(_UnknownHist(), hist)
    assert hist_res.ok is False
    assert any(a.outcome is ProviderAttemptOutcome.CONTRACT_ERROR for a in hist_res.attempts)


# --- CNINFO org map -----------------------------------------------------------


def test_cninfo_org_map_completeness_and_representative_codes() -> None:
    mapping = load_cninfo_org_map(_ORG_MAP_PATH)
    assert len(mapping) >= MIN_ENTRY_COUNT
    # SSE, SZSE main, GEM, STAR, BSE
    assert mapping["600519"] == "gssh0600519"
    assert mapping["000001"] == "gssz0000001"
    assert mapping["300750"] == "GD165627"
    assert "688001" in mapping
    assert mapping["920000"] == "gfbj0832000"
    doc = json.loads(_ORG_MAP_PATH.read_text(encoding="utf-8"))
    validate_org_map_document(doc)
    assert "cninfo.com.cn" in "".join(doc["source_urls"])


def test_cninfo_org_map_wheel_resource_path_constant() -> None:
    # Packaged path constant exists for force-include; source is project config.
    assert PACKAGED_CNINFO_ORG_MAP_PATH.name == "cninfo_org_map.v1.json"
    assert _ORG_MAP_PATH.is_file()


@pytest.mark.asyncio
async def test_cninfo_request_construction_for_representative_symbols() -> None:
    cases = [
        ("600519.SH", "SSE", "gssh0600519", "sse"),
        ("000001.SZ", "SZSE", "gssz0000001", "szse"),
        ("300750.SZ", "SZSE", "GD165627", "szse"),
        ("688001.SH", "SSE", None, "sse"),  # org from map
        ("920001.BJ", "BSE", None, "bjse"),
    ]
    for symbol, exchange, expected_org, column in cases:
        code6 = symbol.split(".")[0]
        transport = ScriptedHttpTransport(
            responses=[
                HttpResponse(
                    status_code=200,
                    headers=_JSON,
                    body=b'{"announcements":[]}',
                )
            ]
        )
        adapter = CninfoAShareAdapter(transport, clock=FixedClock(AS_OF), org_id_map=None)
        await adapter.get_announcements(
            _equity(symbol=symbol, exchange=exchange), limit=5, as_of=AS_OF
        )
        assert transport.requests
        req = transport.requests[0]
        body = req.body.decode() if req.body else ""
        qs = parse_qs(body)
        stock = qs["stock"][0]
        assert stock.startswith(f"{code6},")
        org = stock.split(",", 1)[1]
        if expected_org is not None:
            assert org == expected_org
        else:
            assert org  # from full map
            assert org == load_cninfo_org_map()[code6]
        assert qs["column"][0] == column


@pytest.mark.asyncio
async def test_cninfo_missing_mapping_fails_before_network() -> None:
    transport = ScriptedHttpTransport(responses=[])
    adapter = CninfoAShareAdapter(
        transport, clock=FixedClock(AS_OF), org_id_map={"600519": "gssh0600519"}
    )
    with pytest.raises(DataContractError) as ei:
        await adapter.get_announcements(_equity(symbol="111111.SH"), limit=5, as_of=AS_OF)
    assert ei.value.details.get("rule") == "org_map_missing"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_szse_does_not_claim_bj() -> None:
    transport = ScriptedHttpTransport(responses=[])
    adapter = SzseAShareDisclosureAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError):
        await adapter.get_announcements(
            _equity(symbol="920001.BJ", exchange="BSE"), limit=5, as_of=AS_OF
        )
    assert transport.requests == []


# --- CLS instrument scope -----------------------------------------------------


@pytest.mark.asyncio
async def test_cls_instrument_scope_no_network() -> None:
    transport = ScriptedHttpTransport(responses=[])
    adapter = CLSAShareAdapter(transport, clock=FixedClock(AS_OF))
    with pytest.raises(NoMarketData) as ei:
        await adapter.get_news(
            _equity(),
            start=AS_OF - timedelta(days=1),
            end=AS_OF,
            limit=10,
            as_of=AS_OF,
        )
    assert ei.value.details.get("rule") == "instrument_unsupported"
    assert transport.requests == []


# --- Eastmoney report offset --------------------------------------------------


def _report_row(key: str, day: int, title: str | None = None) -> dict[str, str]:
    return {
        "infoCode": key,
        "title": title or f"t-{key}",
        "publishDate": f"2024-01-{day:02d} 09:00:00",
        "orgSName": "券商",
        "encodeUrl": key,
    }


class _PagedReportTransport:
    """Serve report pages by pageNo (reusable across offset searches)."""

    def __init__(self, pages: dict[int, list[dict[str, str]]]) -> None:
        self.pages = pages
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        page_no = int(request.params.get("pageNo", "1"))
        rows = self.pages.get(page_no, [])
        return HttpResponse(200, _JSON, json.dumps({"data": rows}).encode())


@pytest.mark.asyncio
async def test_eastmoney_report_offset_exact_semantics() -> None:
    # 15 sequential rows across two pages of page_size=limit=10.
    page1 = [_report_row(f"R{i:02d}", min(i + 1, 15)) for i in range(10)]
    page2 = [_report_row(f"R{i:02d}", min(i + 1, 15)) for i in range(10, 15)]
    transport = _PagedReportTransport({1: page1, 2: page2})
    em = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF))

    async def _search(offset: int, limit: int = 10) -> tuple[str, ...]:
        res = await em.search_reports(
            text=None,
            instrument=_equity(),
            industry_code=None,
            published_from=None,
            published_to=None,
            limit=limit,
            offset=offset,
            as_of=AS_OF,
        )
        return tuple(r.report_key for r in res.value)

    # offset 0 → first 10 of stream (then sorted for output)
    keys0 = await _search(0)
    assert len(keys0) == 10
    assert set(keys0) == {f"R{i:02d}" for i in range(10)}

    # offset 5 → stream rows 5..14 (requires page 1 + page 2; offset % page_size)
    keys5 = await _search(5)
    assert len(keys5) == 10
    assert set(keys5) == {f"R{i:02d}" for i in range(5, 15)}

    # offset == page size → second-page fill
    keys10 = await _search(10)
    assert set(keys10) == {f"R{i:02d}" for i in range(10, 15)}
    assert len(keys10) == 5
    assert any(r.params.get("pageNo") == "2" for r in transport.requests)


@pytest.mark.asyncio
async def test_eastmoney_report_duplicate_key_and_cutoff() -> None:
    future = "2024-02-01 09:00:00"
    # page_size follows limit (3) so second page is fetched for cross-page dedupe.
    rows_p1 = [
        _report_row("DUP", 10),
        _report_row("A", 11),
        {**_report_row("FUT", 12), "publishDate": future},
    ]
    rows_p2 = [
        _report_row("DUP", 10),  # duplicate across pages
        _report_row("B", 9),
        _report_row("C", 8),
    ]
    transport = ScriptedHttpTransport(
        responses=[
            HttpResponse(200, _JSON, json.dumps({"data": rows_p1}).encode()),
            HttpResponse(200, _JSON, json.dumps({"data": rows_p2}).encode()),
        ]
    )
    em = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF))
    res = await em.search_reports(
        text=None,
        instrument=_equity(),
        industry_code=None,
        published_from=None,
        published_to=None,
        limit=3,
        offset=0,
        as_of=AS_OF,
    )
    keys = [r.report_key for r in res.value]
    assert keys.count("DUP") == 1
    assert "FUT" not in keys
    # Deterministic order: published_at desc, report_key asc
    pubs = {r.report_key: r.published_at for r in res.value}
    assert keys == sorted(keys, key=lambda k: (-pubs[k].timestamp(), k))


@pytest.mark.asyncio
async def test_eastmoney_consensus_one_pair_per_row_no_double_count() -> None:
    body = {
        "data": [
            {
                "predictYear": 2024,
                "predictThisYearEps": "10",
                "year": 2024,
                "eps": "99",  # must not double-count same row
                "infoCode": "R1",
                "orgSName": "A券商",
            },
            {
                "predictYear": 2024,
                "predictThisYearEps": "12",
                "infoCode": "R2",
                "orgSName": "B券商",
            },
            {"predictYear": "bad", "predictThisYearEps": "1"},  # malformed ignore
            {"year": 2025, "eps": "20", "infoCode": "R3"},
        ]
    }
    transport = ScriptedHttpTransport(
        responses=[HttpResponse(200, _JSON, json.dumps(body).encode())]
    )
    em = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF))
    res = await em.get_consensus(_equity(), as_of=AS_OF)
    by_year = {e.fiscal_year: e for e in res.value}
    assert by_year[2024].institution_count == 2
    assert by_year[2024].mean == Decimal("11")
    assert by_year[2025].institution_count == 1


# --- Iwencai secret surfaces --------------------------------------------------


@pytest.mark.asyncio
async def test_iwencai_secret_never_on_diagnostic_surfaces() -> None:
    secret = "iwencai-live-key-must-not-leak-abc"
    transport = ScriptedHttpTransport(
        responses=[
            HttpResponse(
                status_code=500,
                headers=_JSON,
                body=b'{"error":"fail"}',
            )
        ]
    )
    adapter = IwencaiAShareAdapter(
        transport,
        clock=FixedClock(AS_OF),
        enabled=True,
        api_key=secret,
    )
    with pytest.raises(Exception) as ei:
        await adapter.search_reports(
            text="白酒研究",
            instrument=None,
            industry_code=None,
            published_from=None,
            published_to=None,
            limit=5,
            offset=0,
            as_of=AS_OF,
        )
    surfaces = [
        repr(adapter),
        repr(transport.requests[0]) if transport.requests else "",
        repr(ei.value),
        str(getattr(ei.value, "details", {})),
        str(ei.value),
    ]
    for s in surfaces:
        assert secret not in s
    # Wire body may contain key (required for auth) — only assert diagnostics above.
    assert transport.requests
    assert secret.encode() in (transport.requests[0].body or b"")


def test_current_window_seconds_validated_on_adapters() -> None:
    transport = ScriptedHttpTransport(responses=[])
    with pytest.raises(DataContractError):
        CninfoAShareAdapter(transport, current_window_seconds=-1)
    with pytest.raises(DataContractError):
        CninfoAShareAdapter(transport, current_window_seconds=True)  # type: ignore[arg-type]
    with pytest.raises(DataContractError):
        IwencaiAShareAdapter(transport, current_window_seconds=-5)


# --- Result wrapper invariants ------------------------------------------------


def test_snapshot_result_ok_invariants() -> None:
    with pytest.raises(DataContractError):
        AShareSnapshotResult(ok=True, data=None, warnings=(), error=None, provenance=())
    with pytest.raises(DataContractError):
        AShareSnapshotResult(
            ok=False,
            data=None,
            warnings=(),
            error=None,
            provenance=(),
        )
    with pytest.raises(DataContractError):
        AShareSnapshotResult(
            ok=False,
            data=None,
            warnings=("not-a-warning",),  # type: ignore[arg-type]
            error=DataContractError("x"),
            provenance=(),
        )
    with pytest.raises(DataContractError):
        ResearchReportSearchResult(
            ok=True,
            data=None,
            warnings=(),
            error=DataContractError("x"),
            provenance=(),
        )
    # A successful search has one product DTO; reports/consensus no longer form
    # a second result shape.


# --- Strict report search inputs (zero network) ------------------------------


@pytest.mark.asyncio
async def test_report_search_invalid_inputs_zero_network() -> None:
    class _NoCallRouter:
        async def execute(self, *a: object, **k: object) -> None:
            raise AssertionError("router must not be called for invalid inputs")

    service = ResearchReportSearchService(
        router=_NoCallRouter(),  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        secret_redactor=DefaultSecretRedactor(),
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    cases: list[dict[str, object]] = [
        {"text": 123},
        {"text": "x" * 501},
        {"industry_code": 1},
        {"industry_code": "c" * 65},
        {"instrument": "equity:A_SHARE:600519.SH"},
        {
            "instrument": Instrument(
                instrument_id="option:A_SHARE:10005123.SH",
                symbol="10005123.SH",
                name="opt",
                market=Market.A_SHARE,
                exchange="SSE",
                currency="CNY",
                timezone="Asia/Shanghai",
                asset_type=AssetType.OPTION,
            )
        },
        {"published_from": AS_OF},  # datetime not date
        {"published_to": AS_OF},
        {"include_consensus": 1},
        {"limit": True},
        {"offset": -1},
        {"text": "   "},  # blank only → no filters
    ]
    for kwargs in cases:
        with pytest.raises(DataContractError):
            await service.search(**kwargs)  # type: ignore[arg-type]


# --- Malicious provider / corrupted cache contract gates ---------------------


def _router_for(adapter: object, categories: list[DataCategory]) -> ProviderRouter:
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, adapter)  # type: ignore[arg-type]
    chains = {(Market.A_SHARE, c): (VendorId.EASTMONEY,) for c in categories}
    engine = ProviderRouterEngine(
        registry=registry,
        cache_store=_MemCache(),  # type: ignore[arg-type]
        health_store=_MemHealth(),  # type: ignore[arg-type]
        rate_limiter=_AllowRateLimiter(),  # type: ignore[arg-type]
        circuit_breaker=CircuitBreaker(FixedClock(AS_OF)),
        clock=FixedClock(AS_OF),
        settings=_settings(),
    )
    return ProviderRouter(
        engine=engine,
        chain_config=_StaticChain(chains),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )


def _snapshot_service(
    router: ProviderRouter,
    *,
    clock: FixedClock | None = None,
    current_window_seconds: int = 300,
) -> AShareSnapshotService:
    return AShareSnapshotService(
        router=router,
        clock=clock or FixedClock(AS_OF),
        quote_codec=quote_codec(),
        fundamentals_codec=fundamentals_codec(),
        f10_codec=f10_codec(),
        statements_codec=statements_codec(),
        announcements_codec=announcements_codec(),
        news_codec=news_codec(),
        corporate_actions_codec=corporate_actions_codec(),
        current_window_seconds=current_window_seconds,
    )


class _BaseMulti:
    vendor_id = VendorId.EASTMONEY
    provider_name = "eastmoney"

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE

    def is_configured(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_malicious_provider_wrong_category_element_dupes_unsorted() -> None:
    class _Malicious(_BaseMulti):
        async def get_quote(self, instrument: Instrument, as_of: datetime):
            return ProviderSuccess(
                value=_quote(instrument),
                meta=_meta(DataCategory.NEWS, as_of=as_of),
            )

        async def get_fundamentals(self, instrument: Instrument, as_of: datetime):
            return ProviderSuccess(
                value=("not-a-metric",),  # type: ignore[arg-type]
                meta=_meta(DataCategory.FUNDAMENTALS, as_of=as_of),
            )

        async def get_announcements(self, instrument: Instrument, *, limit: int, as_of: datetime):
            a1 = AnnouncementItem(
                announcement_key="K1",
                title="t1",
                published_at=as_of - timedelta(days=2),
                category=None,
                source_url="https://example.com/1",
                pdf_url=None,
            )
            a2 = AnnouncementItem(
                announcement_key="K1",
                title="t2",
                published_at=as_of - timedelta(days=1),
                category=None,
                source_url="https://example.com/2",
                pdf_url=None,
            )
            return ProviderSuccess(
                value=(a1, a2), meta=_meta(DataCategory.ANNOUNCEMENTS, as_of=as_of)
            )

        async def get_news(
            self,
            instrument: Instrument | None,
            *,
            start: datetime,
            end: datetime,
            limit: int,
            as_of: datetime,
        ):
            n1 = NewsItem(
                news_key="N1",
                title="old",
                summary=None,
                published_at=as_of - timedelta(days=2),
                source_name="s",
                source_url=None,
            )
            n2 = NewsItem(
                news_key="N2",
                title="new",
                summary=None,
                published_at=as_of - timedelta(days=1),
                source_name="s",
                source_url=None,
            )
            return ProviderSuccess(value=(n1, n2), meta=_meta(DataCategory.NEWS, as_of=as_of))

        async def get_f10_sections(
            self, instrument: Instrument, *, sections: tuple[str, ...], as_of: datetime
        ):
            return ProviderSuccess(
                value=(
                    F10Section(
                        section="unexpected",
                        title="x",
                        body="y",
                        as_of=as_of + timedelta(days=1),
                    ),
                ),
                meta=_meta(DataCategory.FUNDAMENTALS, as_of=as_of),
            )

        async def get_financial_statements(
            self,
            instrument: Instrument,
            *,
            statement_types: tuple[FinancialStatementType, ...],
            periods: int,
            as_of: datetime,
        ):
            line = FinancialStatementLine(
                statement_type=FinancialStatementType.INCOME_STATEMENT,
                period_end=datetime(2023, 12, 31, tzinfo=UTC).date(),
                published_at=as_of - timedelta(days=1),
                item_code="NETPROFIT",
                item_name="净利润",
                value=Decimal("1"),
                unit="CNY",
            )
            return ProviderSuccess(
                value=(line, line),
                meta=_meta(DataCategory.FINANCIAL_STATEMENTS, as_of=as_of),
            )

        async def get_corporate_actions(
            self,
            instrument: Instrument,
            *,
            start: object,
            end: object,
            as_of: datetime,
        ):
            u = UnlockRecord(
                unlock_date=datetime(2024, 1, 10, tzinfo=UTC).date(),
                published_at=as_of - timedelta(days=1),
                unlock_type="restricted",
                unlock_shares=100,
                tradable_shares=100,
                market_value_cny=None,
                source_vendor=VendorId.EASTMONEY,
                reliability=ReliabilityLevel.MEDIUM,
                is_authoritative=False,
            )
            return ProviderSuccess(
                value=(u, u), meta=_meta(DataCategory.CORPORATE_ACTIONS, as_of=as_of)
            )

    service = _snapshot_service(
        _router_for(
            _Malicious(),
            [
                DataCategory.MARKET_QUOTE,
                DataCategory.FUNDAMENTALS,
                DataCategory.FINANCIAL_STATEMENTS,
                DataCategory.ANNOUNCEMENTS,
                DataCategory.NEWS,
                DataCategory.CORPORATE_ACTIONS,
            ],
        )
    )
    res = await service.get_snapshot(_equity(), AS_OF_QUOTE, AShareSnapshotDetail.FULL)
    assert res.ok is False
    assert res.error is not None

    fund_res = await service._fetch_fundamentals(  # noqa: SLF001
        _equity(),
        AS_OF,
        now=AS_OF,
        require_non_empty=False,
        tool_policy=SNAPSHOT_SUMMARY_POLICY,
    )
    assert fund_res.ok is False
    assert any(a.outcome is ProviderAttemptOutcome.CONTRACT_ERROR for a in fund_res.attempts)

    for coro in (
        service._fetch_announcements(  # noqa: SLF001
            _equity(), AS_OF, now=AS_OF, detail=AShareSnapshotDetail.SUMMARY
        ),
        service._fetch_news(  # noqa: SLF001
            _equity(), AS_OF, now=AS_OF, detail=AShareSnapshotDetail.SUMMARY
        ),
        service._fetch_statements(  # noqa: SLF001
            _equity(), AS_OF, now=AS_OF, require_non_empty=False
        ),
        service._fetch_actions(_equity(), AS_OF, now=AS_OF),  # noqa: SLF001
        service._fetch_f10(_equity(), AS_OF, now=AS_OF),  # noqa: SLF001
    ):
        component_res = await coro
        assert component_res.ok is False


@pytest.mark.asyncio
async def test_quote_identity_mismatch_rejected() -> None:
    class _WrongId(_BaseMulti):
        async def get_quote(self, instrument: Instrument, as_of: datetime):
            q = _quote(instrument)
            bad = AShareQuote(
                instrument_id="equity:A_SHARE:000001.SZ",
                quote_at=q.quote_at,
                session=q.session,
                last=q.last,
                open=None,
                high=None,
                low=None,
                previous_close=None,
                change=None,
                change_percent=None,
                volume_shares=None,
                turnover_amount_cny=None,
                turnover_rate=None,
                pe_ttm=None,
                pb=None,
                total_market_cap_cny=None,
                float_market_cap_cny=None,
                limit_up_price=None,
                limit_down_price=None,
            )
            return ProviderSuccess(value=bad, meta=_meta(DataCategory.MARKET_QUOTE, as_of=as_of))

    service = _snapshot_service(_router_for(_WrongId(), [DataCategory.MARKET_QUOTE]))
    res = await service._fetch_quote(_equity(), AS_OF_QUOTE, now=AS_OF)  # noqa: SLF001
    assert res.ok is False
    assert any(a.outcome is ProviderAttemptOutcome.CONTRACT_ERROR for a in res.attempts)


@pytest.mark.asyncio
async def test_malicious_report_search_contract_and_identity() -> None:
    class _BadReports:
        vendor_id = VendorId.EASTMONEY
        provider_name = "eastmoney"

        def supports(self, market: Market, category: DataCategory) -> bool:
            return category is DataCategory.RESEARCH_REPORTS

        def is_configured(self) -> bool:
            return True

        async def search_reports(self, **kwargs: object):
            as_of = kwargs["as_of"]
            assert isinstance(as_of, datetime)
            r1 = AnalystReportItem(
                report_key="R1",
                title="a",
                institution=None,
                analyst_names=(),
                published_at=as_of - timedelta(days=2),
                rating=None,
                target_price=None,
                eps_forecasts=(),
                source_url=None,
                pdf_url=None,
            )
            r2 = AnalystReportItem(
                report_key="R1",
                title="b",
                institution=None,
                analyst_names=(),
                published_at=as_of - timedelta(days=1),
                rating=None,
                target_price=None,
                eps_forecasts=(),
                source_url=None,
                pdf_url=None,
            )
            return ProviderSuccess(
                value=(r1, r2),
                meta=_meta(DataCategory.NEWS, as_of=as_of),
            )

        async def get_consensus(self, instrument: Instrument, *, as_of: datetime):
            return ProviderSuccess(
                value=(
                    ConsensusEstimate(
                        fiscal_year=2024,
                        metric="eps",
                        mean=Decimal("1"),
                        high=Decimal("2"),
                        low=Decimal("0.5"),
                        institution_count=2,
                    ),
                    ConsensusEstimate(
                        fiscal_year=2024,
                        metric="eps",
                        mean=Decimal("1.1"),
                        high=Decimal("2"),
                        low=Decimal("0.5"),
                        institution_count=1,
                    ),
                ),
                meta=_meta(DataCategory.RESEARCH_REPORTS, as_of=as_of),
            )

    service = ResearchReportSearchService(
        router=_router_for(_BadReports(), [DataCategory.RESEARCH_REPORTS]),
        clock=FixedClock(AS_OF),
        secret_redactor=DefaultSecretRedactor(),
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    result = await service.search(
        text="白酒", instrument=_equity(), include_consensus=False, as_of=AS_OF
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.reports == ()
    assert result.error is None

    class _OkReportsDupConsensus(_BadReports):
        async def search_reports(self, **kwargs: object):
            as_of = kwargs["as_of"]
            assert isinstance(as_of, datetime)
            return ProviderSuccess(
                value=(),
                meta=_meta(DataCategory.RESEARCH_REPORTS, as_of=as_of),
            )

    service2 = ResearchReportSearchService(
        router=_router_for(_OkReportsDupConsensus(), [DataCategory.RESEARCH_REPORTS]),
        clock=FixedClock(AS_OF),
        secret_redactor=DefaultSecretRedactor(),
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    result2 = await service2.search(
        text="白酒", instrument=_equity(), include_consensus=True, as_of=AS_OF
    )
    assert result2.ok is True
    assert result2.data is not None
    assert result2.data.consensus == ()


@pytest.mark.asyncio
async def test_report_all_settle_sanitizes_unexpected_and_retains_consensus() -> None:
    class _ConcurrentRouter:
        def __init__(self) -> None:
            self.consensus_finished = False

        async def execute(self, **kwargs: object) -> RouterExecutionResult[object]:
            operation = kwargs["operation_name"]
            if operation == OP_REPORTS:
                await asyncio.sleep(0.001)
                raise RuntimeError("provider body https://secret.example/raw-payload")
            assert operation == OP_CONSENSUS
            self.consensus_finished = True
            return RouterExecutionResult(
                value=(
                    ConsensusEstimate(
                        fiscal_year=2025,
                        metric="eps",
                        mean=Decimal("1"),
                        high=Decimal("2"),
                        low=Decimal("0.5"),
                        institution_count=1,
                    ),
                ),
                ok=True,
                criticality=DataCriticality.OPTIONAL,
                meta=_meta(DataCategory.RESEARCH_REPORTS, as_of=AS_OF),
                attempts=(),
                warnings=(WarningInfo(code="CONSENSUS_OK", message="kept", details={}),),
                error=None,
            )

    router = _ConcurrentRouter()
    service = ResearchReportSearchService(
        router=router,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        secret_redactor=DefaultSecretRedactor(),
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    result = await service.search(
        text="白酒", instrument=_equity(), include_consensus=True, as_of=AS_OF
    )
    assert router.consensus_finished is True
    assert result.ok is True
    assert result.data is not None
    assert len(result.data.consensus) == 1
    assert tuple(item.component.value for item in result.provenance) == ("consensus",)
    assert result.provenance[0].meta.category is DataCategory.RESEARCH_REPORTS
    assert result.data.provenance == provenance_dtos(result.provenance)
    assert tuple(item.code for item in result.warnings) == ("CONSENSUS_OK",)


@pytest.mark.asyncio
async def test_report_provenance_order_ignores_async_completion_order() -> None:
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_REPORTS: _ok_result((), DataCategory.RESEARCH_REPORTS),
        OP_CONSENSUS: _ok_result((), DataCategory.RESEARCH_REPORTS),
    }

    class _OutOfOrderReportsRouter(_ScriptedOpRouter):
        async def execute(self, **kwargs: object) -> RouterExecutionResult[object]:
            if kwargs["operation_name"] == OP_REPORTS:
                await asyncio.sleep(0.01)
            return await super().execute(**kwargs)

    router = _OutOfOrderReportsRouter(by_op)
    service = ResearchReportSearchService(
        router=router,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        secret_redactor=DefaultSecretRedactor(),
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    result = await service.search(
        text="白酒", instrument=_equity(), include_consensus=True, as_of=AS_OF
    )
    assert result.ok is True and result.data is not None
    assert tuple(item.component.value for item in result.provenance) == (
        "reports",
        "consensus",
    )
    assert tuple(item.meta for item in result.provenance) == (
        by_op[OP_REPORTS].meta,
        by_op[OP_CONSENSUS].meta,
    )
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_report_all_optional_failures_have_legal_empty_provenance() -> None:
    by_op: dict[str, RouterExecutionResult[object]] = {
        OP_REPORTS: _fail_result(category=DataCategory.RESEARCH_REPORTS),
        OP_CONSENSUS: _fail_result(category=DataCategory.RESEARCH_REPORTS),
    }
    router = _ScriptedOpRouter(by_op)
    service = ResearchReportSearchService(
        router=router,  # type: ignore[arg-type]
        clock=FixedClock(AS_OF),
        secret_redactor=DefaultSecretRedactor(),
        reports_codec=reports_codec(),
        consensus_codec=consensus_codec(),
    )
    result = await service.search(
        text="白酒", instrument=_equity(), include_consensus=True, as_of=AS_OF
    )
    assert result.ok is True and result.data is not None
    assert result.data.reports == () and result.data.consensus == ()
    assert result.provenance == ()
    assert result.data.provenance == provenance_dtos(result.provenance)


@pytest.mark.asyncio
async def test_corrupted_cache_wrong_category_falls_through_then_live_validates() -> None:
    """Corrupted cache is rejected; live path still enforces service validators."""
    from application.dto.provider_state import CacheEntry

    class _HitCache:
        def __init__(self, entry: CacheEntry) -> None:
            self._entry = entry
            self.gets = 0

        def get(self, key: str) -> CacheEntry:
            self.gets += 1
            return self._entry

        def set(self, key: str, entry: object) -> None:
            return None

        def delete(self, key: str) -> None:
            return None

    class _LiveBadQuote(_BaseMulti):
        calls = 0

        def supports(self, market: Market, category: DataCategory) -> bool:
            return category is DataCategory.MARKET_QUOTE

        async def get_quote(self, instrument: Instrument, as_of: datetime):
            type(self).calls += 1
            # Live also drifts: future quote_at
            q = _quote(instrument)
            bad = AShareQuote(
                instrument_id=q.instrument_id,
                quote_at=as_of + timedelta(days=1),
                session=q.session,
                last=q.last,
                open=None,
                high=None,
                low=None,
                previous_close=None,
                change=None,
                change_percent=None,
                volume_shares=None,
                turnover_amount_cny=None,
                turnover_rate=None,
                pe_ttm=None,
                pb=None,
                total_market_cap_cny=None,
                float_market_cap_cny=None,
                limit_up_price=None,
                limit_down_price=None,
            )
            return ProviderSuccess(value=bad, meta=_meta(DataCategory.MARKET_QUOTE, as_of=as_of))

    inst = _equity()
    good = ProviderSuccess(value=_quote(inst), meta=_meta(DataCategory.MARKET_QUOTE))

    # Codec decode returns wrong category (corrupted payload interpretation).
    class _CorruptQuoteCodec:
        codec_id = "corrupt-quote"

        def encode(self, success: ProviderSuccess[AShareQuote]) -> str:
            return quote_codec().encode(success)

        def decode(self, entry: object) -> ProviderSuccess[AShareQuote]:
            return ProviderSuccess(
                value=_quote(inst),
                meta=_meta(DataCategory.NEWS),
            )

    entry = CacheEntry(
        key="k",
        category=DataCategory.MARKET_QUOTE,
        market=Market.A_SHARE,
        instrument_id=inst.instrument_id,
        vendor=VendorId.EASTMONEY,
        payload_json=quote_codec().encode(good),
        as_of=AS_OF,
        fetched_at=AS_OF,
        expires_at=AS_OF + timedelta(hours=1),
        freshness=Freshness.UNKNOWN,
    )
    cache = _HitCache(entry)
    settings = AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="e3-cache",
        app_env=AppEnvironment.TEST,
        log_level=LogLevel.INFO,
        database_url="sqlite:///:memory:",
        mcp_server_name="e3-cache",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
        provider_retry_max_attempts=1,
        enable_provider_cache=True,
        enable_circuit_breaker=False,
    )
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, _LiveBadQuote())  # type: ignore[arg-type]
    engine = ProviderRouterEngine(
        registry=registry,
        cache_store=cache,  # type: ignore[arg-type]
        health_store=_MemHealth(),  # type: ignore[arg-type]
        rate_limiter=_AllowRateLimiter(),  # type: ignore[arg-type]
        circuit_breaker=CircuitBreaker(FixedClock(AS_OF)),
        clock=FixedClock(AS_OF),
        settings=settings,
    )
    router = ProviderRouter(
        engine=engine,
        chain_config=_StaticChain(
            {(Market.A_SHARE, DataCategory.MARKET_QUOTE): (VendorId.EASTMONEY,)}
        ),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )
    service = AShareSnapshotService(
        router=router,
        clock=FixedClock(AS_OF),
        quote_codec=_CorruptQuoteCodec(),  # type: ignore[arg-type]
        fundamentals_codec=fundamentals_codec(),
        f10_codec=f10_codec(),
        statements_codec=statements_codec(),
        announcements_codec=announcements_codec(),
        news_codec=news_codec(),
        corporate_actions_codec=corporate_actions_codec(),
    )
    res = await service._fetch_quote(inst, AS_OF_QUOTE, now=AS_OF)  # noqa: SLF001
    assert cache.gets >= 1
    assert _LiveBadQuote.calls >= 1
    assert res.ok is False
    assert any(a.outcome is ProviderAttemptOutcome.CONTRACT_ERROR for a in res.attempts)
    assert any(w.code == "CACHE_ENTRY_REJECTED" for w in res.warnings)


# --- Atomic clock / stepping boundary ----------------------------------------


@pytest.mark.asyncio
async def test_atomic_clock_same_current_vs_historical_decision() -> None:
    """Single sampled now: all components share the same current-window decision."""
    window = 3
    base = AS_OF

    class _CurrentUnknownPub(_BaseMulti):
        async def get_quote(self, instrument: Instrument, as_of: datetime):
            return ProviderSuccess(
                value=_quote(instrument),
                meta=_meta(DataCategory.MARKET_QUOTE, as_of=as_of),
            )

        async def get_fundamentals(self, instrument: Instrument, as_of: datetime):
            return ProviderSuccess(
                value=(
                    FundamentalMetric(
                        name="eps",
                        value=Decimal("1"),
                        unit="CNY",
                        period_end=None,
                        published_at=None,
                    ),
                ),
                meta=_meta(DataCategory.FUNDAMENTALS, as_of=as_of),
            )

        async def get_financial_statements(
            self,
            instrument: Instrument,
            *,
            statement_types: tuple[FinancialStatementType, ...],
            periods: int,
            as_of: datetime,
        ):
            return ProviderSuccess(
                value=(
                    FinancialStatementLine(
                        statement_type=FinancialStatementType.INCOME_STATEMENT,
                        period_end=datetime(2023, 12, 31, tzinfo=UTC).date(),
                        published_at=None,
                        item_code="NETPROFIT",
                        item_name="净利润",
                        value=Decimal("1"),
                        unit="CNY",
                    ),
                ),
                meta=_meta(DataCategory.FINANCIAL_STATEMENTS, as_of=as_of),
            )

        async def get_f10_sections(
            self, instrument: Instrument, *, sections: tuple[str, ...], as_of: datetime
        ):
            return ProviderSuccess(value=(), meta=_meta(DataCategory.FUNDAMENTALS, as_of=as_of))

        async def get_announcements(self, instrument: Instrument, *, limit: int, as_of: datetime):
            return ProviderSuccess(value=(), meta=_meta(DataCategory.ANNOUNCEMENTS, as_of=as_of))

        async def get_news(
            self,
            instrument: Instrument | None,
            *,
            start: datetime,
            end: datetime,
            limit: int,
            as_of: datetime,
        ):
            return ProviderSuccess(value=(), meta=_meta(DataCategory.NEWS, as_of=as_of))

        async def get_corporate_actions(
            self,
            instrument: Instrument,
            *,
            start: object,
            end: object,
            as_of: datetime,
        ):
            return ProviderSuccess(
                value=(
                    UnlockRecord(
                        unlock_date=datetime(2024, 1, 10, tzinfo=UTC).date(),
                        published_at=None,
                        unlock_type="r",
                        unlock_shares=1,
                        tradable_shares=1,
                        market_value_cny=None,
                        source_vendor=VendorId.EASTMONEY,
                        reliability=ReliabilityLevel.MEDIUM,
                        is_authoritative=False,
                    ),
                ),
                meta=_meta(DataCategory.CORPORATE_ACTIONS, as_of=as_of),
            )

    clock = FixedClock(base, step_seconds=1)
    adapter = _CurrentUnknownPub()
    registry = VendorRegistry()
    registry.register(VendorId.EASTMONEY, adapter)  # type: ignore[arg-type]
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
        chain_config=_StaticChain(
            {
                (Market.A_SHARE, DataCategory.MARKET_QUOTE): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.FUNDAMENTALS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.FINANCIAL_STATEMENTS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.ANNOUNCEMENTS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.NEWS): (VendorId.EASTMONEY,),
                (Market.A_SHARE, DataCategory.CORPORATE_ACTIONS): (VendorId.EASTMONEY,),
            }
        ),
        clock=clock,
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        criticality_policy=CriticalityPolicy(),
    )
    service = AShareSnapshotService(
        router=router,
        clock=clock,
        quote_codec=quote_codec(),
        fundamentals_codec=fundamentals_codec(),
        f10_codec=f10_codec(),
        statements_codec=statements_codec(),
        announcements_codec=announcements_codec(),
        news_codec=news_codec(),
        corporate_actions_codec=corporate_actions_codec(),
        current_window_seconds=window,
    )
    result = await service.get_snapshot(_equity(), base, AShareSnapshotDetail.FULL)
    assert result.ok is True
    assert result.data is not None
    assert result.data.fundamentals
    assert result.data.statements
    assert result.data.unlocks


# --- Eastmoney report page budget --------------------------------------------


@pytest.mark.asyncio
async def test_eastmoney_report_huge_offset_zero_network() -> None:
    transport = ScriptedHttpTransport(responses=[])
    em = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF))
    with pytest.raises(DataContractError) as ei:
        await em.search_reports(
            text=None,
            instrument=_equity(),
            industry_code=None,
            published_from=None,
            published_to=None,
            limit=50,
            offset=400,
            as_of=AS_OF,
        )
    assert ei.value.details.get("rule") == "page_budget_exceeded"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_eastmoney_report_page_budget_exhausted_on_cutoff_full_pages() -> None:
    future = "2024-06-01 09:00:00"
    page_size = 5
    full_page = [{**_report_row(f"F{i}", 1), "publishDate": future} for i in range(page_size)]
    pages = {i: full_page for i in range(1, 10)}
    transport = _PagedReportTransport(pages)
    em = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF))
    with pytest.raises(PartialDataError) as ei:
        await em.search_reports(
            text=None,
            instrument=_equity(),
            industry_code=None,
            published_from=None,
            published_to=None,
            limit=page_size,
            offset=0,
            as_of=AS_OF,
        )
    assert ei.value.details.get("rule") == "page_budget_exhausted"
    assert 1 <= len(transport.requests) <= 8


@pytest.mark.asyncio
async def test_eastmoney_report_short_final_page_ok() -> None:
    page1 = [_report_row(f"R{i:02d}", i + 1) for i in range(3)]
    transport = _PagedReportTransport({1: page1})
    em = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF))
    res = await em.search_reports(
        text=None,
        instrument=_equity(),
        industry_code=None,
        published_from=None,
        published_to=None,
        limit=10,
        offset=0,
        as_of=AS_OF,
    )
    assert len(res.value) == 3
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_eastmoney_report_cross_page_dedupe_documented() -> None:
    # Full page1 with one cutoff loss → kept < limit → page2 fetched; DUP deduped.
    future = "2024-06-01 09:00:00"
    rows_p1 = [
        _report_row("DUP", 15),
        _report_row("A", 14),
        _report_row("X", 12),
        {**_report_row("FUT", 1), "publishDate": future},
    ]
    rows_p2 = [
        _report_row("DUP", 15),
        _report_row("B", 13),
        _report_row("C", 11),
        _report_row("D", 9),
    ]
    transport = _PagedReportTransport({1: rows_p1, 2: rows_p2})
    em = EastmoneyAShareAdapter(transport, _gate(), calendar=_calendar(), clock=FixedClock(AS_OF))
    res = await em.search_reports(
        text=None,
        instrument=_equity(),
        industry_code=None,
        published_from=None,
        published_to=None,
        limit=4,
        offset=0,
        as_of=AS_OF,
    )
    keys = [r.report_key for r in res.value]
    assert keys.count("DUP") == 1
    assert "FUT" not in keys
    assert "B" in keys
    assert len(transport.requests) >= 2
