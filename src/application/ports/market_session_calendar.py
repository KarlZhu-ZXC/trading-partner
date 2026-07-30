"""US market-session calendar boundary for scheduled operational jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MarketSession:
    session_date: date
    close_at: datetime


class MarketSessionCalendar(Protocol):
    def session_at(self, moment: datetime) -> MarketSession | None: ...

    def session_on_or_before(self, moment: datetime) -> MarketSession | None: ...

    def previous_session(self, session_date: date) -> MarketSession | None: ...

    def next_session(self, session_date: date) -> MarketSession | None: ...
