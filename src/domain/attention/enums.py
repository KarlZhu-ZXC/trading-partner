"""Closed Attention query vocabulary. Not the ReviewItem wire ABI."""

from enum import StrEnum

ATTENTION_DEFAULT_LIMIT = 25
ATTENTION_MAX_LIMIT = 100


class AttentionSourceType(StrEnum):
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"
    CATALYST_AGENDA = "CATALYST_AGENDA"
    TRADE_RETRO = "TRADE_RETRO"
    SCORECARD_GAP = "SCORECARD_GAP"
    MONITOR_BLIND_SPOT = "MONITOR_BLIND_SPOT"
    BROKER_ORDER_INTENT = "BROKER_ORDER_INTENT"
    AGENT_PENDING_ACTION = "AGENT_PENDING_ACTION"
    DATA_QUALITY = "DATA_QUALITY"


class AttentionSeverity(StrEnum):
    INFO = "INFO"
    ATTENTION = "ATTENTION"
    ERROR = "ERROR"


class AttentionStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class AttentionTrackingKind(StrEnum):
    REVIEW_ITEM = "REVIEW_ITEM"
    LIVE_PROJECTION = "LIVE_PROJECTION"


class AttentionScope(StrEnum):
    GLOBAL = "global"
    SUBJECT = "subject"


class AttentionCoverageState(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class AttentionCoverageSource(StrEnum):
    REVIEW_ITEMS = "review_items"
    RESEARCH_CANDIDATES = "research_candidates"
    CATALYST_AGENDA = "catalyst_agenda"
    TRADE_RETRO = "trade_retro"
    SCORECARD = "scorecard"
    MONITORS = "monitors"
    BROKER_ORDERS = "broker_orders"
    AGENT_PENDING_ACTIONS = "agent_pending_actions"
    DATA_QUALITY = "data_quality"


class AttentionClosureCode(StrEnum):
    CANDIDATE_RESOLVED = "CANDIDATE_RESOLVED"
    AGENDA_OUTCOME_CLOSED = "AGENDA_OUTCOME_CLOSED"
    RETRO_STATE_SATISFIED = "RETRO_STATE_SATISFIED"
    SCORECARD_GAP_CLEARED = "SCORECARD_GAP_CLEARED"
    MONITOR_COVERAGE_RESTORED = "MONITOR_COVERAGE_RESTORED"
    BROKER_OBSERVATION_RECONCILED = "BROKER_OBSERVATION_RECONCILED"
    AGENT_ACTION_TERMINAL = "AGENT_ACTION_TERMINAL"
    DATA_QUALITY_ISSUE_CLEARED = "DATA_QUALITY_ISSUE_CLEARED"


READ_ONLY_NEXT_READ_TOOLS = frozenset(
    {
        "system_health",
        "investment_case_read",
        "research_judgment_get",
        "research_memory_get",
        "monitor_read",
        "broker_order_manage",
        "portfolio_analyze",
    }
)

FORBIDDEN_NEXT_READ_OPERATIONS = frozenset(
    {
        "submit",
        "cancel",
        "preview",
        "cash_sweep_preview",
        "evaluate",
        "create",
        "update",
        "archive",
        "add",
        "remove",
        "accounts",
        "transactions",
        "watchlist",
    }
)
