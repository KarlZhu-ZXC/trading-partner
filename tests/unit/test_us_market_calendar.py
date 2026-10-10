"""Bounded historical XNYS session coverage for provider facts and operations."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars
import pytest

from infrastructure.calendars.us_market_session_calendar import XnysMarketSessionCalendar

NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("day", "utc_hour"),
    [
        (date(1990, 1, 2), 21),
        (date(2000, 1, 3), 21),
        (date(2000, 3, 31), 21),
        (date(2000, 4, 3), 20),
        (date(2000, 6, 15), 20),
    ],
)
def test_xnys_historical_regular_close_and_historical_dst(day: date, utc_hour: int) -> None:
    calendar = XnysMarketSessionCalendar()

    session = calendar.session_at(datetime(day.year, day.month, day.day, 12, 0, tzinfo=NY))

    assert session is not None
    assert session.session_date == day
    assert session.close_at == datetime(day.year, day.month, day.day, utc_hour, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "utc_hour"),
    [
        (date(1992, 11, 27), 19),
        (date(1993, 11, 26), 18),
        (date(2000, 11, 24), 18),
        (date(2000, 7, 3), 17),
    ],
)
def test_xnys_historical_early_close_retains_pre_1993_two_pm_rule(
    day: date, utc_hour: int,
) -> None:
    session = XnysMarketSessionCalendar().session_at(
        datetime(day.year, day.month, day.day, 12, 0, tzinfo=NY),
    )

    assert session is not None
    assert session.close_at == datetime(day.year, day.month, day.day, utc_hour, 0, tzinfo=UTC)


@pytest.mark.parametrize("day", [11, 12, 13, 14, 15, 16])
def test_xnys_september_11_closure_remains_closed(day: int) -> None:
    calendar = XnysMarketSessionCalendar()

    assert calendar.session_at(datetime(2001, 9, day, 12, 0, tzinfo=NY)) is None


def test_xnys_historical_session_navigation_crosses_september_11_closure() -> None:
    calendar = XnysMarketSessionCalendar()

    before = calendar.previous_session(date(2001, 9, 17))
    after = calendar.next_session(date(2001, 9, 10))
    latest = calendar.session_on_or_before(datetime(2001, 9, 14, 12, 0, tzinfo=NY))

    assert before is not None and before.session_date == date(2001, 9, 10)
    assert before.close_at == datetime(2001, 9, 10, 20, 0, tzinfo=UTC)
    assert after is not None and after.session_date == date(2001, 9, 17)
    assert after.close_at == datetime(2001, 9, 17, 20, 0, tzinfo=UTC)
    assert latest == before


@pytest.mark.parametrize("year", [1900, 1989])
def test_xnys_outside_declared_history_is_rejected(year: int) -> None:
    calendar = XnysMarketSessionCalendar()

    with pytest.raises(ValueError):
        calendar.session_at(datetime(year, 6, 15, 12, 0, tzinfo=NY))
    with pytest.raises(ValueError):
        calendar.session_on_or_before(datetime(year, 6, 15, 12, 0, tzinfo=NY))


def test_xnys_history_expansion_keeps_library_future_bound_and_other_instances() -> None:
    ordinary = exchange_calendars.get_calendar("XNYS")
    first_before, last_before = ordinary.first_session, ordinary.last_session

    expanded = XnysMarketSessionCalendar()

    assert expanded._calendar.first_session.date() == date(1990, 1, 2)
    assert expanded._calendar.last_session == last_before
    assert ordinary.first_session == first_before
    assert ordinary.last_session == last_before
    assert exchange_calendars.get_calendar("XNYS").first_session == first_before
    outside_future = expanded._calendar.last_session.to_pydatetime().date() + timedelta(days=370)
    with pytest.raises(ValueError):
        expanded.session_at(datetime(
            outside_future.year, outside_future.month, outside_future.day, 12, 0, tzinfo=NY,
        ))
