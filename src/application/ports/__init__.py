"""Application ports (protocols implemented by infrastructure)."""

from application.ports.activity_annotation_repository import ActivityAnnotationRepository
from application.ports.agent_attachment_store import AgentAttachmentStore
from application.ports.assumption_repository import AssumptionRepository
from application.ports.audit_log_writer import AuditLogWriter
from application.ports.behavior_review_repository import BehaviorReviewRepository
from application.ports.candidate_thesis_revision_repository import (
    CandidateThesisRevisionRepository,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.daily_equity_repository import (
    DailyEquityRepository,
    DailyEquitySnapshotRepository,
    JournalActivationRepository,
)
from application.ports.database import Database
from application.ports.id_generator import IdGenerator
from application.ports.instrument_repository import InstrumentRepository
from application.ports.instrument_unit_of_work import InstrumentUnitOfWork
from application.ports.invalidation_condition_repository import (
    InvalidationConditionRepository,
)
from application.ports.monitor_repository import MonitorRepository
from application.ports.open_question_repository import OpenQuestionRepository
from application.ports.provider_cache import ProviderCacheStore
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.provider_health_store import ProviderHealthStore
from application.ports.provider_rate_limit_store import ProviderRateLimitStore
from application.ports.provider_router_engine import ProviderRouterEnginePort
from application.ports.provider_router_settings import ProviderRouterSettings
from application.ports.reddit_state_store import RedditStateStore
from application.ports.research_subject_repository import ResearchSubjectRepository
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.risk_policy_repository import RiskPolicyRepository
from application.ports.secret_redactor import SecretRedactor
from application.ports.settings import AppSettingsView
from application.ports.technical_chart_renderer import TechnicalChartRenderer
from application.ports.technical_indicator_engine import TechnicalIndicatorEngine
from application.ports.thesis_repository import ThesisRepository
from application.ports.thesis_revision_repository import ThesisRevisionRepository
from application.ports.trade_cycle_override_repository import TradeCycleOverrideRepository
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
    "AgentAttachmentStore",
    "ActivityAnnotationRepository",
    "TradeCycleOverrideRepository",
    "BehaviorReviewRepository",
    "AssumptionRepository",
    "AuditLogWriter",
    "CandidateThesisRevisionRepository",
    "CategoryProvider",
    "Clock",
    "Database",
    "DailyEquityRepository",
    "DailyEquitySnapshotRepository",
    "JournalActivationRepository",
    "IdGenerator",
    "InstrumentRepository",
    "InstrumentUnitOfWork",
    "WatchlistGroupRepository",
    "WatchlistHubUnitOfWork",
    "WatchlistMembershipRepository",
    "WatchlistMutationRepository",
    "InvalidationConditionRepository",
    "ResearchSubjectRepository",
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
