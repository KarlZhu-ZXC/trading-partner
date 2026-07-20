"""Terminal state for the scheduled US post-market synchronization job."""

from enum import StrEnum


class PostMarketSyncStepStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class PostMarketSyncRunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
