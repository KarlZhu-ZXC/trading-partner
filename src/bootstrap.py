"""Composition root — the only module that wires application and infrastructure."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.account_provider import AccountProvider
from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.challenge_review_repository import ChallengeReviewRepository
from application.ports.clock import Clock
from application.ports.http_transport import HttpTransport
from application.ports.id_generator import IdGenerator
from application.ports.industry_metric_repository import IndustryMetricRepository
from application.ports.instrument_unit_of_work import InstrumentUnitOfWork
from application.ports.monitor_repository import MonitorRepository
from application.ports.post_market_sync_run_repository import PostMarketSyncRunRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.risk_policy_repository import RiskPolicyRepository
from application.ports.secret_redactor import SecretRedactor
from application.ports.watchlist_source_provider import WatchlistSourceProvider
from application.ports.workflow_run_repository import WorkflowRunRepository
from application.services.a_share_capital_service import AShareCapitalService
from application.services.a_share_company_operating_metrics_service import (
    AShareCompanyOperatingMetricsService,
)
from application.services.a_share_etf_option_service import AShareEtfOptionService
from application.services.a_share_industry_cycle_service import AShareIndustryCycleService
from application.services.a_share_limit_up_service import AShareLimitUpService
from application.services.a_share_market_structure_service import (
    AShareMarketStructureService,
)
from application.services.a_share_sentiment_service import AShareSentimentService
from application.services.a_share_snapshot_service import AShareSnapshotService
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.account_service import AccountService
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from application.services.challenge_review_service import ChallengeReviewService
from application.services.criticality_policy import CriticalityPolicy
from application.services.decision_record_service import DecisionRecordService
from application.services.evidence_service import EvidenceService
from application.services.health_service import HealthService
from application.services.instrument_master_service import InstrumentMasterService
from application.services.instrument_resolve_service import InstrumentResolveService
from application.services.investment_case_service import InvestmentCaseService
from application.services.journal_service import JournalService
from application.services.mock_instrument_resolver import MockInstrumentResolver
from application.services.mock_market_snapshot_coordinator import (
    MockMarketSnapshotCoordinator,
)
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_service import MonitorService
from application.services.monitor_tool_coordinator import MonitorToolCoordinator
from application.services.open_question_service import OpenQuestionService
from application.services.portfolio_enrichment_calculator import PortfolioEnrichmentCalculator
from application.services.portfolio_review_fact_service import PortfolioReviewFactService
from application.services.portfolio_risk_calculator import PortfolioRiskCalculator
from application.services.portfolio_service import PortfolioService
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from application.services.post_market_sync_service import PostMarketSyncService
from application.services.provider_router import ProviderRouter
from application.services.research_archive_service import ResearchArchiveService
from application.services.research_context_builder import ResearchContextBuilder
from application.services.research_report_search_service import (
    ResearchReportSearchService,
)
from application.services.research_search_service import ResearchSearchService
from application.services.research_state_query_service import ResearchStateQueryService
from application.services.research_timeline_service import ResearchTimelineService
from application.services.research_workflow_orchestrator import ResearchWorkflowOrchestrator
from application.services.risk_engine_service import RiskEngineService
from application.services.risk_policy_service import RiskPolicyService
from application.services.risk_tool_coordinator import RiskToolCoordinator
from application.services.routed_market_snapshot_service import (
    RoutedMarketSnapshotService,
)
from application.services.technical_tool_coordinator import TechnicalToolCoordinator
from application.services.thesis_revision_service import ThesisRevisionService
from application.services.us_community_heat_service import USCommunityHeatService
from application.services.us_company_update_service import USCompanyUpdateService
from application.services.us_context_services import (
    USMacroService,
    USNewsService,
    USPredictionMarketService,
    USSentimentService,
)
from application.services.us_context_tool_coordinator import USContextToolCoordinator
from application.services.us_filing_service import USFilingService
from application.services.us_fundamental_service import USFundamentalService
from application.services.us_market_breadth_service import USMarketBreadthService
from application.services.us_market_context_service import USMarketContextService
from application.services.us_market_data_service import USMarketDataService
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from application.services.us_technical_service import USTechnicalService
from application.services.us_tool_coordinator import USToolCoordinator
from application.services.watchlist_hub_service import WatchlistHubService
from application.services.watchlist_service import WatchlistService
from domain.common.enums import DataCategory, Market, VendorId
from domain.watchlist.enums import WatchlistSource
from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar
from infrastructure.config.settings import AppSettings
from infrastructure.config.vendor_chain import YamlVendorChainConfig
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.challenge_review_repository import (
    SqlAlchemyChallengeReviewRepository,
)
from infrastructure.persistence.database import (
    SqlAlchemyDatabase,
    create_engine_from_url,
)
from infrastructure.persistence.industry_metric_repository import (
    SqlAlchemyIndustryMetricRepository,
)
from infrastructure.persistence.instrument_unit_of_work import (
    SqlAlchemyInstrumentUnitOfWork,
)
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from infrastructure.persistence.post_market_sync_run_repository import (
    SqlAlchemyPostMarketSyncRunRepository,
)
from infrastructure.persistence.provider_state_backend import (
    build_provider_state_backend,
)
from infrastructure.persistence.reddit_state_store import build_reddit_state_store
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.persistence.risk_policy_repository import (
    SqlAlchemyRiskPolicyRepository,
)
from infrastructure.persistence.watchlist_hub_unit_of_work import (
    SqlAlchemyWatchlistHubUnitOfWork,
)
from infrastructure.persistence.workflow_run_repository import (
    SqlAlchemyWorkflowRunRepository,
)
from infrastructure.providers.a_share.cls import CLSAShareAdapter
from infrastructure.providers.a_share.cninfo import CninfoAShareAdapter
from infrastructure.providers.a_share.codecs import (
    announcements_codec,
    bars_codec,
    block_trades_codec,
    chip_distribution_codec,
    consensus_codec,
    corporate_actions_codec,
    daily_flow_codec,
    dragon_tiger_codec,
    f10_codec,
    fundamentals_codec,
    industry_performance_codec,
    interactive_qa_codec,
    intraday_flow_codec,
    limit_context_codec,
    margin_codec,
    market_board_codec,
    news_codec,
    northbound_codec,
    option_snapshot_codec,
    order_book_codec,
    quote_codec,
    reports_codec,
    sentiment_codec,
    shareholder_counts_codec,
    statements_codec,
    ticks_codec,
)
from infrastructure.providers.a_share.eastmoney import EastmoneyAShareAdapter
from infrastructure.providers.a_share.eastmoney_gate import (
    EastmoneyRequestGate,
    get_production_eastmoney_request_gate,
)
from infrastructure.providers.a_share.exchanges import (
    SseAShareDisclosureAdapter,
    SzseAShareDisclosureAdapter,
)
from infrastructure.providers.a_share.hkex import HkexNorthboundAdapter
from infrastructure.providers.a_share.iwencai import IwencaiAShareAdapter
from infrastructure.providers.a_share.mock_market import (
    MockAShareMarketSnapshotProvider,
)
from infrastructure.providers.a_share.nahs import NahsHogCycleAdapter
from infrastructure.providers.a_share.sina import SinaAShareAdapter
from infrastructure.providers.a_share.tencent import TencentAShareAdapter
from infrastructure.providers.a_share.ths import ThsAShareAdapter
from infrastructure.providers.a_share.trading_calendar import (
    load_default_a_share_trading_calendar,
)
from infrastructure.providers.account.manual_csv import ManualCsvAccountAdapter
from infrastructure.providers.account.moomoo import MoomooAccountAdapter
from infrastructure.providers.account.schwab import SchwabAccountAdapter
from infrastructure.providers.common.circuit_breaker import CircuitBreaker
from infrastructure.providers.common.httpx_transport import HttpxTransport
from infrastructure.providers.common.market_snapshot_category_adapter import (
    MarketSnapshotCategoryAdapter,
)
from infrastructure.providers.common.null_category_provider import NullCategoryProvider
from infrastructure.providers.common.rate_limiter import ProviderRateLimiter
from infrastructure.providers.common.verified_snapshot_cache_codec import (
    VerifiedMarketSnapshotCacheCodec,
)
from infrastructure.providers.instrument_directory import (
    AlphaVantageInstrumentDirectoryAdapter,
    TencentInstrumentDirectoryAdapter,
    YahooInstrumentDirectoryAdapter,
)
from infrastructure.providers.moomoo_rate_limiter import MoomooOpenDRateLimiter
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.router_engine import ProviderRouterEngine
from infrastructure.providers.us.alpha_vantage_research import AlphaVantageResearchAdapter
from infrastructure.providers.us.codecs import us_bars_codec, us_quote_codec
from infrastructure.providers.us.context_codecs import (
    us_community_heat_codec,
    us_macro_context_codec,
    us_market_breadth_codec,
    us_news_feed_codec,
    us_prediction_market_context_codec,
    us_sentiment_samples_codec,
)
from infrastructure.providers.us.eastmoney_futures import EastmoneyMetalFuturesAdapter
from infrastructure.providers.us.fred import FredMacroAdapter
from infrastructure.providers.us.mock_market import MockUSMarketSnapshotProvider
from infrastructure.providers.us.moomoo_community import MoomooCommunityHeatAdapter
from infrastructure.providers.us.moomoo_sentiment import MoomooSentimentAdapter
from infrastructure.providers.us.polymarket import PolymarketPredictionAdapter
from infrastructure.providers.us.reddit import RedditSentimentAdapter
from infrastructure.providers.us.research_codecs import (
    us_corporate_actions_codec,
    us_filings_codec,
    us_financial_statements_codec,
    us_fundamental_snapshot_codec,
    us_insider_activity_codec,
)
from infrastructure.providers.us.sec_research import SECResearchAdapter
from infrastructure.providers.us.sina_futures import SinaMetalFuturesAdapter
from infrastructure.providers.us.stocktwits import StockTwitsSentimentAdapter
from infrastructure.providers.us.yahoo_finance_research import YahooFinanceResearchAdapter
from infrastructure.providers.watchlist.manual_csv import ManualCsvWatchlistAdapter
from infrastructure.providers.watchlist.moomoo import MoomooWatchlistAdapter
from infrastructure.providers.watchlist.moomoo_security_corrections import (
    MoomooSecurityCorrections,
)
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator
from infrastructure.system.process_file_lock import ProcessFileLock
from infrastructure.system.redactor import DefaultSecretRedactor
from infrastructure.technical import MatplotlibChartRenderer, TALibIndicatorEngine

UowFactory = Callable[[], ResearchUnitOfWork]
InstrumentUowFactory = Callable[[], InstrumentUnitOfWork]


@dataclass(frozen=True, slots=True)
class BootstrapOverrides:
    """Deterministic composition-only overrides; never a production mode switch."""

    clock: Clock | None = None
    a_share_transport: HttpTransport | None = None
    eastmoney_gate: EastmoneyRequestGate | None = None
    a_share_calendar: AShareTradingCalendar | None = None
    watchlist_provider: WatchlistSourceProvider | None = None


@dataclass
class ApplicationContainer:
    """Internal composition-root structure (not part of MCP/domain public contracts)."""

    settings: AppSettings
    clock: Clock
    database: SqlAlchemyDatabase
    id_generator: IdGenerator
    secret_redactor: SecretRedactor
    health_service: HealthService
    mock_market_snapshot_coordinator: MockMarketSnapshotCoordinator
    # Phase 1D D8b router surface
    provider_router: ProviderRouter
    vendor_registry: VendorRegistry
    routed_market_snapshot_service: RoutedMarketSnapshotService
    # Phase 1B research
    investment_case_service: InvestmentCaseService
    thesis_revision_service: ThesisRevisionService
    watchlist_service: WatchlistService
    watchlist_hub_service: WatchlistHubService
    research_state_query_service: ResearchStateQueryService
    open_question_service: OpenQuestionService
    research_unit_of_work_factory: UowFactory
    # Phase 1D instrument master (D3b) — no seed/migrations here
    instrument_master_service: InstrumentMasterService
    instrument_resolve_service: InstrumentResolveService
    instrument_unit_of_work_factory: InstrumentUowFactory
    # Phase 1C research memory (C5 composition)
    evidence_service: EvidenceService
    research_archive_service: ResearchArchiveService
    research_search_service: ResearchSearchService
    research_timeline_service: ResearchTimelineService
    journal_service: JournalService
    decision_record_service: DecisionRecordService
    # Phase 1E E5b/E5c A-share product services + tool coordinator (MCP still deferred)
    a_share_trading_calendar: AShareTradingCalendar
    a_share_snapshot_service: AShareSnapshotService
    a_share_market_structure_service: AShareMarketStructureService
    a_share_capital_service: AShareCapitalService
    a_share_limit_up_service: AShareLimitUpService
    a_share_sentiment_service: AShareSentimentService
    a_share_etf_option_service: AShareEtfOptionService
    industry_metric_repository: IndustryMetricRepository
    research_report_search_service: ResearchReportSearchService
    a_share_tool_coordinator: AShareToolCoordinator
    # Phase 1F F3c US market product services + tool coordinator
    us_market_data_service: USMarketDataService
    us_market_breadth_service: USMarketBreadthService
    us_market_context_service: USMarketContextService
    us_technical_service: USTechnicalService
    us_tool_coordinator: USToolCoordinator
    technical_tool_coordinator: TechnicalToolCoordinator
    # Phase 1G US research services + tool coordinator
    us_fundamental_service: USFundamentalService
    us_filing_service: USFilingService
    us_company_update_service: USCompanyUpdateService
    us_research_tool_coordinator: USResearchToolCoordinator
    # Phase 1H US news, macro, sentiment, and prediction context.
    us_news_service: USNewsService
    us_macro_service: USMacroService
    us_sentiment_service: USSentimentService
    us_prediction_market_service: USPredictionMarketService
    us_context_tool_coordinator: USContextToolCoordinator
    # Phase 1I read-only account and deterministic portfolio services.
    account_snapshot_repository: AccountSnapshotRepository
    account_service: AccountService
    portfolio_service: PortfolioService
    portfolio_tool_coordinator: PortfolioToolCoordinator
    risk_policy_repository: RiskPolicyRepository
    risk_policy_service: RiskPolicyService
    risk_engine_service: RiskEngineService
    risk_tool_coordinator: RiskToolCoordinator
    monitor_repository: MonitorRepository
    monitor_service: MonitorService
    monitor_evaluation_service: MonitorEvaluationService
    monitor_tool_coordinator: MonitorToolCoordinator
    monitor_run_lock: ProcessFileLock
    post_market_sync_service: PostMarketSyncService
    post_market_sync_lock: ProcessFileLock
    research_context_builder: ResearchContextBuilder
    challenge_review_repository: ChallengeReviewRepository
    challenge_review_service: ChallengeReviewService
    account_transaction_repository: AccountTransactionRepository
    account_transaction_coordinator: AccountTransactionCoordinator
    workflow_run_repository: WorkflowRunRepository
    portfolio_review_fact_service: PortfolioReviewFactService
    research_workflow_orchestrator: ResearchWorkflowOrchestrator
    _owned_a_share_transport: HttpTransport | None = field(default=None, repr=False)
    _owned_polymarket_transport: HttpTransport | None = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError("running event loop: await container.aclose()")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._owned_a_share_transport is not None:
                aclose = getattr(self._owned_a_share_transport, "aclose", None)
                if callable(aclose):
                    await aclose()
            if self._owned_polymarket_transport is not None:
                aclose = getattr(self._owned_polymarket_transport, "aclose", None)
                if callable(aclose):
                    await aclose()
        finally:
            self.database.close()


def build_application(
    settings: AppSettings, *, overrides: BootstrapOverrides | None = None
) -> ApplicationContainer:
    """Explicit factory: construct services from settings. No I/O at import time.

    Does **not** run migrations or seed the instrument master (production seed
    is migration 0003; tests seed via Alembic or InstrumentSeedLoader).
    """
    overrides = overrides or BootstrapOverrides()
    clock: Clock = overrides.clock or SystemClock()
    id_generator: IdGenerator = Uuid7IdGenerator()
    secret_redactor: SecretRedactor = DefaultSecretRedactor()
    owned_a_share_transport: HttpTransport | None = None
    owned_polymarket_transport: HttpTransport | None = None
    if overrides.a_share_transport is None:
        owned_a_share_transport = HttpxTransport(
            max_response_bytes=settings.http_max_response_bytes,
            timeout_seconds=settings.provider_timeout_market_seconds,
        )

    engine = create_engine_from_url(settings.database_url)
    database = SqlAlchemyDatabase(engine)
    account_snapshot_repository: AccountSnapshotRepository = SqlAlchemyAccountSnapshotRepository(
        engine
    )
    account_transaction_repository: AccountTransactionRepository = (
        SqlAlchemyAccountTransactionRepository(engine)
    )
    workflow_run_repository: WorkflowRunRepository = SqlAlchemyWorkflowRunRepository(engine)
    industry_metric_repository: IndustryMetricRepository = (
        SqlAlchemyIndustryMetricRepository(engine)
    )
    post_market_sync_run_repository: PostMarketSyncRunRepository = (
        SqlAlchemyPostMarketSyncRunRepository(engine)
    )

    def research_unit_of_work_factory() -> ResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(
            engine,
            clock,
            id_generator,
            secret_redactor,
        )

    def watchlist_hub_unit_of_work_factory() -> SqlAlchemyWatchlistHubUnitOfWork:
        return SqlAlchemyWatchlistHubUnitOfWork(
            engine,
            clock,
            id_generator,
            secret_redactor,
        )

    def search_backend_probe() -> bool:
        # Isolated UoW session; must not leak SQL/path/query into health warnings.
        with research_unit_of_work_factory() as uow:
            return uow.search_index.probe()

    health_service = HealthService(
        database=database,
        settings=settings,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        search_backend_probe=search_backend_probe,
    )

    # --- Phase 1D D8b: Vendor Chain + Registry + Router + Routed Snapshot ---
    chain_config = YamlVendorChainConfig.load(settings.vendor_chain_path)

    a_share_transport = overrides.a_share_transport or owned_a_share_transport
    assert a_share_transport is not None
    polymarket_transport = a_share_transport
    if settings.polymarket_proxy_url is not None and overrides.a_share_transport is None:
        owned_polymarket_transport = HttpxTransport(
            max_response_bytes=settings.http_max_response_bytes,
            timeout_seconds=settings.provider_timeout_default_seconds,
            proxy_url=settings.polymarket_proxy_url,
        )
        polymarket_transport = owned_polymarket_transport
    a_share_calendar: AShareTradingCalendar = (
        overrides.a_share_calendar or load_default_a_share_trading_calendar()
    )
    eastmoney_gate: EastmoneyRequestGate = (
        overrides.eastmoney_gate
        or get_production_eastmoney_request_gate(
            min_interval_seconds=settings.eastmoney_min_interval_seconds,
            jitter_seconds=settings.eastmoney_jitter_seconds,
        )
    )
    a_share_provider = MockAShareMarketSnapshotProvider()
    us_provider = MockUSMarketSnapshotProvider()

    vendor_registry = VendorRegistry()
    vendor_registry.register(
        VendorId.MOCK_A_SHARE,
        MarketSnapshotCategoryAdapter(
            vendor_id=VendorId.MOCK_A_SHARE,
            provider=a_share_provider,
        ),
    )
    vendor_registry.register(
        VendorId.MOCK_US,
        MarketSnapshotCategoryAdapter(
            vendor_id=VendorId.MOCK_US,
            provider=us_provider,
        ),
    )
    # Explicit null placeholder so YAML ``null`` chains are not accidental misses.
    vendor_registry.register(VendorId.NULL, NullCategoryProvider())
    # Real adapters remain registered even when disabled; Router reports an
    # explicit configured-skip rather than silently changing configured chains.
    market_timeout = settings.provider_timeout_market_seconds
    vendor_registry.register(
        VendorId.TENCENT,
        TencentAShareAdapter(
            a_share_transport,
            calendar=a_share_calendar,
            clock=clock,
            enabled=settings.tencent_enabled,
            timeout_seconds=market_timeout,
            max_fresh_seconds=settings.a_share_max_fresh_seconds,
            max_delayed_seconds=settings.a_share_max_delayed_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.EASTMONEY,
        EastmoneyAShareAdapter(
            a_share_transport,
            eastmoney_gate,
            calendar=a_share_calendar,
            clock=clock,
            enabled=settings.eastmoney_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
            max_fresh_seconds=settings.a_share_max_fresh_seconds,
            max_delayed_seconds=settings.a_share_max_delayed_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.SINA,
        SinaAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.sina_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
            max_fresh_seconds=settings.a_share_max_fresh_seconds,
            max_delayed_seconds=settings.a_share_max_delayed_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.CNINFO,
        CninfoAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.cninfo_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    nahs_hog_cycle_provider = NahsHogCycleAdapter(
        a_share_transport,
        clock=clock,
        enabled=settings.nahs_enabled,
        timeout_seconds=settings.provider_timeout_default_seconds,
    )
    vendor_registry.register(VendorId.NAHS, nahs_hog_cycle_provider)
    vendor_registry.register(
        VendorId.THS,
        ThsAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.ths_enabled,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.CLS,
        CLSAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.cls_enabled,
            timeout_seconds=market_timeout,
        ),
    )
    vendor_registry.register(
        VendorId.SSE,
        SseAShareDisclosureAdapter(
            a_share_transport,
            clock=clock,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.SZSE,
        SzseAShareDisclosureAdapter(
            a_share_transport,
            clock=clock,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.HKEX,
        HkexNorthboundAdapter(
            a_share_transport,
            clock=clock,
            timeout_seconds=market_timeout,
        ),
    )
    vendor_registry.register(
        VendorId.IWENCAI,
        IwencaiAShareAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.iwencai_enabled,
            api_key=settings.iwencai_api_key,
            base_url=settings.iwencai_base_url,
            timeout_seconds=market_timeout,
            current_window_seconds=settings.a_share_current_window_seconds,
        ),
    )
    # Phase 1F: Yahoo + Alpha Vantage share the single owned/injected transport.
    vendor_registry.register(
        VendorId.YFINANCE,
        YahooFinanceResearchAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.yfinance_enabled,
            timeout_seconds=market_timeout,
            breadth_timeout_seconds=settings.provider_timeout_us_breadth_seconds,
            max_fresh_seconds=settings.us_max_fresh_seconds,
            max_delayed_seconds=settings.us_max_delayed_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.SINA_FUTURES,
        SinaMetalFuturesAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.sina_enabled,
            timeout_seconds=market_timeout,
        ),
    )
    vendor_registry.register(
        VendorId.EASTMONEY_FUTURES,
        EastmoneyMetalFuturesAdapter(
            a_share_transport,
            eastmoney_gate,
            clock=clock,
            enabled=settings.eastmoney_enabled,
            timeout_seconds=market_timeout,
        ),
    )
    vendor_registry.register(
        VendorId.ALPHA_VANTAGE,
        AlphaVantageResearchAdapter(
            a_share_transport,
            api_keys=settings.alpha_vantage_api_keys,
            clock=clock,
            enabled=settings.alpha_vantage_enabled,
            timeout_seconds=market_timeout,
            max_fresh_seconds=settings.us_max_fresh_seconds,
            max_delayed_seconds=settings.us_max_delayed_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.SEC_EDGAR,
        SECResearchAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.sec_edgar_enabled,
            sec_user_agent=settings.sec_user_agent,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.FRED,
        FredMacroAdapter(
            a_share_transport,
            api_key=settings.fred_api_key,
            clock=clock,
            enabled=settings.fred_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.STOCKTWITS,
        StockTwitsSentimentAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.stocktwits_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.MOOMOO_FEED,
        MoomooSentimentAdapter(
            a_share_transport,
            clock=clock,
            enabled=settings.moomoo_sentiment_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    vendor_registry.register(
        VendorId.REDDIT,
        RedditSentimentAdapter(
            a_share_transport,
            user_agent=settings.reddit_user_agent,
            subreddits=tuple(settings.reddit_subreddits.split(",")),
            clock=clock,
            enabled=settings.reddit_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
            min_interval_seconds=settings.reddit_min_interval_seconds,
            cache_ttl_seconds=settings.reddit_cache_ttl_seconds,
            cooldown_default_seconds=settings.reddit_cooldown_default_seconds,
            cooldown_max_seconds=settings.reddit_cooldown_max_seconds,
            apify_enabled=settings.reddit_apify_enabled,
            apify_api_token=settings.apify_api_token,
            apify_actor_id=settings.reddit_apify_actor_id,
            apify_subreddits=tuple(settings.reddit_apify_subreddits.split(",")),
            apify_lookback_days=settings.reddit_apify_lookback_map,
            apify_max_charge_usd=settings.reddit_apify_max_charge_usd,
            state_store=build_reddit_state_store(engine, clock, secret_redactor),
        ),
    )
    vendor_registry.register(
        VendorId.POLYMARKET,
        PolymarketPredictionAdapter(
            polymarket_transport,
            clock=clock,
            enabled=settings.polymarket_enabled,
            timeout_seconds=settings.provider_timeout_default_seconds,
        ),
    )
    moomoo_opend_rate_limiter = MoomooOpenDRateLimiter(
        settings.post_market_sync_lock_path.parent / "moomoo_opend_rate_limit.log"
    )
    moomoo_community_heat_provider = MoomooCommunityHeatAdapter(
        enabled=settings.moomoo_community_heat_enabled,
        host=settings.moomoo_host,
        port=settings.moomoo_port,
        clock=clock,
        opend_rate_limiter=moomoo_opend_rate_limiter,
    )
    vendor_registry.register(VendorId.MOOMOO, moomoo_community_heat_provider)
    moomoo_account_provider = MoomooAccountAdapter(
        id_generator,
        enabled="MOOMOO" in settings.holdings_sources,
        host=settings.moomoo_host,
        port=settings.moomoo_port,
        account_ids=tuple(
            item.strip() for item in settings.moomoo_account_ids.split(",") if item.strip()
        ),
        clock=clock,
        opend_rate_limiter=moomoo_opend_rate_limiter,
    )
    manual_account_provider = ManualCsvAccountAdapter(
        (settings.manual_holdings_csv_path if "MANUAL_CSV" in settings.holdings_sources else None),
        id_generator,
        clock=clock,
    )
    schwab_account_provider = SchwabAccountAdapter(
        id_generator,
        enabled="SCHWAB" in settings.holdings_sources,
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        redirect_uri=settings.schwab_redirect_uri,
        token_path=settings.schwab_token_path,
        account_hashes=tuple(
            item.strip() for item in settings.schwab_account_hashes.split(",") if item.strip()
        ),
        clock=clock,
    )
    vendor_registry.register(VendorId.SCHWAB, schwab_account_provider)
    vendor_registry.register(VendorId.MANUAL_CSV, manual_account_provider)

    watchlist_source_provider = overrides.watchlist_provider

    state_backend = build_provider_state_backend(engine, clock, secret_redactor)

    rate_limiter = ProviderRateLimiter(state_backend.rate_limit_store, clock)
    if watchlist_source_provider is None:
        if settings.watchlist_source == WatchlistSource.MOOMOO.value:
            watchlist_source_provider = MoomooWatchlistAdapter(
                enabled=True,
                host=settings.moomoo_host,
                port=settings.moomoo_port,
                clock=clock,
                opend_rate_limiter=moomoo_opend_rate_limiter,
                security_corrections=MoomooSecurityCorrections.load_default(),
            )
        else:
            watchlist_source_provider = ManualCsvWatchlistAdapter(
                settings.manual_watchlist_csv_path,
                default_group=settings.watchlist_default_group,
                clock=clock,
            )

    circuit_breaker = CircuitBreaker(
        clock,
        failure_threshold=settings.circuit_failure_threshold,
        recovery_timeout_seconds=settings.circuit_recovery_timeout_seconds,
        half_open_max_calls=settings.circuit_half_open_max_calls,
    )

    router_engine = ProviderRouterEngine(
        registry=vendor_registry,
        cache_store=state_backend.cache_store,
        health_store=state_backend.health_store,
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker,
        clock=clock,
        settings=settings,
    )
    provider_router = ProviderRouter(
        engine=router_engine,
        chain_config=chain_config,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        criticality_policy=CriticalityPolicy(),
    )

    cache_codec = VerifiedMarketSnapshotCacheCodec()
    routed_market_snapshot_service = RoutedMarketSnapshotService(
        router=provider_router,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        cache_codec=cache_codec,
    )

    resolver = MockInstrumentResolver()
    coordinator = MockMarketSnapshotCoordinator(
        resolver=resolver,
        routed_service=routed_market_snapshot_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
    )

    def instrument_unit_of_work_factory() -> InstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    investment_case_service = InvestmentCaseService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    thesis_revision_service = ThesisRevisionService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    watchlist_service = WatchlistService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    watchlist_hub_service = WatchlistHubService(
        provider=watchlist_source_provider,
        uow_factory=watchlist_hub_unit_of_work_factory,
        research_uow_factory=research_unit_of_work_factory,
        default_group=settings.watchlist_default_group,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
    )
    research_state_query_service = ResearchStateQueryService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    open_question_service = OpenQuestionService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )

    instrument_master_service = InstrumentMasterService(
        instrument_unit_of_work_factory,
    )
    instrument_resolve_service = InstrumentResolveService(
        master=instrument_master_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        directories={
            Market.A_SHARE: (
                TencentInstrumentDirectoryAdapter(
                    a_share_transport,
                    clock=clock,
                    enabled=settings.tencent_enabled,
                    timeout_seconds=market_timeout,
                ),
            ),
            Market.US: (
                YahooInstrumentDirectoryAdapter(
                    a_share_transport,
                    clock=clock,
                    enabled=settings.yfinance_enabled,
                    timeout_seconds=market_timeout,
                ),
                AlphaVantageInstrumentDirectoryAdapter(
                    a_share_transport,
                    api_keys=settings.alpha_vantage_api_keys,
                    clock=clock,
                    enabled=settings.alpha_vantage_enabled,
                    timeout_seconds=market_timeout,
                ),
            ),
        },
    )

    # Phase 1C C5: single research UoW factory shared across all six services.
    evidence_service = EvidenceService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    research_archive_service = ResearchArchiveService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    research_search_service = ResearchSearchService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    research_timeline_service = ResearchTimelineService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    journal_service = JournalService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )
    decision_record_service = DecisionRecordService(
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
    )

    # Phase 1E E5b: construct each runtime cache codec exactly once, then inject.
    # chip/sentiment use v2 ids only (no v1 dual injection).
    quote_cache_codec = quote_codec()
    bars_cache_codec = bars_codec()
    order_book_cache_codec = order_book_codec()
    ticks_cache_codec = ticks_codec()
    industry_performance_cache_codec = industry_performance_codec()
    market_board_cache_codec = market_board_codec()
    fundamentals_cache_codec = fundamentals_codec()
    f10_cache_codec = f10_codec()
    statements_cache_codec = statements_codec()
    announcements_cache_codec = announcements_codec()
    news_cache_codec = news_codec()
    corporate_actions_cache_codec = corporate_actions_codec()
    reports_cache_codec = reports_codec()
    consensus_cache_codec = consensus_codec()
    intraday_flow_cache_codec = intraday_flow_codec()
    daily_flow_cache_codec = daily_flow_codec()
    northbound_cache_codec = northbound_codec()
    dragon_tiger_cache_codec = dragon_tiger_codec()
    margin_cache_codec = margin_codec()
    block_trades_cache_codec = block_trades_codec()
    shareholder_counts_cache_codec = shareholder_counts_codec()
    chip_distribution_cache_codec = chip_distribution_codec()
    limit_context_cache_codec = limit_context_codec()
    sentiment_cache_codec = sentiment_codec()
    interactive_qa_cache_codec = interactive_qa_codec()
    option_snapshot_cache_codec = option_snapshot_codec()

    a_share_snapshot_service = AShareSnapshotService(
        router=provider_router,
        clock=clock,
        quote_codec=quote_cache_codec,
        fundamentals_codec=fundamentals_cache_codec,
        f10_codec=f10_cache_codec,
        statements_codec=statements_cache_codec,
        announcements_codec=announcements_cache_codec,
        news_codec=news_cache_codec,
        corporate_actions_codec=corporate_actions_cache_codec,
        current_window_seconds=settings.a_share_current_window_seconds,
    )
    a_share_market_structure_service = AShareMarketStructureService(
        router=provider_router,
        clock=clock,
        calendar=a_share_calendar,
        quote_codec=quote_cache_codec,
        bars_codec=bars_cache_codec,
        order_book_codec=order_book_cache_codec,
        ticks_codec=ticks_cache_codec,
        industry_codec=industry_performance_cache_codec,
        market_board_codec=market_board_cache_codec,
        freshness_window_seconds=settings.a_share_current_window_seconds,
    )
    a_share_capital_service = AShareCapitalService(
        router=provider_router,
        clock=clock,
        calendar=a_share_calendar,
        intraday_flow_codec=intraday_flow_cache_codec,
        daily_flow_codec=daily_flow_cache_codec,
        northbound_codec=northbound_cache_codec,
        dragon_tiger_codec=dragon_tiger_cache_codec,
        margin_codec=margin_cache_codec,
        block_trades_codec=block_trades_cache_codec,
        shareholder_counts_codec=shareholder_counts_cache_codec,
        chip_distribution_codec=chip_distribution_cache_codec,
        corporate_actions_codec=corporate_actions_cache_codec,
        current_window_seconds=settings.a_share_current_window_seconds,
    )
    a_share_limit_up_service = AShareLimitUpService(
        router=provider_router,
        clock=clock,
        calendar=a_share_calendar,
        limit_context_codec=limit_context_cache_codec,
    )
    a_share_sentiment_service = AShareSentimentService(
        router=provider_router,
        clock=clock,
        sentiment_codec=sentiment_cache_codec,
        interactive_qa_codec=interactive_qa_cache_codec,
        news_codec=news_cache_codec,
    )
    a_share_etf_option_service = AShareEtfOptionService(
        router=provider_router,
        clock=clock,
        option_snapshot_codec=option_snapshot_cache_codec,
    )
    a_share_industry_cycle_service = AShareIndustryCycleService(
        provider_router,
        industry_metric_repository,
    )
    a_share_company_operating_metrics_service = AShareCompanyOperatingMetricsService(
        provider_router,
    )
    research_report_search_service = ResearchReportSearchService(
        router=provider_router,
        clock=clock,
        secret_redactor=secret_redactor,
        reports_codec=reports_cache_codec,
        consensus_codec=consensus_cache_codec,
    )
    a_share_tool_coordinator = AShareToolCoordinator(
        instrument_master=instrument_master_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        snapshot_service=a_share_snapshot_service,
        market_structure_service=a_share_market_structure_service,
        capital_service=a_share_capital_service,
        limit_up_service=a_share_limit_up_service,
        sentiment_service=a_share_sentiment_service,
        etf_option_service=a_share_etf_option_service,
        industry_cycle_service=a_share_industry_cycle_service,
        company_operating_metrics_service=a_share_company_operating_metrics_service,
        report_search_service=research_report_search_service,
    )

    # Phase 1F F3c: construct US codecs exactly once, then inject.
    us_quote_cache_codec = us_quote_codec()
    us_bars_cache_codec = us_bars_codec()
    us_market_data_service = USMarketDataService(
        router=provider_router,
        clock=clock,
        quote_codec=us_quote_cache_codec,
        bars_codec=us_bars_cache_codec,
    )
    us_market_breadth_service = USMarketBreadthService(
        router=provider_router,
        clock=clock,
        codec=us_market_breadth_codec(),
    )
    us_community_heat_service = USCommunityHeatService(
        router=provider_router,
        clock=clock,
        codec=us_community_heat_codec(),
    )
    us_market_context_service = USMarketContextService(
        data_service=us_market_data_service,
        instrument_master=instrument_master_service,
        clock=clock,
        breadth_service=us_market_breadth_service,
        community_heat_service=us_community_heat_service,
        community_heat_limit=settings.moomoo_community_heat_limit,
    )
    us_technical_service = USTechnicalService(
        data_service=us_market_data_service,
        clock=clock,
    )
    us_tool_coordinator = USToolCoordinator(
        instrument_master=instrument_master_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        data_service=us_market_data_service,
        context_service=us_market_context_service,
        technical_service=us_technical_service,
    )
    technical_tool_coordinator = TechnicalToolCoordinator(
        instrument_master=instrument_master_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        us_data_service=us_market_data_service,
        a_share_data_service=a_share_market_structure_service,
        indicator_engine=TALibIndicatorEngine(),
        chart_renderer=MatplotlibChartRenderer(),
    )

    # Phase 1G: one codec instance per research category and thin composition.
    us_fundamental_service = USFundamentalService(
        router=provider_router,
        clock=clock,
        fundamental_codec=us_fundamental_snapshot_codec(),
        statements_codec=us_financial_statements_codec(),
        actions_codec=us_corporate_actions_codec(),
    )
    us_filing_service = USFilingService(
        router=provider_router,
        clock=clock,
        filings_codec=us_filings_codec(),
        insider_codec=us_insider_activity_codec(),
    )
    us_news_service = USNewsService(provider_router, clock, us_news_feed_codec())
    us_company_update_service = USCompanyUpdateService(
        us_fundamental_service,
        us_filing_service,
        us_news_service,
    )
    us_research_tool_coordinator = USResearchToolCoordinator(
        instrument_master=instrument_master_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        fundamental_service=us_fundamental_service,
        filing_service=us_filing_service,
        company_update_service=us_company_update_service,
    )

    # Phase 1H: four routed context services and one thin tool coordinator.
    us_macro_service = USMacroService(provider_router, clock, us_macro_context_codec())
    us_sentiment_service = USSentimentService(provider_router, clock, us_sentiment_samples_codec())
    us_prediction_market_service = USPredictionMarketService(
        provider_router, clock, us_prediction_market_context_codec()
    )
    us_context_tool_coordinator = USContextToolCoordinator(
        instrument_master=instrument_master_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        news_service=us_news_service,
        macro_service=us_macro_service,
        sentiment_service=us_sentiment_service,
        prediction_service=us_prediction_market_service,
    )

    # Phase 1I: direct read-only account ports; no order-capable service exists.
    account_providers: dict[VendorId, AccountProvider] = {
        VendorId.SCHWAB: schwab_account_provider,
        VendorId.MOOMOO: moomoo_account_provider,
        VendorId.MANUAL_CSV: manual_account_provider,
    }
    account_service = AccountService(
        account_providers,
        account_snapshot_repository,
        clock,
        default_order=chain_config.chain_for(Market.US, DataCategory.ACCOUNT),
    )
    portfolio_service = PortfolioService(
        account_service,
        account_snapshot_repository,
        clock,
        id_generator,
    )
    portfolio_tool_coordinator = PortfolioToolCoordinator(
        account_service,
        portfolio_service,
        clock,
        id_generator,
        secret_redactor,
    )
    risk_policy_repository: RiskPolicyRepository = SqlAlchemyRiskPolicyRepository(engine)
    risk_policy_service = RiskPolicyService(risk_policy_repository, clock, id_generator)
    risk_engine_service = RiskEngineService(account_service, risk_policy_service)
    risk_tool_coordinator = RiskToolCoordinator(
        risk_engine_service,
        risk_policy_service,
        clock,
        id_generator,
        secret_redactor,
    )
    monitor_repository: MonitorRepository = SqlAlchemyMonitorRepository(engine)
    monitor_service = MonitorService(
        monitor_repository,
        research_unit_of_work_factory,
        clock,
        id_generator,
    )
    monitor_evaluation_service = MonitorEvaluationService(
        monitor_repository,
        a_share_tool_coordinator,
        us_tool_coordinator,
        risk_tool_coordinator,
        clock,
        id_generator,
    )
    monitor_tool_coordinator = MonitorToolCoordinator(
        monitor_service,
        monitor_evaluation_service,
        clock,
        id_generator,
        secret_redactor,
    )
    monitor_run_lock = ProcessFileLock(
        settings.post_market_sync_lock_path.parent / "monitoring.lock"
    )
    post_market_sync_service = PostMarketSyncService(
        calendar=XnysMarketSessionCalendar(),
        repository=post_market_sync_run_repository,
        portfolio=portfolio_tool_coordinator,
        watchlist=watchlist_hub_service,
        clock=clock,
        id_generator=id_generator,
        delay_minutes=settings.post_market_sync_delay_minutes,
    )
    post_market_sync_lock = ProcessFileLock(settings.post_market_sync_lock_path)
    research_context_builder = ResearchContextBuilder(
        research_unit_of_work_factory,
        account_snapshot_repository,
        clock,
        id_generator,
        secret_redactor,
    )
    challenge_review_repository: ChallengeReviewRepository = SqlAlchemyChallengeReviewRepository(
        engine
    )
    challenge_review_service = ChallengeReviewService(
        challenge_review_repository,
        research_context_builder,
        clock,
        id_generator,
        secret_redactor,
    )
    account_transaction_coordinator = AccountTransactionCoordinator(
        {
            VendorId.SCHWAB: schwab_account_provider,
            VendorId.MOOMOO: moomoo_account_provider,
        },
        account_transaction_repository,
        clock,
        id_generator,
        secret_redactor,
    )
    portfolio_review_fact_service = PortfolioReviewFactService(
        account_service,
        instrument_resolve_service,
        research_context_builder,
        a_share_tool_coordinator,
        us_tool_coordinator,
        us_research_tool_coordinator,
        PortfolioRiskCalculator(),
        PortfolioEnrichmentCalculator(),
        clock,
        id_generator,
        secret_redactor,
    )
    research_workflow_orchestrator = ResearchWorkflowOrchestrator(
        workflow_run_repository,
        investment_case_service,
        research_context_builder,
        research_archive_service,
        a_share_tool_coordinator,
        us_tool_coordinator,
        us_research_tool_coordinator,
        us_context_tool_coordinator,
        portfolio_tool_coordinator,
        account_transaction_coordinator,
        portfolio_review_fact_service,
        clock,
        id_generator,
        secret_redactor,
    )

    return ApplicationContainer(
        settings=settings,
        clock=clock,
        database=database,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        health_service=health_service,
        mock_market_snapshot_coordinator=coordinator,
        provider_router=provider_router,
        vendor_registry=vendor_registry,
        routed_market_snapshot_service=routed_market_snapshot_service,
        investment_case_service=investment_case_service,
        thesis_revision_service=thesis_revision_service,
        watchlist_service=watchlist_service,
        watchlist_hub_service=watchlist_hub_service,
        research_state_query_service=research_state_query_service,
        open_question_service=open_question_service,
        research_unit_of_work_factory=research_unit_of_work_factory,
        instrument_master_service=instrument_master_service,
        instrument_resolve_service=instrument_resolve_service,
        instrument_unit_of_work_factory=instrument_unit_of_work_factory,
        evidence_service=evidence_service,
        research_archive_service=research_archive_service,
        research_search_service=research_search_service,
        research_timeline_service=research_timeline_service,
        journal_service=journal_service,
        decision_record_service=decision_record_service,
        a_share_trading_calendar=a_share_calendar,
        a_share_snapshot_service=a_share_snapshot_service,
        a_share_market_structure_service=a_share_market_structure_service,
        a_share_capital_service=a_share_capital_service,
        a_share_limit_up_service=a_share_limit_up_service,
        a_share_sentiment_service=a_share_sentiment_service,
        a_share_etf_option_service=a_share_etf_option_service,
        industry_metric_repository=industry_metric_repository,
        research_report_search_service=research_report_search_service,
        a_share_tool_coordinator=a_share_tool_coordinator,
        us_market_data_service=us_market_data_service,
        us_market_breadth_service=us_market_breadth_service,
        us_market_context_service=us_market_context_service,
        us_technical_service=us_technical_service,
        us_tool_coordinator=us_tool_coordinator,
        technical_tool_coordinator=technical_tool_coordinator,
        us_fundamental_service=us_fundamental_service,
        us_filing_service=us_filing_service,
        us_company_update_service=us_company_update_service,
        us_research_tool_coordinator=us_research_tool_coordinator,
        us_news_service=us_news_service,
        us_macro_service=us_macro_service,
        us_sentiment_service=us_sentiment_service,
        us_prediction_market_service=us_prediction_market_service,
        us_context_tool_coordinator=us_context_tool_coordinator,
        account_snapshot_repository=account_snapshot_repository,
        account_service=account_service,
        portfolio_service=portfolio_service,
        portfolio_tool_coordinator=portfolio_tool_coordinator,
        risk_policy_repository=risk_policy_repository,
        risk_policy_service=risk_policy_service,
        risk_engine_service=risk_engine_service,
        risk_tool_coordinator=risk_tool_coordinator,
        monitor_repository=monitor_repository,
        monitor_service=monitor_service,
        monitor_evaluation_service=monitor_evaluation_service,
        monitor_tool_coordinator=monitor_tool_coordinator,
        monitor_run_lock=monitor_run_lock,
        post_market_sync_service=post_market_sync_service,
        post_market_sync_lock=post_market_sync_lock,
        research_context_builder=research_context_builder,
        challenge_review_repository=challenge_review_repository,
        challenge_review_service=challenge_review_service,
        account_transaction_repository=account_transaction_repository,
        account_transaction_coordinator=account_transaction_coordinator,
        workflow_run_repository=workflow_run_repository,
        portfolio_review_fact_service=portfolio_review_fact_service,
        research_workflow_orchestrator=research_workflow_orchestrator,
        _owned_a_share_transport=owned_a_share_transport,
        _owned_polymarket_transport=owned_polymarket_transport,
    )


def load_settings() -> AppSettings:
    """Load settings from the project-root ``.env`` (I/O — call from main only)."""
    return AppSettings.load()


def build_default_application() -> ApplicationContainer:
    """Load settings and build the container (composition-root helper for main)."""
    return build_application(load_settings())
