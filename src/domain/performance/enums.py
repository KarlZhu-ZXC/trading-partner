"""Closed enums for deterministic account performance calculations."""

from enum import StrEnum


class PerformanceStatus(StrEnum):
    """Coverage status for a performance series.

    ``INCOMPLETE`` is intentionally used when any metric has to be withheld.
    A metric-level ``NOT_COMPUTABLE`` status is carried separately by the
    series for algorithms (notably XIRR) that can be unavailable while the
    source valuation points remain readable.
    """

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"


class PerformanceComputationStatus(StrEnum):
    """Status of an individual return calculation."""

    COMPUTED = "COMPUTED"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"
    UNAVAILABLE = "UNAVAILABLE"


class DailyEquityCoverageStatus(StrEnum):
    """Coverage/quality state for one durable equity projection."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INCOMPLETE = "INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"


class DailyEquityMaterializationMode(StrEnum):
    """Whether a materialization run writes the rebuildable projection."""

    SHADOW = "SHADOW"
    DRY_RUN = "DRY_RUN"
    PERSIST = "PERSIST"


# Readability aliases for callers that use the return-oriented terminology.
PerformanceSeriesStatus = PerformanceStatus
ReturnComputationStatus = PerformanceComputationStatus
DailyEquityQualityStatus = DailyEquityCoverageStatus
