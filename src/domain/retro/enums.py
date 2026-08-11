"""Closed Trade Retro enum vocabulary."""

from enum import StrEnum


class TradeRetroStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class TradeRetroSeverity(StrEnum):
    INFO = "INFO"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TradeRetroReviewStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    DISPUTED = "DISPUTED"
    RESOLVED = "RESOLVED"


class TradeRetroFindingReviewStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DISPUTED = "DISPUTED"
    RESOLVED = "RESOLVED"
