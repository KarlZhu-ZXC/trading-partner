"""US market business enums (Phase 1F F1).

Wire-format values are the member ``value`` strings. Once exposed in Tool Schema
they must not change without a migration.
"""

from enum import StrEnum


class USBarInterval(StrEnum):
    """US OHLCV bar intervals (design §3 frozen wire values)."""

    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    SIXTY_MINUTES = "60m"
    ONE_DAY = "1d"
    ONE_WEEK = "1wk"
    ONE_MONTH = "1mo"
