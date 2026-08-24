"""Compatibility namespace for native-currency return facts."""

from domain.performance.daily_equity import (
    DailyEquityMaterializationReceipt,
    DailyEquityMaterializationResult,
    DailyEquityMaterializationWriteResult,
    DailyEquitySnapshot,
    JournalActivation,
)
from domain.performance.enums import (
    DailyEquityCoverageStatus,
    DailyEquityMaterializationMode,
    DailyEquityQualityStatus,
    PerformanceComputationStatus,
    PerformanceSeriesStatus,
    PerformanceStatus,
    ReturnComputationStatus,
)
from domain.performance.models import CyclePerformance, DailyEquityPoint, PerformanceSeries

__all__ = [
    "DailyEquityPoint",
    "CyclePerformance",
    "DailyEquityCoverageStatus",
    "DailyEquityMaterializationMode",
    "DailyEquityMaterializationReceipt",
    "DailyEquityMaterializationResult",
    "DailyEquityMaterializationWriteResult",
    "DailyEquityQualityStatus",
    "DailyEquitySnapshot",
    "PerformanceComputationStatus",
    "PerformanceSeriesStatus",
    "PerformanceSeries",
    "PerformanceStatus",
    "ReturnComputationStatus",
    "JournalActivation",
]
