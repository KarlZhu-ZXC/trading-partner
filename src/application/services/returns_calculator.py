"""Compatibility import for the Phase 4C performance calculator."""

from application.services.performance_calculator import (
    PerformanceCalculator,
    PerformanceReturnsCalculator,
    PerformanceSeriesCalculator,
)

__all__ = [
    "PerformanceCalculator",
    "PerformanceReturnsCalculator",
    "PerformanceSeriesCalculator",
]
