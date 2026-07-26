"""Closed Trade Plan wire/domain enums."""

from enum import StrEnum


class TradePlanStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class TradePlanConditionPhase(StrEnum):
    ENTRY = "ENTRY"
    SCALE = "SCALE"
    EXIT = "EXIT"
    INVALIDATION = "INVALIDATION"
    REVIEW = "REVIEW"


class TradePlanConditionMode(StrEnum):
    MANUAL = "MANUAL"
    MONITORABLE = "MONITORABLE"


class TradePlanFactType(StrEnum):
    PRICE = "PRICE"
    VOLUME = "VOLUME"
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    COMPANY_EVENT = "COMPANY_EVENT"
    MACRO = "MACRO"
    SENTIMENT = "SENTIMENT"
    THESIS_STATE = "THESIS_STATE"
    PORTFOLIO_RISK = "PORTFOLIO_RISK"


class TradePlanComparator(StrEnum):
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    EQ = "EQ"
    OCCURRED = "OCCURRED"
