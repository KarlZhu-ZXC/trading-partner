"""Closed availability vocabulary for behavior metrics."""

from enum import StrEnum


class BehaviorMetricAvailability(StrEnum):
    """Whether a metric is a supported, computable fact in this version."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_SUPPORTED = "NOT_SUPPORTED"


# Compatibility-friendly aliases for application callers that use shorter
# names; the wire values remain the canonical enum values above.
BehaviorMetricStatus = BehaviorMetricAvailability
MetricAvailability = BehaviorMetricAvailability


__all__ = [
    "BehaviorMetricAvailability",
    "BehaviorMetricStatus",
    "MetricAvailability",
]
