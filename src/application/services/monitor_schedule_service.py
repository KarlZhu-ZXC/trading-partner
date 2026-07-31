"""Deterministic due calculation shared by Monitor dashboard and dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from application.ports.market_session_calendar import MarketSessionCalendar
from domain.monitoring.enums import MonitorCadence, MonitorRunStatus
from domain.monitoring.models import MonitorDefinition, MonitorRun

MonitorScheduleHealth = Literal[
    "ON_DEMAND",
    "MARKET_SCHEDULED",
    "NEVER_RUN",
    "ON_SCHEDULE",
    "OVERDUE",
]


@dataclass(frozen=True, slots=True)
class MonitorSchedule:
    next_due_at: datetime | None
    due: bool
    health: MonitorScheduleHealth


class MonitorScheduleService:
    def __init__(
        self,
        *,
        us_calendar: MarketSessionCalendar | None = None,
        a_share_calendar: MarketSessionCalendar | None = None,
        kr_calendar: MarketSessionCalendar | None = None,
        post_market_delay_minutes: int = 10,
    ) -> None:
        self._us_calendar = us_calendar
        self._a_share_calendar = a_share_calendar
        self._kr_calendar = kr_calendar
        self._post_market_delay = timedelta(minutes=post_market_delay_minutes)

    def status(
        self,
        monitor: MonitorDefinition,
        latest_run: MonitorRun | None,
        now: datetime,
    ) -> MonitorSchedule:
        if monitor.cadence is MonitorCadence.ON_DEMAND:
            return MonitorSchedule(None, False, "ON_DEMAND")
        if monitor.cadence is MonitorCadence.INTERVAL:
            return self._interval_status(monitor, latest_run, now)
        calendar = {
            MonitorCadence.US_POST_MARKET: self._us_calendar,
            MonitorCadence.A_SHARE_POST_MARKET: self._a_share_calendar,
            MonitorCadence.KR_POST_MARKET: self._kr_calendar,
        }.get(monitor.cadence)
        if calendar is None:
            return MonitorSchedule(None, False, "MARKET_SCHEDULED")
        return self._market_status(monitor, latest_run, now, calendar)

    @staticmethod
    def _interval_status(
        monitor: MonitorDefinition,
        latest_run: MonitorRun | None,
        now: datetime,
    ) -> MonitorSchedule:
        assert monitor.interval_minutes is not None
        if latest_run is None:
            next_due = monitor.created_at
            return MonitorSchedule(
                next_due,
                now >= next_due,
                "NEVER_RUN" if now < next_due else "OVERDUE",
            )
        retry_minutes = (
            60
            if latest_run.status in {MonitorRunStatus.PARTIAL, MonitorRunStatus.FAILED}
            else monitor.interval_minutes
        )
        next_due = latest_run.completed_at + timedelta(minutes=retry_minutes)
        due = now >= next_due
        return MonitorSchedule(next_due, due, "OVERDUE" if due else "ON_SCHEDULE")

    def _market_status(
        self,
        monitor: MonitorDefinition,
        latest_run: MonitorRun | None,
        now: datetime,
        calendar: MarketSessionCalendar,
    ) -> MonitorSchedule:
        session = calendar.session_at(now)
        if session is None:
            session = calendar.session_on_or_before(now)
        if session is None:
            return MonitorSchedule(None, False, "MARKET_SCHEDULED")
        scheduled = session.close_at + self._post_market_delay
        if now < scheduled:
            return MonitorSchedule(scheduled, False, "MARKET_SCHEDULED")
        completed_for_session = (
            latest_run is not None
            and latest_run.cadence is monitor.cadence
            and latest_run.completed_at >= scheduled
        )
        if not completed_for_session:
            return MonitorSchedule(scheduled, True, "OVERDUE")
        next_session = calendar.next_session(session.session_date)
        if next_session is None:
            return MonitorSchedule(None, False, "MARKET_SCHEDULED")
        return MonitorSchedule(
            next_session.close_at + self._post_market_delay,
            False,
            "ON_SCHEDULE",
        )
