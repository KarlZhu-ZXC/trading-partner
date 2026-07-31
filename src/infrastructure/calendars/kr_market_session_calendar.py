"""XKRX calendar adapter with Korean holidays and exchange sessions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import exchange_calendars

from application.ports.market_session_calendar import MarketSession
from domain.common.time import require_aware_datetime

_SEOUL = ZoneInfo("Asia/Seoul")


class XkrxMarketSessionCalendar:
    def __init__(self) -> None:
        self._calendar = exchange_calendars.get_calendar("XKRX")

    def session_at(self, moment: datetime) -> MarketSession | None:
        require_aware_datetime(moment, field_name="moment")
        session_date = moment.astimezone(_SEOUL).date()
        if not self._calendar.is_session(session_date):
            return None
        return self._session(session_date)

    def session_on_or_before(self, moment: datetime) -> MarketSession | None:
        require_aware_datetime(moment, field_name="moment")
        candidate = moment.astimezone(_SEOUL).date()
        label = self._calendar.date_to_session(candidate, direction="previous")
        return self._session(date.fromisoformat(str(label.date())))

    def previous_session(self, session_date: date) -> MarketSession | None:
        label = self._calendar.previous_session(session_date)
        return self._session(date.fromisoformat(str(label.date())))

    def next_session(self, session_date: date) -> MarketSession | None:
        label = self._calendar.next_session(session_date)
        return self._session(date.fromisoformat(str(label.date())))

    def _session(self, session_date: date) -> MarketSession:
        close_at = self._calendar.session_close(session_date).to_pydatetime().astimezone(UTC)
        return MarketSession(session_date=session_date, close_at=close_at)
