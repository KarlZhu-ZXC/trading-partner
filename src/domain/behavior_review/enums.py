"""Closed period and action-state vocabularies for behavior reviews."""

from enum import StrEnum


class BehaviorReviewPeriodKind(StrEnum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"


class BehaviorReviewRunStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"


class BehaviorActionStatus(StrEnum):
    NEW = "NEW"
    PERSISTENT = "PERSISTENT"
    RESOLVED = "RESOLVED"
    RECURRED = "RECURRED"


# Short compatibility names keep the domain vocabulary discoverable without
# adding a second status family.
BehaviorReviewStatus = BehaviorReviewRunStatus
ActionRecurrenceStatus = BehaviorActionStatus


__all__ = [
    "ActionRecurrenceStatus",
    "BehaviorActionStatus",
    "BehaviorReviewPeriodKind",
    "BehaviorReviewRunStatus",
    "BehaviorReviewStatus",
]
