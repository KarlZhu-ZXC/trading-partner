"""Frozen Challenge Review wire enums."""

from enum import StrEnum


class ChallengeTrigger(StrEnum):
    DISCUSSION = "discussion"
    THESIS_ACTIVATION = "thesis_activation"
    CONFIDENCE_INCREASE = "confidence_increase"
    INVALIDATION_RELAXATION = "invalidation_relaxation"
    POSITION_INTENT = "position_intent"
    CONTRARY_EVIDENCE = "contrary_evidence"
    POSITION_THESIS_CONFLICT = "position_thesis_conflict"
    STALE_REVIEW = "stale_review"
    CONFIDENCE_WITHOUT_EVIDENCE = "confidence_without_evidence"


class ChallengeDimension(StrEnum):
    FALSIFIABILITY = "falsifiability"
    EVIDENCE_QUALITY = "evidence_quality"
    HIDDEN_ASSUMPTIONS = "hidden_assumptions"
    CONTRARY_EVIDENCE = "contrary_evidence"
    VALUATION_EXPECTATIONS = "valuation_expectations"
    OPPORTUNITY_COST = "opportunity_cost"
    PORTFOLIO_BIAS = "portfolio_bias"
    TIME_HORIZON_CONSISTENCY = "time_horizon_consistency"
    MOVING_THE_GOALPOSTS_RISK = "moving_the_goalposts_risk"
    MISSING_INFORMATION = "missing_information"


class ChallengeFindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ChallengeReviewStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class ChallengeResolution(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    REJECT = "reject"
    DEFER = "defer"
