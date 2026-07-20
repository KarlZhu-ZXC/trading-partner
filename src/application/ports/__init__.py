"""Application ports (protocols implemented by infrastructure)."""

from application.ports.assumption_repository import AssumptionRepository
from application.ports.audit_log_writer import AuditLogWriter
from application.ports.candidate_thesis_revision_repository import (
    CandidateThesisRevisionRepository,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.database import Database
from application.ports.id_generator import IdGenerator
from application.ports.instrument_repository import InstrumentRepository
from application.ports.instrument_unit_of_work import InstrumentUnitOfWork
from application.ports.invalidation_condition_repository import (
    InvalidationConditionRepository,
)
from application.ports.investment_case_repository import InvestmentCaseRepository
from application.ports.market_snapshot_category_provider import (
    MarketSnapshotCategoryProvider,
)
from application.ports.market_snapshot_provider import MarketSnapshotProvider
from application.ports.monitor_repository import MonitorRepository
from application.ports.open_question_repository import OpenQuestionRepository
from application.ports.provider_cache import ProviderCacheStore
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.provider_health_store import ProviderHealthStore
from application.ports.provider_rate_limit_store import ProviderRateLimitStore
from application.ports.provider_router_engine import ProviderRouterEnginePort
from application.ports.provider_router_settings import ProviderRouterSettings
from application.ports.reddit_state_store import RedditStateStore
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.risk_policy_repository import RiskPolicyRepository
from application.ports.secret_redactor import SecretRedactor
from application.ports.settings import AppSettingsView
from application.ports.technical_chart_renderer import TechnicalChartRenderer
from application.ports.technical_indicator_engine import TechnicalIndicatorEngine
from application.ports.thesis_repository import ThesisRepository
from application.ports.thesis_revision_repository import ThesisRevisionRepository
from application.ports.vendor_chain_config import VendorChainConfig
from application.ports.watchlist_group_repository import WatchlistGroupRepository
from application.ports.watchlist_hub_unit_of_work import WatchlistHubUnitOfWork
from application.ports.watchlist_membership_repository import (
    WatchlistMembershipRepository,
)
from application.ports.watchlist_mutation_repository import WatchlistMutationRepository
from application.ports.watchlist_repository import WatchlistRepository
from application.ports.watchlist_source_provider import WatchlistSourceProvider

__all__ = [
    "AppSettingsView",
    "AssumptionRepository",
    "AuditLogWriter",
    "CandidateThesisRevisionRepository",
    "CategoryProvider",
    "Clock",
    "Database",
    "IdGenerator",
    "InstrumentRepository",
    "InstrumentUnitOfWork",
    "WatchlistGroupRepository",
    "WatchlistHubUnitOfWork",
    "WatchlistMembershipRepository",
    "WatchlistMutationRepository",
    "InvalidationConditionRepository",
    "InvestmentCaseRepository",
    "MarketSnapshotCategoryProvider",
    "MarketSnapshotProvider",
    "MonitorRepository",
    "OpenQuestionRepository",
    "ProviderCacheCodec",
    "ProviderCacheStore",
    "ProviderHealthStore",
    "ProviderRateLimitStore",
    "ProviderRouterEnginePort",
    "ProviderRouterSettings",
    "RedditStateStore",
    "ResearchUnitOfWork",
    "RiskPolicyRepository",
    "SecretRedactor",
    "ThesisRepository",
    "ThesisRevisionRepository",
    "TechnicalChartRenderer",
    "TechnicalIndicatorEngine",
    "VendorChainConfig",
    "WatchlistRepository",
    "WatchlistSourceProvider",
]
