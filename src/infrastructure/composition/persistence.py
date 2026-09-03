"""Build the persistence adapters without importing application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.activity_annotation_repository import ActivityAnnotationRepository
from application.ports.agent_conversation_repository import AgentConversationRepository
from application.ports.agent_handoff_repository import AgentHandoffRepository
from application.ports.agent_pending_action_repository import AgentPendingActionRepository
from application.ports.agent_preferences_repository import AgentPreferencesRepository
from application.ports.behavior_review_repository import BehaviorReviewRepository
from application.ports.broker_order_repository import BrokerOrderRepository
from application.ports.catalyst_agenda_repository import CatalystAgendaRepository
from application.ports.catalyst_agenda_scope_reader import CatalystAgendaScopeReader
from application.ports.catalyst_agenda_sync_repository import CatalystAgendaSyncRepository
from application.ports.clock import Clock
from application.ports.daily_equity_repository import (
    DailyEquityRepository,
    JournalActivationRepository,
)
from application.ports.external_note_repository import ExternalNoteRepository
from application.ports.historical_validation_artifact_repository import (
    HistoricalValidationArtifactRepository,
)
from application.ports.id_generator import IdGenerator
from application.ports.industry_metric_repository import IndustryMetricRepository
from application.ports.judgment_scorecard_repository import JudgmentScorecardRepository
from application.ports.operational_job_repository import OperationalJobRepository
from application.ports.post_market_sync_run_repository import PostMarketSyncRunRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.review_item_repository import ReviewItemRepository
from application.ports.secret_redactor import SecretRedactor
from application.ports.trade_cycle_override_repository import TradeCycleOverrideRepository
from application.ports.trade_retro_repository import TradeRetroRepository
from application.ports.workflow_run_repository import WorkflowRunRepository
from infrastructure.artifacts.historical_validation import (
    FileHistoricalValidationArtifactRepository,
)
from infrastructure.config.settings import AppSettings
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.activity_annotation_repository import (
    SqlAlchemyActivityAnnotationRepository,
)
from infrastructure.persistence.agent_conversation_repository import (
    SqlAlchemyAgentConversationRepository,
)
from infrastructure.persistence.agent_handoff_repository import SqlAlchemyAgentHandoffRepository
from infrastructure.persistence.agent_pending_action_repository import (
    SqlAlchemyAgentPendingActionRepository,
)
from infrastructure.persistence.agent_preferences_repository import (
    SqlAlchemyAgentPreferencesRepository,
)
from infrastructure.persistence.behavior_review_repository import (
    SqlAlchemyBehaviorReviewRepository,
)
from infrastructure.persistence.broker_order_repository import (
    SqlAlchemyBrokerOrderRepository,
)
from infrastructure.persistence.catalyst_agenda_scope_reader import (
    SqlAlchemyCatalystAgendaScopeReader,
)
from infrastructure.persistence.catalyst_agenda_sync_repository import (
    SqlAlchemyCatalystAgendaSyncRepository,
)
from infrastructure.persistence.daily_equity_repository import SqlAlchemyDailyEquityRepository
from infrastructure.persistence.database import (
    SqlAlchemyDatabase,
    create_engine_from_url,
)
from infrastructure.persistence.external_note_repository import (
    SqlAlchemyExternalNoteRepository,
)
from infrastructure.persistence.industry_metric_repository import (
    SqlAlchemyIndustryMetricRepository,
)
from infrastructure.persistence.judgment_scorecard_repository import (
    SqlAlchemyJudgmentScorecardRepository,
)
from infrastructure.persistence.operational_job_repository import (
    SqlAlchemyOperationalJobRepository,
)
from infrastructure.persistence.post_market_sync_run_repository import (
    SqlAlchemyPostMarketSyncRunRepository,
)
from infrastructure.persistence.repositories.catalyst_agenda import (
    SqlAlchemyCatalystAgendaRepository,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository
from infrastructure.persistence.trade_cycle_override_repository import (
    SqlAlchemyTradeCycleOverrideRepository,
)
from infrastructure.persistence.trade_retro_repository import SqlAlchemyTradeRetroRepository
from infrastructure.persistence.watchlist_hub_unit_of_work import (
    SqlAlchemyWatchlistHubUnitOfWork,
)
from infrastructure.persistence.workflow_run_repository import (
    SqlAlchemyWorkflowRunRepository,
)
from infrastructure.providers.local_embedding import build_local_embedding_provider

ResearchUowFactory = Callable[[], ResearchUnitOfWork]
WatchlistUowFactory = Callable[[], SqlAlchemyWatchlistHubUnitOfWork]


@dataclass(frozen=True, slots=True)
class PersistenceInfrastructure:
    """Persistence ports and factories sharing one SQLAlchemy engine."""

    engine: Engine
    database: SqlAlchemyDatabase
    account_snapshots: AccountSnapshotRepository
    account_transactions: AccountTransactionRepository
    activity_annotations: ActivityAnnotationRepository
    trade_cycle_overrides: TradeCycleOverrideRepository
    behavior_reviews: BehaviorReviewRepository
    daily_equity: DailyEquityRepository
    journal_activation: JournalActivationRepository
    agent_conversations: AgentConversationRepository
    agent_handoffs: AgentHandoffRepository
    agent_pending_actions: AgentPendingActionRepository
    agent_preferences: AgentPreferencesRepository
    operational_jobs: OperationalJobRepository
    broker_orders: BrokerOrderRepository
    workflow_runs: WorkflowRunRepository
    historical_validation_artifacts: HistoricalValidationArtifactRepository
    industry_metrics: IndustryMetricRepository
    post_market_sync_runs: PostMarketSyncRunRepository
    trade_retro: TradeRetroRepository
    scorecards: JudgmentScorecardRepository
    catalyst_agenda: CatalystAgendaRepository
    catalyst_agenda_scope: CatalystAgendaScopeReader
    catalyst_agenda_sync: CatalystAgendaSyncRepository
    review_items: ReviewItemRepository
    external_notes: ExternalNoteRepository
    research_uow_factory: ResearchUowFactory
    watchlist_uow_factory: WatchlistUowFactory

    def search_backend_probe(self) -> bool:
        """Probe the search projection through an isolated unit of work."""

        with self.research_uow_factory() as uow:
            return uow.search_index.probe()


def build_persistence_infrastructure(
    settings: AppSettings,
    *,
    clock: Clock,
    id_generator: IdGenerator,
    secret_redactor: SecretRedactor,
) -> PersistenceInfrastructure:
    """Create one engine and every persistence adapter owned by the process."""

    engine = create_engine_from_url(settings.database_url)
    embedding_provider = build_local_embedding_provider(settings)

    def research_uow_factory() -> ResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(
            engine,
            clock,
            id_generator,
            secret_redactor,
            embedding_provider=embedding_provider,
            semantic_candidate_limit=settings.research_semantic_candidate_limit,
        )

    def watchlist_uow_factory() -> SqlAlchemyWatchlistHubUnitOfWork:
        return SqlAlchemyWatchlistHubUnitOfWork(
            engine,
            clock,
            id_generator,
            secret_redactor,
        )

    daily_equity = SqlAlchemyDailyEquityRepository(engine)
    return PersistenceInfrastructure(
        engine=engine,
        database=SqlAlchemyDatabase(engine),
        account_snapshots=SqlAlchemyAccountSnapshotRepository(engine),
        account_transactions=SqlAlchemyAccountTransactionRepository(engine),
        activity_annotations=SqlAlchemyActivityAnnotationRepository(engine),
        trade_cycle_overrides=SqlAlchemyTradeCycleOverrideRepository(engine),
        behavior_reviews=SqlAlchemyBehaviorReviewRepository(engine),
        daily_equity=daily_equity,
        journal_activation=daily_equity,
        agent_conversations=SqlAlchemyAgentConversationRepository(engine),
        agent_handoffs=SqlAlchemyAgentHandoffRepository(engine),
        agent_pending_actions=SqlAlchemyAgentPendingActionRepository(engine),
        agent_preferences=SqlAlchemyAgentPreferencesRepository(engine),
        operational_jobs=SqlAlchemyOperationalJobRepository(engine),
        broker_orders=SqlAlchemyBrokerOrderRepository(engine),
        workflow_runs=SqlAlchemyWorkflowRunRepository(engine),
        historical_validation_artifacts=FileHistoricalValidationArtifactRepository(
            settings.paths.historical_validation
        ),
        industry_metrics=SqlAlchemyIndustryMetricRepository(engine),
        post_market_sync_runs=SqlAlchemyPostMarketSyncRunRepository(engine),
        trade_retro=SqlAlchemyTradeRetroRepository(engine),
        scorecards=SqlAlchemyJudgmentScorecardRepository(engine),
        catalyst_agenda=SqlAlchemyCatalystAgendaRepository(engine),
        catalyst_agenda_scope=SqlAlchemyCatalystAgendaScopeReader(engine),
        catalyst_agenda_sync=SqlAlchemyCatalystAgendaSyncRepository(engine),
        review_items=SqlAlchemyReviewItemRepository(engine),
        external_notes=SqlAlchemyExternalNoteRepository(engine),
        research_uow_factory=research_uow_factory,
        watchlist_uow_factory=watchlist_uow_factory,
    )
