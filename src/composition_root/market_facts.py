"""Market-facts graph builders extracted from the bootstrap composition root.

Builds the A-share and US market/company fact services plus their four tool
coordinators. The module exists so `bootstrap.py` stays a bounded facade; it
is part of the top-level composition root and may import application services
and infrastructure implementations, exactly like `bootstrap.py` itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.industry_metric_repository import IndustryMetricRepository
from application.ports.secret_redactor import SecretRedactor
from application.services.a_share_capital_service import AShareCapitalService
from application.services.a_share_company_operating_metrics_service import (
    AShareCompanyOperatingMetricsService,
)
from application.services.a_share_etf_option_service import AShareEtfOptionService
from application.services.a_share_industry_cycle_service import AShareIndustryCycleService
from application.services.a_share_limit_up_service import AShareLimitUpService
from application.services.a_share_market_structure_service import AShareMarketStructureService
from application.services.a_share_sentiment_service import AShareSentimentService
from application.services.a_share_snapshot_service import AShareSnapshotService
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.commodity_spot_service import CommoditySpotService
from application.services.futures_curve_service import FuturesCurveService
from application.services.instrument_access_service import InstrumentAccessService
from application.services.instrument_master_service import InstrumentMasterService
from application.services.market_tool_coordinator import MarketToolCoordinator
from application.services.provider_router import ProviderRouter
from application.services.research_report_search_service import ResearchReportSearchService
from application.services.technical_tool_coordinator import TechnicalToolCoordinator
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
from infrastructure.config.settings import AppSettings
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
from infrastructure.providers.us.codecs import (
    us_bars_codec,
    us_quote_codec,
)
from infrastructure.providers.us.context_codecs import (
    us_community_heat_codec,
    us_macro_context_codec,
    us_market_breadth_codec,
    us_news_feed_codec,
    us_prediction_market_context_codec,
    us_sentiment_samples_codec,
)
from infrastructure.providers.us.research_codecs import (
    us_corporate_actions_codec,
    us_filings_codec,
    us_financial_statements_codec,
    us_fundamental_snapshot_codec,
    us_insider_activity_codec,
)
from infrastructure.technical import (
    MatplotlibChartRenderer,
    TALibIndicatorEngine,
)


@dataclass(frozen=True, slots=True)
class MarketFactsBundle:
    """Tool coordinators owned by the market-facts graph."""

    a_share: AShareToolCoordinator
    market: MarketToolCoordinator
    technical: TechnicalToolCoordinator
    us: USToolCoordinator
    us_research: USResearchToolCoordinator
    us_context: USContextToolCoordinator


def build_market_facts_services(
    *,
    settings: AppSettings,
    engine: Engine,
    clock: Clock,
    id_generator: IdGenerator,
    secret_redactor: SecretRedactor,
    provider_router: ProviderRouter,
    access_service: InstrumentAccessService,
    instrument_master_service: InstrumentMasterService,
    commodity_spot_service: CommoditySpotService,
    futures_curve_service: FuturesCurveService,
    industry_metric_repository: IndustryMetricRepository,
    a_share_calendar: AShareTradingCalendar,
) -> MarketFactsBundle:
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
        instrument_access=access_service,
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
    us_quote_cache_codec = us_quote_codec()
    us_bars_cache_codec = us_bars_codec()
    us_market_data_service = USMarketDataService(
        router=provider_router,
        clock=clock,
        quote_codec=us_quote_cache_codec,
        bars_codec=us_bars_cache_codec,
        current_quote_window_seconds=settings.us_current_window_seconds,
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
        instrument_access=access_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        data_service=us_market_data_service,
        context_service=us_market_context_service,
        technical_service=us_technical_service,
    )
    market_tool_coordinator = MarketToolCoordinator(
        instrument_access=access_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        us_tool_coordinator=us_tool_coordinator,
        data_service=us_market_data_service,
        commodity_spot_service=commodity_spot_service,
        futures_curve_service=futures_curve_service,
    )
    technical_tool_coordinator = TechnicalToolCoordinator(
        instrument_access=access_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        us_data_service=us_market_data_service,
        a_share_data_service=a_share_market_structure_service,
        indicator_engine=TALibIndicatorEngine(),
        chart_renderer=MatplotlibChartRenderer(),
        commodity_spot_service=commodity_spot_service,
    )
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
        instrument_access=access_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        fundamental_service=us_fundamental_service,
        filing_service=us_filing_service,
        company_update_service=us_company_update_service,
    )
    us_macro_service = USMacroService(provider_router, clock, us_macro_context_codec())
    us_sentiment_service = USSentimentService(provider_router, clock, us_sentiment_samples_codec())
    us_prediction_market_service = USPredictionMarketService(
        provider_router, clock, us_prediction_market_context_codec()
    )
    us_context_tool_coordinator = USContextToolCoordinator(
        instrument_access=access_service,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        news_service=us_news_service,
        macro_service=us_macro_service,
        sentiment_service=us_sentiment_service,
        prediction_service=us_prediction_market_service,
    )
    return MarketFactsBundle(
        a_share=a_share_tool_coordinator,
        market=market_tool_coordinator,
        technical=technical_tool_coordinator,
        us=us_tool_coordinator,
        us_research=us_research_tool_coordinator,
        us_context=us_context_tool_coordinator,
    )
