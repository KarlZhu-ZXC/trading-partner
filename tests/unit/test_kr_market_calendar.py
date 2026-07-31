"""Focused Korea Exchange calendar contract."""

from datetime import UTC, datetime

from infrastructure.calendars.kr_market_session_calendar import XkrxMarketSessionCalendar


def test_xkrx_calendar_returns_korean_session_close() -> None:
    calendar = XkrxMarketSessionCalendar()

    session = calendar.session_at(datetime(2026, 7, 30, 7, 0, tzinfo=UTC))

    assert session is not None
    assert session.session_date.isoformat() == "2026-07-30"
    assert session.close_at == datetime(2026, 7, 30, 6, 30, tzinfo=UTC)
