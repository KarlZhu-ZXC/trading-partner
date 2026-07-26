"""Closed enums for deterministic company peer comparison."""

from enum import StrEnum


class PeerComparisonPeriodMode(StrEnum):
    ANNUAL = "annual"
    LATEST_REPORTED = "latest_reported"


class PeerComparisonStatus(StrEnum):
    COMPARABLE = "COMPARABLE"
    PARTIAL = "PARTIAL"
    NOT_COMPARABLE = "NOT_COMPARABLE"
