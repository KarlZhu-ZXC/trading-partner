"""Persisted Catalyst Agenda enums."""

from enum import StrEnum


class AgendaItemKind(StrEnum):
    EARNINGS = "EARNINGS"
    FILING = "FILING"
    DIVIDEND = "DIVIDEND"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    INVESTOR_EVENT = "INVESTOR_EVENT"
    MACRO_RELEASE = "MACRO_RELEASE"
    POLICY = "POLICY"
    INDUSTRY = "INDUSTRY"
    USER_DEFINED = "USER_DEFINED"


class AgendaDateCertainty(StrEnum):
    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class AgendaItemStatus(StrEnum):
    UPCOMING = "UPCOMING"
    OCCURRED = "OCCURRED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class AgendaScopeReason(StrEnum):
    GLOBAL = "GLOBAL"
    PORTFOLIO = "PORTFOLIO"
    WATCHLIST = "WATCHLIST"
    SUBJECT = "SUBJECT"
    EXPLICIT = "EXPLICIT"


class AgendaSourceType(StrEnum):
    USER_CONFIRMED = "USER_CONFIRMED"
    PROVIDER = "PROVIDER"


class AgendaSyncProviderStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AgendaSyncStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
