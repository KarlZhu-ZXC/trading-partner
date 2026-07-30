"""Adapt the versioned A-share calendar to the Monitor schedule boundary."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from application.ports.a_share_trading_calendar import AShareTradingCalendar
from application.ports.market_session_calendar import MarketSession

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_NEXT_SESSION_SEARCH_DAYS = 31


class AShareMarketSessionCalendarAdapter:
    def __init__(self, calendar: AShareTradingCalendar) -> None:
        self._calendar = calendar

    def session_at(self, moment: datetime) -> MarketSession | None:
        return self._session(moment.astimezone(_SHANGHAI).date())

    def session_on_or_before(self, moment: datetime) -> MarketSession | None:
        day = moment.astimezone(_SHANGHAI).date()
        current = self._session(day)
        if current is not None:
            return current
        return self._session(self._calendar.previous_trading_day(day))

    def previous_session(self, session_date: date) -> MarketSession | None:
        return self._session(self._calendar.previous_trading_day(session_date))

    def next_session(self, session_date: date) -> MarketSession | None:
        for offset in range(1, _NEXT_SESSION_SEARCH_DAYS + 1):
            candidate = session_date + timedelta(days=offset)
            if self._calendar.is_trading_day(candidate):
                return self._session(candidate)
        return None

    def _session(self, day: date) -> MarketSession | None:
        if not self._calendar.is_trading_day(day):
            return None
        windows = self._calendar.sessions_for(day)
        if not windows:
            return None
        return MarketSession(
            session_date=day,
            close_at=max(item.end_at for item in windows),
        )
