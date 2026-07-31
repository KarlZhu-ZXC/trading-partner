"""Closed performance-attribution enums."""

from enum import StrEnum


class AttributionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class CostBasisMethod(StrEnum):
    FIFO = "FIFO"
    BROKER_REPORTED = "BROKER_REPORTED"


class LotDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

