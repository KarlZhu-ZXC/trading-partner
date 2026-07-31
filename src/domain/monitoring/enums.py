"""Closed Phase 2C Monitoring enum vocabulary."""

from enum import StrEnum


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


class MonitorEventAction(StrEnum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    RESOLVE = "RESOLVE"


class MonitorRunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class MonitorNotificationChannel(StrEnum):
    TELEGRAM = "TELEGRAM"


class MonitorNotificationStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"
    EXPIRED = "EXPIRED"
