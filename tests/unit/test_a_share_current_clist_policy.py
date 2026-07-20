"""Current-only clist trade_date policy (Phase 1E E2)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from domain.a_share.current_clist_policy import (
    require_current_clist_trade_date,
    resolve_supportable_closed_trade_date,
)
from domain.common.errors import DataContractError
from infrastructure.providers.a_share.trading_calendar import JsonAShareTradingCalendar

_CAL = JsonAShareTradingCalendar.load(
    Path(__file__).resolve().parents[2] / "config" / "a_share_trading_calendar.v1.json"
)
_SH = ZoneInfo("Asia/Shanghai")

# Tuesday 2024-01-16 is a Shanghai trading day; previous = 2024-01-15 (Mon).
_TRADING_DAY = date(2024, 1, 16)
_PREV_TRADING_DAY = date(2024, 1, 15)


def _sh(hour: int, minute: int = 0, *, day: date = _TRADING_DAY) -> datetime:
    """Build an aware datetime at the given Asia/Shanghai wall clock."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_SH)


def _resolve(now: datetime) -> date:
    return resolve_supportable_closed_trade_date(
        now=now,
        is_trading_day=_CAL.is_trading_day,
        previous_trading_day=_CAL.previous_trading_day,
    )


def test_before_open_uses_previous() -> None:
    # 09:29 inclusive → still pre-open → previous closed session.
    assert _resolve(_sh(9, 29)) == _PREV_TRADING_DAY


def test_at_open_rejected() -> None:
    # 09:30 inclusive starts the reject window.
    with pytest.raises(DataContractError) as exc:
        _resolve(_sh(9, 30))
    assert exc.value.details.get("rule") == "current_cross_section_in_session"
    assert exc.value.retryable is False


def test_lunch_start_rejected() -> None:
    # Lunch is still in-session for current clist (partial same-day data).
    with pytest.raises(DataContractError) as exc:
        _resolve(_sh(11, 30))
    assert exc.value.details.get("rule") == "current_cross_section_in_session"


def test_lunch_mid_rejected() -> None:
    with pytest.raises(DataContractError) as exc:
        _resolve(_sh(12, 0))
    assert exc.value.details.get("rule") == "current_cross_section_in_session"


def test_lunch_end_rejected() -> None:
    with pytest.raises(DataContractError) as exc:
        _resolve(_sh(12, 59))
    assert exc.value.details.get("rule") == "current_cross_section_in_session"


def test_afternoon_before_close_rejected() -> None:
    with pytest.raises(DataContractError) as exc:
        _resolve(_sh(14, 59))
    assert exc.value.details.get("rule") == "current_cross_section_in_session"


def test_at_close_today_allowed() -> None:
    # 15:00 inclusive → support current trading day.
    assert _resolve(_sh(15, 0)) == _TRADING_DAY
    require_current_clist_trade_date(
        trade_date=_TRADING_DAY,
        now=_sh(15, 0),
        is_trading_day=_CAL.is_trading_day,
        previous_trading_day=_CAL.previous_trading_day,
        operation="test",
    )


def test_after_close_today_allowed_utc_form() -> None:
    # 2024-01-16 15:00 Shanghai as UTC (legacy form used across E2 fixtures).
    now = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
    assert _resolve(now) == _TRADING_DAY


def test_weekend_previous_day_allowed() -> None:
    # Saturday 2024-01-20 noon Shanghai → previous Friday 2024-01-19.
    now = datetime(2024, 1, 20, 4, 0, tzinfo=UTC)
    assert _resolve(now) == date(2024, 1, 19)


def test_official_holiday_previous_day_allowed() -> None:
    # 2024-05-01 Labour Day (Wed holiday) → previous trading day 2024-04-30.
    holiday = date(2024, 5, 1)
    assert _CAL.is_trading_day(holiday) is False
    assert _resolve(_sh(12, 0, day=holiday)) == date(2024, 4, 30)


def test_during_morning_session_rejected() -> None:
    now = datetime(2024, 1, 16, 2, 0, tzinfo=UTC)  # 10:00 Shanghai
    with pytest.raises(DataContractError) as exc:
        _resolve(now)
    assert exc.value.details.get("rule") == "current_cross_section_in_session"
    assert exc.value.retryable is False


def test_arbitrary_historical_rejected() -> None:
    now = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
    with pytest.raises(DataContractError) as exc:
        require_current_clist_trade_date(
            trade_date=date(2024, 1, 2),
            now=now,
            is_trading_day=_CAL.is_trading_day,
            previous_trading_day=_CAL.previous_trading_day,
            operation="test",
        )
    assert exc.value.details.get("rule") == "current_only_trade_date"


def test_future_rejected() -> None:
    now = datetime(2024, 1, 16, 7, 0, tzinfo=UTC)
    with pytest.raises(DataContractError) as exc:
        require_current_clist_trade_date(
            trade_date=date(2024, 1, 17),
            now=now,
            is_trading_day=_CAL.is_trading_day,
            previous_trading_day=_CAL.previous_trading_day,
            operation="test",
        )
    assert exc.value.details.get("rule") == "current_only_trade_date"


def test_pre_open_trading_day_uses_previous() -> None:
    # Monday 2024-01-15 08:00 Shanghai (before open)
    now = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    assert _resolve(now) == date(2024, 1, 12)
