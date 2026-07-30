"""Build the persistence adapters without importing application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.clock import Clock
from application.ports.historical_validation_artifact_repository import (
    HistoricalValidationArtifactRepository,
)
from application.ports.id_generator import IdGenerator
from application.ports.industry_metric_repository import IndustryMetricRepository
from application.ports.post_market_sync_run_repository import PostMarketSyncRunRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from application.ports.workflow_run_repository import WorkflowRunRepository
from infrastructure.artifacts.historical_validation import (
    FileHistoricalValidationArtifactRepository,
)
from infrastructure.config.settings import PROJECT_ROOT, AppSettings
from infrastructure.persistence.account_snapshot_repository import (
    SqlAlchemyAccountSnapshotRepository,
)
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.database import (
    SqlAlchemyDatabase,
    create_engine_from_url,
)
from infrastructure.persistence.industry_metric_repository import (
    SqlAlchemyIndustryMetricRepository,
)
from infrastructure.persistence.post_market_sync_run_repository import (
    SqlAlchemyPostMarketSyncRunRepository,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.persistence.watchlist_hub_unit_of_work import (
    SqlAlchemyWatchlistHubUnitOfWork,
)
from infrastructure.persistence.workflow_run_repository import (
    SqlAlchemyWorkflowRunRepository,
)

ResearchUowFactory = Callable[[], ResearchUnitOfWork]
WatchlistUowFactory = Callable[[], SqlAlchemyWatchlistHubUnitOfWork]


@dataclass(frozen=True, slots=True)
class PersistenceInfrastructure:
    """Persistence ports and factories sharing one SQLAlchemy engine."""

    engine: Engine
    database: SqlAlchemyDatabase
    account_snapshots: AccountSnapshotRepository
    account_transactions: AccountTransactionRepository
    workflow_runs: WorkflowRunRepository
    historical_validation_artifacts: HistoricalValidationArtifactRepository
    industry_metrics: IndustryMetricRepository
    post_market_sync_runs: PostMarketSyncRunRepository
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

    def research_uow_factory() -> ResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, id_generator, secret_redactor)

    def watchlist_uow_factory() -> SqlAlchemyWatchlistHubUnitOfWork:
        return SqlAlchemyWatchlistHubUnitOfWork(
            engine,
            clock,
            id_generator,
            secret_redactor,
        )

    return PersistenceInfrastructure(
        engine=engine,
        database=SqlAlchemyDatabase(engine),
        account_snapshots=SqlAlchemyAccountSnapshotRepository(engine),
        account_transactions=SqlAlchemyAccountTransactionRepository(engine),
        workflow_runs=SqlAlchemyWorkflowRunRepository(engine),
        historical_validation_artifacts=FileHistoricalValidationArtifactRepository(
            PROJECT_ROOT / "data" / "artifacts" / "historical_validation"
        ),
        industry_metrics=SqlAlchemyIndustryMetricRepository(engine),
        post_market_sync_runs=SqlAlchemyPostMarketSyncRunRepository(engine),
        research_uow_factory=research_uow_factory,
        watchlist_uow_factory=watchlist_uow_factory,
    )
