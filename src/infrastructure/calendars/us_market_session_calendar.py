"""XNYS calendar adapter with holiday, early-close, and DST support."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import exchange_calendars

from application.ports.market_session_calendar import MarketSession
from domain.common.time import require_aware_datetime

_NEW_YORK = ZoneInfo("America/New_York")


class XnysMarketSessionCalendar:
    def __init__(self) -> None:
        self._calendar = exchange_calendars.get_calendar("XNYS")

    def session_at(self, moment: datetime) -> MarketSession | None:
        require_aware_datetime(moment, field_name="moment")
        session_date = moment.astimezone(_NEW_YORK).date()
        if not self._calendar.is_session(session_date):
            return None
        close_at = self._calendar.session_close(session_date).to_pydatetime().astimezone(UTC)
        return MarketSession(session_date=session_date, close_at=close_at)

    def session_on_or_before(self, moment: datetime) -> MarketSession | None:
        require_aware_datetime(moment, field_name="moment")
        candidate = moment.astimezone(_NEW_YORK).date()
        label = self._calendar.date_to_session(candidate, direction="previous")
        return self._session(date.fromisoformat(str(label.date())))

    def previous_session(self, session_date: date) -> MarketSession | None:
        label = self._calendar.previous_session(session_date)
        return self._session(date.fromisoformat(str(label.date())))

    def _session(self, session_date: date) -> MarketSession:
        close_at = self._calendar.session_close(session_date).to_pydatetime().astimezone(UTC)
        return MarketSession(session_date=session_date, close_at=close_at)
