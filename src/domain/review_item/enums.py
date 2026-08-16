"""Closed ReviewItem vocabulary."""

from enum import StrEnum


class ReviewItemSourceType(StrEnum):
    CATALYST_AGENDA = "CATALYST_AGENDA"
    TRADE_RETRO = "TRADE_RETRO"
    SCORECARD_GAP = "SCORECARD_GAP"
    AGENT_PENDING_ACTION = "AGENT_PENDING_ACTION"
    BROKER_ORDER_INTENT = "BROKER_ORDER_INTENT"


class ReviewItemStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    AUTO_RESOLVED = "AUTO_RESOLVED"


class ReviewItemSeverity(StrEnum):
    INFO = "INFO"
    ATTENTION = "ATTENTION"
    ERROR = "ERROR"
