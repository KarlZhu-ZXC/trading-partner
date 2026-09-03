"""Composition root — the only module that wires application and infrastructure."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.agent_handoff_repository import AgentHandoffRepository
from application.ports.agent_pending_action_repository import AgentPendingActionRepository
from application.ports.agent_preferences_repository import AgentPreferencesRepository
from application.ports.challenge_review_repository import ChallengeReviewRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.industry_metric_repository import IndustryMetricRepository
from application.ports.instrument_unit_of_work import InstrumentUnitOfWork
from application.ports.notification_sender import NotificationSender
from application.ports.operational_maintenance import OperationalMaintenancePort
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.risk_policy_repository import RiskPolicyRepository
from application.ports.secret_redactor import SecretRedactor
from application.runtime import ApplicationServices, RuntimeContext
from application.services.account_service import AccountService
from application.services.agent_conversation_metrics import AgentConversationMetricsService
from application.services.attention_query_service import AttentionQueryService
from application.services.broker_order_service import BrokerOrderService
from application.services.cash_sweep_shadow_service import CashSweepShadowService
from application.services.catalyst_agenda_notification_service import (
    CatalystAgendaNotificationService,
)
from application.services.catalyst_agenda_service import CatalystAgendaService
from application.services.catalyst_agenda_sync_service import CatalystAgendaSyncService
from application.services.challenge_review_service import ChallengeReviewService
from application.services.commodity_spot_service import CommoditySpotService
from application.services.criticality_policy import CriticalityPolicy
from application.services.data_quality_service import DataQualityService
from application.services.decision_record_service import DecisionRecordService
from application.services.futures_contract_service import FuturesContractService
from application.services.futures_curve_service import FuturesCurveService
from application.services.futures_instrument_directory import FuturesInstrumentDirectory
from application.services.health_service import HealthService
from application.services.historical_validation_service import HistoricalValidationService
from application.services.instrument_access_service import InstrumentAccessService
from application.services.instrument_master_service import InstrumentMasterService
from application.services.instrument_resolve_service import InstrumentResolveService
from application.services.journal_service import JournalService
from application.services.judgment_scorecard_service import JudgmentScorecardService
from application.services.monitor_dispatch_service import MonitorDispatchService
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_tool_coordinator import MonitorToolCoordinator
from application.services.notification_service import NotificationService
from application.services.operational_job_runtime import DurableOperationalJobRuntime
from application.services.peer_comparison_service import PeerComparisonService
from application.services.performance_reconciliation_service import (
    PerformanceReconciliationService,
)
from application.services.portfolio_enrichment_calculator import PortfolioEnrichmentCalculator
from application.services.portfolio_review_fact_service import PortfolioReviewFactService
from application.services.portfolio_risk_calculator import PortfolioRiskCalculator
from application.services.portfolio_service import PortfolioService
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from application.services.position_sizing_service import PositionSizingService
from application.services.post_market_sync_service import PostMarketSyncService
from application.services.provider_router import ProviderRouter
from application.services.research_archive_service import ResearchArchiveService
from application.services.research_context_builder import ResearchContextBuilder
from application.services.research_search_service import ResearchSearchService
from application.services.research_state_query_service import ResearchStateQueryService
from application.services.research_subject_service import ResearchSubjectService
from application.services.research_timeline_service import ResearchTimelineService
from application.services.research_workflow_orchestrator import ResearchWorkflowOrchestrator
from application.services.review_item_service import ReviewItemService
from application.services.risk_engine_service import RiskEngineService
from application.services.risk_policy_service import RiskPolicyService
from application.services.risk_tool_coordinator import RiskToolCoordinator
from application.services.routed_futures_provider import RoutedFuturesProvider
from application.services.sgov_shadow_plan_service import SgovShadowPlanService
from application.services.thesis_revision_service import ThesisRevisionService
from application.services.trade_retro_service import TradeRetroService
from application.services.watchlist_hub_service import WatchlistHubService
from composition_root.external_notes import build_external_note_services
from composition_root.market_facts import build_market_facts_services
from composition_root.monitoring import build_monitoring_services
from composition_root.phase4 import build_phase4_services
from domain.common.enums import DataCategory, Market, VendorId
from domain.company_comparison.calculator import PeerComparisonCalculator
from infrastructure.artifacts.trade_retro import ObsidianTradeRetroExporter
from infrastructure.composition import (
    CompositionOverrides,
    ProviderCompositionOverrides,
    RuntimeResources,
    build_agent_attachment_store,
    build_agent_model_providers,
    build_agent_web_search_provider,
    build_persistence_infrastructure,
    build_provider_infrastructure,
    build_trade_retro_narrative_provider,
    enabled_account_provider_order,
)
from infrastructure.config.account_basis_checkpoints import load_account_basis_checkpoints
from infrastructure.config.settings import AppSettings
from infrastructure.observability.tracing import configure_tracing
from infrastructure.persistence.audit_log_writer import SqlAlchemyAuditLogWriter
from infrastructure.persistence.catalyst_agenda_outcome_reader import (
    SqlAlchemyCatalystAgendaOutcomeReader,
)
from infrastructure.persistence.challenge_review_repository import (
    SqlAlchemyChallengeReviewRepository,
)
from infrastructure.persistence.instrument_unit_of_work import (
    SqlAlchemyInstrumentUnitOfWork,
)
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from infrastructure.persistence.operational_maintenance import SqliteOperationalMaintenance
from infrastructure.persistence.risk_policy_repository import (
    SqlAlchemyRiskPolicyRepository,
)
from infrastructure.persistence.sqlalchemy_futures_definition_repository import (
    SqlAlchemyFuturesDefinitionRepository,
)
from infrastructure.providers.account.reconciliation_writer import (
    OwnerOnlyBrokerReconciliationWriter,
)
from infrastructure.providers.account.schwab_oauth import (
    SchwabOAuthFlowManager,
    SchwabOAuthTokenInspector,
)
from infrastructure.providers.account.schwab_statement_csv import (
    SchwabRealizedGainLossCsvParser,
)
from infrastructure.providers.instrument_directory import (
    AlphaVantageInstrumentDirectoryAdapter,
    TencentInstrumentDirectoryAdapter,
    YahooInstrumentDirectoryAdapter,
)
from infrastructure.providers.notifications.telegram import TelegramNotificationAdapter
from infrastructure.providers.registry import VendorRegistry
from infrastructure.providers.us.catalyst_calendar_codecs import catalyst_calendar_codec
from infrastructure.system.agent_turn_lock import AgentTurnLockFactory
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator
from infrastructure.system.process_file_lock import ProcessFileLock
from infrastructure.system.redactor import DefaultSecretRedactor

UowFactory = Callable[[], ResearchUnitOfWork]
InstrumentUowFactory = Callable[[], InstrumentUnitOfWork]
BootstrapOverrides = CompositionOverrides


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    router: ProviderRouter
    registry: VendorRegistry


@dataclass(frozen=True, slots=True)
class OperationalServices:
    industry_metrics: IndustryMetricRepository
    futures_contracts: FuturesContractService
    monitor_evaluation: MonitorEvaluationService
    notifications: NotificationService
    monitor_dispatch: MonitorDispatchService
    post_market_sync: PostMarketSyncService
    sgov_shadow_plan: SgovShadowPlanService
    maintenance: OperationalMaintenancePort
    performance_reconciliation: PerformanceReconciliationService
    schwab_oauth: SchwabOAuthFlowManager | None
    catalyst_agenda_sync: CatalystAgendaSyncService
    catalyst_agenda_notifications: CatalystAgendaNotificationService
    agent_conversations: AgentConversationRepository
    agent_handoffs: AgentHandoffRepository
    agent_pending_actions: AgentPendingActionRepository
    agent_metrics: AgentConversationMetricsService
    agent_preferences: AgentPreferencesRepository
    jobs: DurableOperationalJobRuntime

    @property
    def monitor_notifications(self) -> NotificationService:
        return self.notifications


@dataclass(slots=True)
class ApplicationContainer:
    settings: AppSettings
    context: RuntimeContext
    resources: RuntimeResources
    providers: ProviderBundle
    services: ApplicationServices
    operations: OperationalServices

    def close(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError("running event loop: await container.aclose()")

    async def aclose(self) -> None:
        await self.resources.aclose()


def build_application(
    settings: AppSettings, *, overrides: BootstrapOverrides | None = None
) -> ApplicationContainer:
    """Construct services from settings without I/O at import time."""
    telemetry = configure_tracing(settings)
    overrides = overrides or BootstrapOverrides()
    clock: Clock = overrides.clock or SystemClock()
    id_generator: IdGenerator = Uuid7IdGenerator()
    secret_redactor: SecretRedactor = DefaultSecretRedactor()
    owned_notification_sender: NotificationSender | None = None
    notification_sender = overrides.notification_sender or overrides.monitor_notification_sender
    if notification_sender is None and settings.notifications_enabled:
        assert settings.telegram_bot_token is not None
        assert settings.telegram_chat_id is not None
        owned_notification_sender = TelegramNotificationAdapter(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            message_thread_id=settings.telegram_message_thread_id,
            timeout_seconds=settings.provider_timeout_default_seconds,
            proxy_url=settings.provider_proxy_url,
        )
        notification_sender = owned_notification_sender
    persistence = build_persistence_infrastructure(
        settings,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
    )
    engine = persistence.engine
    database = persistence.database
    account_snapshot_repository = persistence.account_snapshots
    account_transaction_repository = persistence.account_transactions
    workflow_run_repository = persistence.workflow_runs
    industry_metric_repository = persistence.industry_metrics
    post_market_sync_run_repository = persistence.post_market_sync_runs
    research_unit_of_work_factory = persistence.research_uow_factory
    watchlist_hub_unit_of_work_factory = persistence.watchlist_uow_factory
    historical_validation_service = HistoricalValidationService(
        persistence.historical_validation_artifacts,
        clock,
        id_generator,
        secret_redactor,
    )
    maintenance_service = SqliteOperationalMaintenance(
        engine=engine,
        database_url=settings.database_url,
        artifact_root=settings.paths.historical_validation,
        backup_root=settings.paths.backups,
        clock=clock,
    )
    health_service = HealthService(
        database=database,
        settings=settings,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        search_backend_probe=persistence.search_backend_probe,
        component_probes={
            "cross_asset.cme_reference": lambda: True,
            "cross_asset.dce_eod": lambda: True,
            "cross_asset.dukascopy_spot": lambda: settings.dukascopy_enabled,
            "cross_asset.ig_weekend_gold": lambda: (
                not settings.ig_weekend_gold_enabled or settings.apify_api_token is not None
            ),
        },
    )
    provider_infrastructure = build_provider_infrastructure(
        settings,
        engine=engine,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        overrides=ProviderCompositionOverrides(
            a_share_transport=overrides.a_share_transport,
            eastmoney_gate=overrides.eastmoney_gate,
            a_share_calendar=overrides.a_share_calendar,
            watchlist_provider=overrides.watchlist_provider,
        ),
    )
    chain_config = provider_infrastructure.chain_config
    vendor_registry = provider_infrastructure.registry
    a_share_calendar = provider_infrastructure.a_share_calendar
    a_share_transport = provider_infrastructure.a_share_transport
    market_timeout = settings.provider_timeout_market_seconds
    cme_public_adapter = provider_infrastructure.cme_public
    dce_official_adapter = provider_infrastructure.dce_official
    commodity_spot_adapter = provider_infrastructure.commodity_spot
    schwab_account_provider = provider_infrastructure.schwab_account
    schwab_quote_provider = provider_infrastructure.schwab_quote
    schwab_order_provider = provider_infrastructure.schwab_order
    moomoo_account_provider = provider_infrastructure.moomoo_account
    watchlist_source_provider = provider_infrastructure.watchlist_source
    provider_router = ProviderRouter(
        engine=provider_infrastructure.router_engine,
        chain_config=chain_config,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        criticality_policy=CriticalityPolicy(),
    )
    cash_sweep_shadow_service = CashSweepShadowService(
        account_snapshot_repository,
        schwab_quote_provider,
        clock,
        id_generator,
        secret_redactor,
    )
    broker_order_service = BrokerOrderService(
        persistence.broker_orders,
        schwab_order_provider,
        schwab_quote_provider,
        SqlAlchemyAuditLogWriter(engine, clock, id_generator, secret_redactor),
        clock,
        id_generator,
        secret_redactor,
        research_unit_of_work_factory,
    )
    review_item_service = ReviewItemService(
        persistence.review_items,
        clock,
        id_generator,
    )
    phase4 = build_phase4_services(
        providers={
            VendorId.SCHWAB: schwab_account_provider,
            VendorId.MOOMOO: moomoo_account_provider,
        },
        persistence=persistence,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        review_items=review_item_service,
        basis_checkpoints=(
            load_account_basis_checkpoints(settings.account_basis_checkpoints_path)
            if settings.account_basis_checkpoints_path is not None
            else ()
        ),
    )

    def instrument_unit_of_work_factory() -> InstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    research_subject_service = ResearchSubjectService(
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
    instrument_master_service = InstrumentMasterService(
        instrument_unit_of_work_factory,
    )
    futures_definition_repository = SqlAlchemyFuturesDefinitionRepository(engine)
    routed_futures_provider = RoutedFuturesProvider(
        {
            Market.CME: cme_public_adapter,
            Market.DCE: dce_official_adapter,
        }
    )
    futures_contract_service = FuturesContractService(
        reference_provider=routed_futures_provider,
        statistics_provider=routed_futures_provider,
        repository=futures_definition_repository,
        clock=clock,
        id_generator=id_generator,
    )
    futures_curve_service = FuturesCurveService(
        contract_service=futures_contract_service,
        clock=clock,
    )
    commodity_spot_service = CommoditySpotService(
        provider=commodity_spot_adapter,
        clock=clock,
    )
    yahoo_instrument_directory = YahooInstrumentDirectoryAdapter(
        a_share_transport,
        clock=clock,
        enabled=settings.yfinance_enabled,
        timeout_seconds=market_timeout,
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
                yahoo_instrument_directory,
                AlphaVantageInstrumentDirectoryAdapter(
                    a_share_transport,
                    api_keys=settings.alpha_vantage_api_keys,
                    clock=clock,
                    enabled=settings.alpha_vantage_enabled,
                    timeout_seconds=market_timeout,
                ),
            ),
            Market.KR: (yahoo_instrument_directory,),
            Market.CME: (
                FuturesInstrumentDirectory(
                    market=Market.CME,
                    vendor_id=VendorId.CME_PUBLIC,
                    contract_service=futures_contract_service,
                    clock=clock,
                ),
            ),
            Market.DCE: (
                FuturesInstrumentDirectory(
                    market=Market.DCE,
                    vendor_id=VendorId.DCE_OFFICIAL,
                    contract_service=futures_contract_service,
                    clock=clock,
                ),
            ),
        },
    )
    access_service = InstrumentAccessService(instrument_master_service, instrument_resolve_service)
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
    market_facts = build_market_facts_services(
        settings=settings,
        engine=engine,
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
        provider_router=provider_router,
        access_service=access_service,
        instrument_master_service=instrument_master_service,
        commodity_spot_service=commodity_spot_service,
        futures_curve_service=futures_curve_service,
        industry_metric_repository=industry_metric_repository,
        a_share_calendar=a_share_calendar,
    )
    account_service = AccountService(
        provider_infrastructure.account_providers,
        account_snapshot_repository,
        clock,
        default_order=enabled_account_provider_order(
            chain_config.chain_for(Market.US, DataCategory.ACCOUNT),
            settings.holdings_sources,
        ),
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
    position_sizing_service = PositionSizingService(research_unit_of_work_factory)
    risk_engine_service = RiskEngineService(
        account_service, risk_policy_service, position_sizing_service
    )
    risk_tool_coordinator = RiskToolCoordinator(
        risk_engine_service,
        risk_policy_service,
        clock,
        id_generator,
        secret_redactor,
    )
    monitor_repository = SqlAlchemyMonitorRepository(engine)
    data_quality_service = DataQualityService(
        account_snapshot_repository,
        account_transaction_repository,
        monitor_repository,
        provider_infrastructure.route_history_store,
        research_unit_of_work_factory,
        clock,
        id_generator,
        secret_redactor,
        persistence.catalyst_agenda_sync,
    )
    monitoring = build_monitoring_services(
        settings=settings,
        repository=monitor_repository,
        market_facts=market_facts,
        risk=risk_tool_coordinator,
        research_uow_factory=research_unit_of_work_factory,
        a_share_calendar=a_share_calendar,
        llm_resilience=provider_infrastructure.llm_resilience,
        clock=clock,
        id_generator=id_generator,
    )
    us_market_calendar = monitoring.us_calendar
    monitor_schedule_service = monitoring.schedule
    monitor_service = monitoring.service
    monitor_evaluation_service = monitoring.evaluation
    monitor_judgment_provider = monitoring.judgment_provider
    monitor_judgment_fallback_provider = monitoring.judgment_fallback_provider
    monitor_event_analysis_provider = monitoring.event_analysis_provider
    external_notes = build_external_note_services(
        settings=settings,
        repository=persistence.external_notes,
        review_repository=persistence.external_note_reviews,
        research_uow_factory=research_unit_of_work_factory,
        account_snapshots=account_snapshot_repository,
        monitors=monitor_repository,
        clock=clock,
        id_generator=id_generator,
        resilience=provider_infrastructure.llm_resilience,
    )
    trade_retro_narrative_provider = build_trade_retro_narrative_provider(settings)
    agent_model_providers = build_agent_model_providers(
        settings,
        resilience=provider_infrastructure.llm_resilience,
    )
    default_agent_model_id = settings.default_agent_llm_id
    agent_model_provider = (
        agent_model_providers.get(default_agent_model_id)
        if default_agent_model_id is not None
        else None
    )
    agent_web_search_provider = build_agent_web_search_provider(
        settings,
        agent_model_providers,
        clock,
    )
    trade_retro_service = TradeRetroService(
        persistence.trade_retro,
        account_transaction_repository,
        research_unit_of_work_factory,
        ObsidianTradeRetroExporter(settings.retro_obsidian_journal_dir),
        clock,
        id_generator,
        secret_redactor,
        trade_retro_narrative_provider,
    )
    judgment_scorecard_service = JudgmentScorecardService(
        persistence.scorecards,
        persistence.catalyst_agenda,
        research_unit_of_work_factory,
        monitor_repository,
        persistence.trade_retro,
        clock,
        id_generator,
        secret_redactor,
    )
    catalyst_agenda_service = CatalystAgendaService(
        persistence.catalyst_agenda,
        persistence.catalyst_agenda_scope,
        SqlAlchemyCatalystAgendaOutcomeReader(research_unit_of_work_factory),
        clock,
        id_generator,
        secret_redactor,
    )
    catalyst_agenda_sync_service = CatalystAgendaSyncService(
        router=provider_router,
        agenda_repository=persistence.catalyst_agenda,
        sync_repository=persistence.catalyst_agenda_sync,
        scope_reader=persistence.catalyst_agenda_scope,
        instrument_uow_factory=instrument_unit_of_work_factory,
        calendar_codec=catalyst_calendar_codec(),
        clock=clock,
        id_generator=id_generator,
    )
    attention_query_service = AttentionQueryService(
        clock,
        review_item_service,
        research_unit_of_work_factory,
        catalyst_agenda_service,
        trade_retro_service,
        judgment_scorecard_service,
        data_quality_service,
        broker_order_service,
        persistence.agent_pending_actions,
    )
    notification_service = NotificationService(
        monitor_repository,
        notification_sender,
        clock,
        enabled=settings.notifications_enabled,
        configured=(
            settings.telegram_bot_token is not None and settings.telegram_chat_id is not None
        ),
        max_attempts=settings.notification_max_attempts,
        ttl_hours=settings.notification_ttl_hours,
        batch_size=settings.notification_batch_size,
    )
    catalyst_agenda_notification_service = CatalystAgendaNotificationService(
        catalyst_agenda_service,
        persistence.catalyst_agenda,
        notification_service,
        clock,
    )
    monitor_dispatch_service = MonitorDispatchService(
        monitor_repository,
        monitor_evaluation_service,
        notification_service,
        monitor_schedule_service,
        clock,
    )
    monitor_tool_coordinator = MonitorToolCoordinator(
        monitor_service,
        monitor_evaluation_service,
        clock,
        id_generator,
        secret_redactor,
    )
    monitor_lock_path = settings.post_market_sync_lock_path.parent / "monitoring.lock"
    monitor_run_lock = ProcessFileLock(monitor_lock_path)
    post_market_sync_service = PostMarketSyncService(
        calendar=us_market_calendar,
        repository=post_market_sync_run_repository,
        portfolio=portfolio_tool_coordinator,
        watchlist=watchlist_hub_service,
        clock=clock,
        id_generator=id_generator,
        delay_minutes=settings.post_market_sync_delay_minutes,
        schwab_oauth_health=SchwabOAuthTokenInspector(
            token_path=settings.schwab_token_path,
            enabled="SCHWAB" in settings.holdings_sources,
        ),
        transactions=phase4.account_transactions,
        activity_annotations=phase4.activity_annotations,
        account_snapshots=account_snapshot_repository,
        transaction_repository=account_transaction_repository,
        daily_equity=phase4.daily_equity,
        external_notes=external_notes.service,
    )
    sgov_shadow_plan_service = SgovShadowPlanService(
        calendar=us_market_calendar,
        portfolio=portfolio_tool_coordinator,
        preview=cash_sweep_shadow_service,
        broker_orders=broker_order_service,
        notifications=notification_service,
        clock=clock,
        hard_cash_floor=settings.sgov_shadow_hard_cash_floor,
        operational_buffer=settings.sgov_shadow_operational_buffer,
        minimum_order_notional=settings.sgov_shadow_minimum_order_notional,
    )
    schwab_oauth_flow_manager = None
    if settings.schwab_client_id and settings.schwab_client_secret:
        schwab_oauth_flow_manager = SchwabOAuthFlowManager(
            client_id=settings.schwab_client_id,
            client_secret=settings.schwab_client_secret,
            redirect_uri=settings.schwab_redirect_uri,
            token_path=settings.schwab_token_path,
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
    reconciliation_root = settings.paths.reconciliation
    performance_reconciliation_service = PerformanceReconciliationService(
        SchwabRealizedGainLossCsvParser(reconciliation_root),
        phase4.account_transactions,
        OwnerOnlyBrokerReconciliationWriter(reconciliation_root),
        clock,
    )
    portfolio_review_fact_service = PortfolioReviewFactService(
        account_service,
        instrument_resolve_service,
        research_context_builder,
        market_facts.a_share,
        market_facts.us,
        market_facts.us_research,
        PortfolioRiskCalculator(),
        PortfolioEnrichmentCalculator(),
        clock,
        id_generator,
        secret_redactor,
    )
    peer_comparison_service = PeerComparisonService(
        a_share=market_facts.a_share,
        us_research=market_facts.us_research,
        calculator=PeerComparisonCalculator(),
        clock=clock,
        id_generator=id_generator,
        secret_redactor=secret_redactor,
    )
    research_workflow_orchestrator = ResearchWorkflowOrchestrator(
        workflow_run_repository,
        research_subject_service,
        research_context_builder,
        research_archive_service,
        market_facts.a_share,
        market_facts.us,
        market_facts.us_research,
        market_facts.us_context,
        portfolio_tool_coordinator,
        phase4.account_transactions,
        portfolio_review_fact_service,
        peer_comparison_service,
        clock,
        id_generator,
        secret_redactor,
    )
    return ApplicationContainer(
        settings=settings,
        context=RuntimeContext(
            clock=clock,
            id_generator=id_generator,
            secret_redactor=secret_redactor,
            telemetry=telemetry,
        ),
        resources=RuntimeResources(
            database=database,
            monitor_run_lock=monitor_run_lock,
            post_market_sync_lock=post_market_sync_lock,
            a_share_transport=provider_infrastructure.owned_a_share_transport,
            cross_asset_transport=provider_infrastructure.owned_cross_asset_transport,
            notification_sender=owned_notification_sender,
            monitor_judgment_provider=monitor_judgment_provider,
            monitor_judgment_fallback_provider=monitor_judgment_fallback_provider,
            monitor_event_analysis_provider=monitor_event_analysis_provider,
            external_note_analysis_provider=external_notes.analysis_provider,
            trade_retro_narrative_provider=trade_retro_narrative_provider,
            agent_model_provider=agent_model_provider,
            agent_model_providers=agent_model_providers,
            agent_attachment_store=build_agent_attachment_store(settings),
            agent_web_search_provider=agent_web_search_provider,
            agent_turn_lock_factory=AgentTurnLockFactory(
                settings.telegram_agent_lock_path.parent / "agent_turns"
            ),
        ),
        providers=ProviderBundle(router=provider_router, registry=vendor_registry),
        services=ApplicationServices(
            health=health_service,
            data_quality=data_quality_service,
            research_subjects=research_subject_service,
            thesis_revisions=thesis_revision_service,
            research_state=research_state_query_service,
            research_archive=research_archive_service,
            research_search=research_search_service,
            research_timeline=research_timeline_service,
            journal=journal_service,
            decisions=decision_record_service,
            instruments=instrument_resolve_service,
            a_share=market_facts.a_share,
            us_market=market_facts.us,
            market=market_facts.market,
            technical=market_facts.technical,
            us_research=market_facts.us_research,
            us_context=market_facts.us_context,
            portfolio=portfolio_tool_coordinator,
            risk=risk_tool_coordinator,
            monitoring=monitor_tool_coordinator,
            research_context=research_context_builder,
            challenge=challenge_review_service,
            account_transactions=phase4.account_transactions,
            activity_annotations=phase4.activity_annotations,
            trade_cycle_overrides=phase4.trade_cycle_overrides,
            behavior_reviews=phase4.behavior_reviews,
            daily_equity=phase4.daily_equity,
            workflows=research_workflow_orchestrator,
            historical_validation=historical_validation_service,
            watchlist=watchlist_hub_service,
            trade_retro=trade_retro_service,
            scorecards=judgment_scorecard_service,
            catalyst_agenda=catalyst_agenda_service,
            broker_order_preview=cash_sweep_shadow_service,
            broker_orders=broker_order_service,
            review_items=review_item_service,
            attention=attention_query_service,
            external_notes=external_notes.service,
            external_note_reviews=external_notes.reviews,
            view_reviews=external_notes.view_reviews,
        ),
        operations=OperationalServices(
            industry_metrics=industry_metric_repository,
            futures_contracts=futures_contract_service,
            monitor_evaluation=monitor_evaluation_service,
            notifications=notification_service,
            monitor_dispatch=monitor_dispatch_service,
            post_market_sync=post_market_sync_service,
            sgov_shadow_plan=sgov_shadow_plan_service,
            maintenance=maintenance_service,
            performance_reconciliation=performance_reconciliation_service,
            schwab_oauth=schwab_oauth_flow_manager,
            catalyst_agenda_sync=catalyst_agenda_sync_service,
            catalyst_agenda_notifications=catalyst_agenda_notification_service,
            agent_conversations=persistence.agent_conversations,
            agent_handoffs=persistence.agent_handoffs,
            agent_pending_actions=persistence.agent_pending_actions,
            agent_metrics=AgentConversationMetricsService(persistence.agent_conversations),
            agent_preferences=persistence.agent_preferences,
            jobs=DurableOperationalJobRuntime(persistence.operational_jobs, clock, id_generator),
        ),
    )


def load_settings(env_file: Path | None = None) -> AppSettings:
    return AppSettings.load(env_file=env_file)


def build_telegram_agent_client(container: ApplicationContainer) -> Any:
    from interfaces.telegram.agent_client import TelegramBotAgentClient

    settings = container.settings
    if not settings.telegram_bot_token:
        raise ValueError("Telegram Agent bot token is not configured")
    return TelegramBotAgentClient(
        bot_token=settings.telegram_bot_token,
        timeout_seconds=max(settings.provider_timeout_default_seconds, 35.0),
        proxy_url=settings.provider_proxy_url,
    )


def build_telegram_agent_lock(path: Path) -> Any:
    return ProcessFileLock(path)


def build_default_application() -> ApplicationContainer:
    return build_application(load_settings())


def build_schwab_oauth_flow_manager() -> SchwabOAuthFlowManager:
    settings = load_settings()
    if not settings.schwab_client_id or not settings.schwab_client_secret:
        raise ValueError("Schwab client credentials are not configured")
    return SchwabOAuthFlowManager(
        client_id=settings.schwab_client_id,
        client_secret=settings.schwab_client_secret,
        redirect_uri=settings.schwab_redirect_uri,
        token_path=settings.schwab_token_path,
    )
