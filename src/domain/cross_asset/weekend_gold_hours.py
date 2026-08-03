"""Published IG Weekend Gold observation window.

IG quotes its weekend contract separately from weekday spot gold.  The window
is expressed in Europe/London time so daylight-saving transitions remain
explicit rather than being approximated with a fixed UTC offset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from domain.common.time import require_aware_datetime

_LONDON = ZoneInfo("Europe/London")
_OPEN = time(8, 0)
_CLOSE = time(22, 40)


@dataclass(frozen=True, slots=True)
class IGWeekendGoldWindow:
    is_open: bool
    next_open_at: datetime


def ig_weekend_gold_window(moment: datetime) -> IGWeekendGoldWindow:
    """Return whether the IG weekend contract is open and its next opening."""

    require_aware_datetime(moment, field_name="moment")
    local = moment.astimezone(_LONDON)
    weekday = local.weekday()
    local_time = local.timetz().replace(tzinfo=None)

    if weekday == 5 and local_time >= _OPEN:
        next_open_date = local.date() + timedelta(days=7)
        return IGWeekendGoldWindow(
            True,
            datetime.combine(next_open_date, _OPEN, tzinfo=_LONDON).astimezone(
                moment.tzinfo
            ),
        )
    if weekday == 6 and local_time < _CLOSE:
        next_open_date = local.date() + timedelta(days=6)
        return IGWeekendGoldWindow(
            True,
            datetime.combine(next_open_date, _OPEN, tzinfo=_LONDON).astimezone(
                moment.tzinfo
            ),
        )

    days_until_saturday = (5 - weekday) % 7
    if days_until_saturday == 0 and local_time >= _OPEN:
        days_until_saturday = 7
    next_open_date = local.date() + timedelta(days=days_until_saturday)
    next_open = datetime.combine(next_open_date, _OPEN, tzinfo=_LONDON)
    return IGWeekendGoldWindow(False, next_open.astimezone(moment.tzinfo))


__all__ = ["IGWeekendGoldWindow", "ig_weekend_gold_window"]
