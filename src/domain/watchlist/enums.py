"""Watchlist hub domain enums (Phase 2)."""

from enum import StrEnum


class WatchlistSource(StrEnum):
    MOOMOO = "MOOMOO"
    MANUAL_CSV = "MANUAL_CSV"


class WatchlistGroupType(StrEnum):
    SYSTEM = "SYSTEM"
    CUSTOM = "CUSTOM"
    MANUAL = "MANUAL"


class WatchlistMutationAction(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"


class WatchlistMutationStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"

