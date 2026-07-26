"""Frozen workflow recipe and terminal status enums."""

from enum import StrEnum


class WorkflowType(StrEnum):
    DEEP_DIVE = "deep_dive"
    CATALYST_REVIEW = "catalyst_review"
    A_SHARE_MARKET_REVIEW = "a_share_market_review"
    US_MARKET_REVIEW = "us_market_review"
    PORTFOLIO_REVIEW = "portfolio_review"
    PEER_COMPARISON = "peer_comparison"


class WorkflowRunStatus(StrEnum):
    STARTED = "started"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
