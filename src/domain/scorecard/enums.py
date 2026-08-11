"""Closed Judgment Scorecard vocabulary."""

from enum import StrEnum


class ScorecardStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NOT_EVALUATED = "NOT_EVALUATED"


class ScorecardDimensionStatus(StrEnum):
    EVALUATED = "EVALUATED"
    PARTIAL = "PARTIAL"
    NOT_EVALUATED = "NOT_EVALUATED"
