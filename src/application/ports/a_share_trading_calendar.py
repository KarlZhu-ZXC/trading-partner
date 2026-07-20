"""A-share trading calendar port (Phase 1E §19)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from domain.a_share.models import TradingSessionWindow


class AShareTradingCalendar(Protocol):
    """Versioned exchange calendar; never degrades to weekday-only rules."""

    @property
    def version(self) -> str:
        """Calendar content version identifier (e.g. fixture schema version)."""
        ...

    def is_trading_day(self, day: date) -> bool:
        """Return whether ``day`` is an open trading day.

        Raises:
            CalendarOutOfRange: when ``day`` is outside coverage_from/to.
        """
        ...

    def previous_trading_day(self, day: date) -> date:
        """Return the latest open trading day strictly before ``day``.

        Raises:
            CalendarOutOfRange: when ``day`` is outside coverage_from/to, or
            when no previous open day exists within coverage.
        """
        ...

    def sessions_for(self, day: date) -> tuple[TradingSessionWindow, ...]:
        """Return session windows for ``day`` (empty if not a trading day).

        Raises:
            CalendarOutOfRange: when ``day`` is outside coverage.
        """
        ...
