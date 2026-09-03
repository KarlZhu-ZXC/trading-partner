"""Typed domain and application boundary errors.

Error messages must never contain API keys, tokens, secrets, full database
credentials, or provider Authorization headers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar


class TradingPartnerError(Exception):
    """Base error for all Trading Partner failures."""

    default_code: ClassVar[str] = "TRADING_PARTNER_ERROR"
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
        retryable: bool | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message
        self.details: dict[str, object] = dict(details or {})
        self.retryable = self.default_retryable if retryable is None else retryable
        self.code = self.default_code if code is None else code
        super().__init__(message)


class ConfigurationError(TradingPartnerError):
    default_code = "CONFIGURATION_ERROR"
    default_retryable = False


class ProviderNotConfigured(TradingPartnerError):
    default_code = "PROVIDER_NOT_CONFIGURED"
    default_retryable = False


class ProviderAuthenticationError(TradingPartnerError):
    default_code = "PROVIDER_AUTHENTICATION_ERROR"
    default_retryable = False


class ProviderRateLimitError(TradingPartnerError):
    default_code = "PROVIDER_RATE_LIMIT_ERROR"
    default_retryable = True


class ProviderQuotaExceededError(TradingPartnerError):
    """Provider plan or pay-as-you-go allowance is exhausted."""

    default_code = "PROVIDER_QUOTA_EXCEEDED"
    default_retryable = False


class ProviderAdmissionTimeoutError(TradingPartnerError):
    """Local admission queue budget expired before a provider slot was free."""

    default_code = "PROVIDER_ADMISSION_TIMEOUT"
    default_retryable = True


class ProviderTimeoutError(TradingPartnerError):
    default_code = "PROVIDER_TIMEOUT_ERROR"
    default_retryable = True


class ProviderUnavailableError(TradingPartnerError):
    default_code = "PROVIDER_UNAVAILABLE_ERROR"
    default_retryable = True


class NoMarketData(TradingPartnerError):
    default_code = "NO_MARKET_DATA"
    default_retryable = False


class StaleMarketData(TradingPartnerError):
    default_code = "STALE_MARKET_DATA"
    default_retryable = True


class InvalidInstrument(TradingPartnerError):
    default_code = "INVALID_INSTRUMENT"
    default_retryable = False


class DataContractError(TradingPartnerError):
    default_code = "DATA_CONTRACT_ERROR"
    default_retryable = False


class ProviderRequestRejectedError(DataContractError):
    """Provider understood the request but rejected its supported shape/policy."""

    default_code = "PROVIDER_REQUEST_REJECTED"


class CalendarOutOfRange(TradingPartnerError):
    """Requested date falls outside the loaded A-share trading calendar coverage."""

    default_code = "CALENDAR_OUT_OF_RANGE"
    default_retryable = False


class PartialDataError(TradingPartnerError):
    default_code = "PARTIAL_DATA_ERROR"
    default_retryable = True


class PersistenceError(TradingPartnerError):
    default_code = "PERSISTENCE_ERROR"
    default_retryable = True


class MigrationError(TradingPartnerError):
    default_code = "MIGRATION_ERROR"
    default_retryable = False


class AccountSnapshotNotFound(TradingPartnerError):
    default_code = "ACCOUNT_SNAPSHOT_NOT_FOUND"
    default_retryable = False


class PortfolioSnapshotNotFound(TradingPartnerError):
    default_code = "PORTFOLIO_SNAPSHOT_NOT_FOUND"
    default_retryable = False


class RiskPolicyNotFound(TradingPartnerError):
    default_code = "RISK_POLICY_NOT_FOUND"
    default_retryable = False


class RiskPolicyVersionConflict(TradingPartnerError):
    default_code = "RISK_POLICY_VERSION_CONFLICT"
    default_retryable = False


class IdempotencyConflict(TradingPartnerError):
    default_code = "IDEMPOTENCY_CONFLICT"
    default_retryable = False


class AgentPreferencesVersionConflict(TradingPartnerError):
    default_code = "AGENT_PREFERENCES_VERSION_CONFLICT"
    default_retryable = False


class ConfirmerMismatch(TradingPartnerError):
    default_code = "CONFIRMER_MISMATCH"
    default_retryable = False


class BrokerOrderNotFound(TradingPartnerError):
    default_code = "BROKER_ORDER_NOT_FOUND"
    default_retryable = False


class BrokerOrderStateConflict(TradingPartnerError):
    default_code = "BROKER_ORDER_STATE_CONFLICT"
    default_retryable = False


class BrokerOrderPreviewExpired(TradingPartnerError):
    default_code = "BROKER_ORDER_PREVIEW_EXPIRED"
    default_retryable = False


class BrokerOrderRejected(TradingPartnerError):
    default_code = "BROKER_ORDER_REJECTED"
    default_retryable = False


class BrokerOrderSubmissionUncertain(TradingPartnerError):
    """A write may have reached Schwab; callers must inspect, never retry blindly."""

    default_code = "BROKER_ORDER_SUBMISSION_UNCERTAIN"
    default_retryable = False


class MonitorNotFound(TradingPartnerError):
    default_code = "MONITOR_NOT_FOUND"
    default_retryable = False


class MonitorVersionConflict(TradingPartnerError):
    default_code = "MONITOR_VERSION_CONFLICT"
    default_retryable = False


class MonitorEventNotFound(TradingPartnerError):
    default_code = "MONITOR_EVENT_NOT_FOUND"
    default_retryable = False


class TradePlanNotFound(TradingPartnerError):
    default_code = "TRADE_PLAN_NOT_FOUND"
    default_retryable = False


class TradePlanVersionConflict(TradingPartnerError):
    default_code = "TRADE_PLAN_VERSION_CONFLICT"
    default_retryable = False


class ChallengeReviewNotFound(TradingPartnerError):
    default_code = "CHALLENGE_REVIEW_NOT_FOUND"
    default_retryable = False


class ChallengeReviewAlreadyResolved(TradingPartnerError):
    default_code = "CHALLENGE_REVIEW_ALREADY_RESOLVED"
    default_retryable = False


class WorkflowRunNotFound(TradingPartnerError):
    default_code = "WORKFLOW_RUN_NOT_FOUND"
    default_retryable = False


class WorkflowRunInProgress(TradingPartnerError):
    default_code = "WORKFLOW_RUN_IN_PROGRESS"
    default_retryable = True


class TradeRetroRunNotFound(TradingPartnerError):
    default_code = "TRADE_RETRO_RUN_NOT_FOUND"
    default_retryable = False


class TradeRetroReviewVersionConflict(TradingPartnerError):
    default_code = "TRADE_RETRO_REVIEW_VERSION_CONFLICT"
    default_retryable = False


class JudgmentScorecardNotFound(TradingPartnerError):
    default_code = "JUDGMENT_SCORECARD_NOT_FOUND"
    default_retryable = False


class ReviewItemNotFound(TradingPartnerError):
    default_code = "REVIEW_ITEM_NOT_FOUND"
    default_retryable = False


class ReviewItemVersionConflict(TradingPartnerError):
    default_code = "REVIEW_ITEM_VERSION_CONFLICT"
    default_retryable = False


class ExternalNoteReviewNotFound(TradingPartnerError):
    default_code = "EXTERNAL_NOTE_REVIEW_NOT_FOUND"
    default_retryable = False


class ExternalNoteReviewVersionConflict(TradingPartnerError):
    default_code = "EXTERNAL_NOTE_REVIEW_VERSION_CONFLICT"
    default_retryable = False


class InvalidExternalNoteReviewTransition(TradingPartnerError):
    default_code = "INVALID_EXTERNAL_NOTE_REVIEW_TRANSITION"
    default_retryable = False


class ActivityAnnotationNotFound(TradingPartnerError):
    default_code = "ACTIVITY_ANNOTATION_NOT_FOUND"
    default_retryable = False


class ActivityAnnotationVersionConflict(TradingPartnerError):
    default_code = "ACTIVITY_ANNOTATION_VERSION_CONFLICT"
    default_retryable = False


class TradeCycleOverrideNotFound(TradingPartnerError):
    default_code = "TRADE_CYCLE_OVERRIDE_NOT_FOUND"
    default_retryable = False


class TradeCycleOverrideVersionConflict(TradingPartnerError):
    default_code = "TRADE_CYCLE_OVERRIDE_VERSION_CONFLICT"
    default_retryable = False


class InvalidTradeCycleOverride(TradingPartnerError):
    default_code = "INVALID_TRADE_CYCLE_OVERRIDE"
    default_retryable = False


class CatalystAgendaNotFound(TradingPartnerError):
    default_code = "CATALYST_AGENDA_NOT_FOUND"
    default_retryable = False


class CatalystAgendaVersionConflict(TradingPartnerError):
    default_code = "CATALYST_AGENDA_VERSION_CONFLICT"
    default_retryable = False


# --- Phase 1B research-state errors (default_retryable=False; no silent retry) ---


class ResearchSubjectNotFound(TradingPartnerError):
    # Published machine-code ABI predates the Research Subject terminology.
    default_code = "INVESTMENT_CASE_NOT_FOUND"
    default_retryable = False


class ThesisNotFound(TradingPartnerError):
    default_code = "THESIS_NOT_FOUND"
    default_retryable = False


class ThesisRevisionNotFound(TradingPartnerError):
    default_code = "THESIS_REVISION_NOT_FOUND"
    default_retryable = False


class ResearchStateConflict(TradingPartnerError):
    """Raised when a research-state entity transition violates a cross-entity invariant."""

    default_code = "RESEARCH_STATE_CONFLICT"
    default_retryable = False


class WatchlistItemNotFound(TradingPartnerError):
    default_code = "WATCHLIST_ITEM_NOT_FOUND"
    default_retryable = False


class WatchlistGroupNotFound(TradingPartnerError):
    default_code = "WATCHLIST_GROUP_NOT_FOUND"
    default_retryable = False


class WatchlistMembershipNotFound(TradingPartnerError):
    default_code = "WATCHLIST_MEMBERSHIP_NOT_FOUND"
    default_retryable = False


class WatchlistMutationNotFound(TradingPartnerError):
    default_code = "WATCHLIST_MUTATION_NOT_FOUND"
    default_retryable = False


class OpenQuestionNotFound(TradingPartnerError):
    default_code = "OPEN_QUESTION_NOT_FOUND"
    default_retryable = False


class CandidateNotFound(TradingPartnerError):
    default_code = "CANDIDATE_NOT_FOUND"
    default_retryable = False


class CandidateAlreadyResolved(TradingPartnerError):
    default_code = "CANDIDATE_ALREADY_RESOLVED"
    default_retryable = False


class UnauthorizedReviewer(TradingPartnerError):
    default_code = "UNAUTHORIZED_REVIEWER"
    default_retryable = False


class InvalidationConditionNarrowingForbidden(TradingPartnerError):
    default_code = "INVALIDATION_CONDITION_NARROWING_FORBIDDEN"
    default_retryable = False


class StrictReviewRequired(TradingPartnerError):
    default_code = "STRICT_REVIEW_REQUIRED"
    default_retryable = False


class DuplicateIdempotencyKey(TradingPartnerError):
    default_code = "DUPLICATE_IDEMPOTENCY_KEY"
    default_retryable = False


class InputValidationError(TradingPartnerError):
    default_code = "INPUT_VALIDATION_ERROR"
    default_retryable = False


class SubjectMetadataPolicyViolation(TradingPartnerError):
    """Raised when Subject metadata is written as an action plan rather than research scope."""

    # Published machine-code ABI predates the Research Subject terminology.
    default_code = "CASE_METADATA_POLICY_VIOLATION"
    default_retryable = False


class AppendOnlyViolation(TradingPartnerError):
    """Raised when an append-only table is mutated via ORM update/delete."""

    default_code = "APPEND_ONLY_VIOLATION"
    default_retryable = False


# --- Phase 1C research-memory errors (default_retryable=False except search backend) ---


class ResearchMemoryNotFound(TradingPartnerError):
    default_code = "RESEARCH_MEMORY_NOT_FOUND"
    default_retryable = False


class ImmutableResearchRecord(TradingPartnerError):
    default_code = "IMMUTABLE_RESEARCH_RECORD"
    default_retryable = False


class InvalidResearchLink(TradingPartnerError):
    default_code = "INVALID_RESEARCH_LINK"
    default_retryable = False


class SearchBackendUnavailable(TradingPartnerError):
    default_code = "SEARCH_BACKEND_UNAVAILABLE"
    default_retryable = True


class HistoricalVisibilityViolation(TradingPartnerError):
    default_code = "HISTORICAL_VISIBILITY_VIOLATION"
    default_retryable = False


class UnauthorizedResearchWrite(TradingPartnerError):
    default_code = "UNAUTHORIZED_RESEARCH_WRITE"
    default_retryable = False
