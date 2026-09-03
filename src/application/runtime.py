"""Application-only runtime service and cross-cutting context bundles."""

from __future__ import annotations

from dataclasses import dataclass

from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.ports.telemetry import NOOP_TELEMETRY, Telemetry
from application.services.a_share_tool_coordinator import AShareToolCoordinator
from application.services.account_transaction_coordinator import AccountTransactionCoordinator
from application.services.activity_annotation_service import ActivityAnnotationService
from application.services.attention_query_service import AttentionQueryService
from application.services.behavior_review_service import BehaviorReviewService
from application.services.broker_order_service import BrokerOrderService
from application.services.cash_sweep_shadow_service import CashSweepShadowService
from application.services.catalyst_agenda_service import CatalystAgendaService
from application.services.challenge_review_service import ChallengeReviewService
from application.services.daily_equity_materialization_service import (
    DailyEquityMaterializationService,
)
from application.services.data_quality_service import DataQualityService
from application.services.decision_record_service import DecisionRecordService
from application.services.external_note_sync_service import ExternalNoteSyncService
from application.services.health_service import HealthService
from application.services.historical_validation_service import HistoricalValidationService
from application.services.instrument_resolve_service import InstrumentResolveService
from application.services.journal_service import JournalService
from application.services.judgment_scorecard_service import JudgmentScorecardService
from application.services.market_tool_coordinator import MarketToolCoordinator
from application.services.monitor_tool_coordinator import MonitorToolCoordinator
from application.services.portfolio_tool_coordinator import PortfolioToolCoordinator
from application.services.research_archive_service import ResearchArchiveService
from application.services.research_context_builder import ResearchContextBuilder
from application.services.research_search_service import ResearchSearchService
from application.services.research_state_query_service import ResearchStateQueryService
from application.services.research_subject_service import ResearchSubjectService
from application.services.research_timeline_service import ResearchTimelineService
from application.services.research_workflow_orchestrator import ResearchWorkflowOrchestrator
from application.services.review_item_service import ReviewItemService
from application.services.risk_tool_coordinator import RiskToolCoordinator
from application.services.technical_tool_coordinator import TechnicalToolCoordinator
from application.services.thesis_revision_service import ThesisRevisionService
from application.services.trade_cycle_override_service import TradeCycleOverrideService
from application.services.trade_retro_service import TradeRetroService
from application.services.us_context_tool_coordinator import USContextToolCoordinator
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from application.services.us_tool_coordinator import USToolCoordinator
from application.services.watchlist_hub_service import WatchlistHubService


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """Tool-facing application graph with no infrastructure dependencies."""

    health: HealthService
    data_quality: DataQualityService
    research_subjects: ResearchSubjectService
    thesis_revisions: ThesisRevisionService
    research_state: ResearchStateQueryService
    research_archive: ResearchArchiveService
    research_search: ResearchSearchService
    research_timeline: ResearchTimelineService
    journal: JournalService
    decisions: DecisionRecordService
    instruments: InstrumentResolveService
    a_share: AShareToolCoordinator
    us_market: USToolCoordinator
    market: MarketToolCoordinator
    technical: TechnicalToolCoordinator
    us_research: USResearchToolCoordinator
    us_context: USContextToolCoordinator
    portfolio: PortfolioToolCoordinator
    risk: RiskToolCoordinator
    monitoring: MonitorToolCoordinator
    research_context: ResearchContextBuilder
    challenge: ChallengeReviewService
    account_transactions: AccountTransactionCoordinator
    activity_annotations: ActivityAnnotationService
    trade_cycle_overrides: TradeCycleOverrideService
    behavior_reviews: BehaviorReviewService
    daily_equity: DailyEquityMaterializationService
    workflows: ResearchWorkflowOrchestrator
    historical_validation: HistoricalValidationService
    watchlist: WatchlistHubService
    trade_retro: TradeRetroService
    scorecards: JudgmentScorecardService
    catalyst_agenda: CatalystAgendaService
    broker_order_preview: CashSweepShadowService
    broker_orders: BrokerOrderService
    review_items: ReviewItemService
    attention: AttentionQueryService
    external_notes: ExternalNoteSyncService


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Cross-cutting deterministic application collaborators."""

    clock: Clock
    id_generator: IdGenerator
    secret_redactor: SecretRedactor
    telemetry: Telemetry = NOOP_TELEMETRY
