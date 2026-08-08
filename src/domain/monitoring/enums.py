"""Closed Phase 2C Monitoring enum vocabulary."""

from enum import StrEnum

from domain.notifications.enums import (
    NotificationChannel,
    NotificationStatus,
)


class MonitorStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class MonitorCadence(StrEnum):
    ON_DEMAND = "ON_DEMAND"
    INTERVAL = "INTERVAL"
    A_SHARE_POST_MARKET = "A_SHARE_POST_MARKET"
    US_POST_MARKET = "US_POST_MARKET"
    KR_POST_MARKET = "KR_POST_MARKET"


class MonitorRuleType(StrEnum):
    PRICE_ABOVE = "PRICE_ABOVE"
    PRICE_BELOW = "PRICE_BELOW"
    RISK_OVERALL_AT_LEAST = "RISK_OVERALL_AT_LEAST"
    FACT_COMPARISON = "FACT_COMPARISON"


class MonitorSeverity(StrEnum):
    INFO = "INFO"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class MonitorRuleStateValue(StrEnum):
    QUIET = "QUIET"
    TRIGGERED = "TRIGGERED"
    NOT_EVALUATED = "NOT_EVALUATED"


class MonitorEventType(StrEnum):
    TRIGGERED = "TRIGGERED"
    RECOVERED = "RECOVERED"
    NOT_EVALUATED = "NOT_EVALUATED"
    JUDGMENT_CHANGED = "JUDGMENT_CHANGED"
    JUDGMENT_UNAVAILABLE = "JUDGMENT_UNAVAILABLE"


class MonitorJudgmentConclusion(StrEnum):
    WATCH = "WATCH"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    WAIT = "WAIT"
    PREPARE_TO_BUY = "PREPARE_TO_BUY"
    BUY_SMALL = "BUY_SMALL"
    BUY = "BUY"
    BUY_AGGRESSIVELY = "BUY_AGGRESSIVELY"
    PAUSE_BUYING = "PAUSE_BUYING"
    INVALIDATE = "INVALIDATE"


class MonitorEventAction(StrEnum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    RESOLVE = "RESOLVE"


class MonitorRunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


# Compatibility aliases for callers written before the generic outbox. New
# application code should import from ``domain.notifications``.
MonitorNotificationChannel = NotificationChannel
MonitorNotificationStatus = NotificationStatus
