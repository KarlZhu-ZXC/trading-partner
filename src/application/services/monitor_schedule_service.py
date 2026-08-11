"""Deterministic due calculation shared by Monitor dashboard and dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from application.ports.market_session_calendar import MarketSessionCalendar
from domain.common.enums import Market
from domain.cross_asset.weekend_gold_hours import ig_weekend_gold_window
from domain.monitoring.enums import MonitorCadence, MonitorRuleType, MonitorRunStatus
from domain.monitoring.models import MonitorDefinition, MonitorRun
from domain.trade_plan.enums import TradePlanFactType

MonitorScheduleHealth = Literal[
    "ON_DEMAND",
    "MARKET_SCHEDULED",
    "MARKET_CLOSED",
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
        weekend_rwa_proxy_enabled: bool = False,
        ig_weekend_gold_enabled: bool = False,
    ) -> None:
        self._us_calendar = us_calendar
        self._a_share_calendar = a_share_calendar
        self._kr_calendar = kr_calendar
        self._post_market_delay = timedelta(minutes=post_market_delay_minutes)
        self._weekend_rwa_proxy_enabled = bool(weekend_rwa_proxy_enabled)
        self._ig_weekend_gold_enabled = bool(ig_weekend_gold_enabled)

    @property
    def session_calendars(self) -> dict[Market, MarketSessionCalendar]:
        """Calendars shared by scheduling and daily-fact freshness policy."""

        return {
            market: calendar
            for market, calendar in (
                (Market.US, self._us_calendar),
                (Market.A_SHARE, self._a_share_calendar),
                (Market.KR, self._kr_calendar),
            )
            if calendar is not None
        }

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

    def _interval_status(
        self,
        monitor: MonitorDefinition,
        latest_run: MonitorRun | None,
        now: datetime,
    ) -> MonitorSchedule:
        assert monitor.interval_minutes is not None
        reopens_at = _interval_market_reopens_at(
            monitor,
            now,
            weekend_rwa_proxy_enabled=self._weekend_rwa_proxy_enabled,
            ig_weekend_gold_enabled=self._ig_weekend_gold_enabled,
        )
        if reopens_at is not None:
            return MonitorSchedule(reopens_at, False, "MARKET_CLOSED")
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
        # INTERVAL definitions are whole-hour schedules and launchd wakes hourly.
        # Anchor to the run-start hour so Provider latency (or an on-demand run at
        # another minute) cannot slide a two-hour Monitor into a three-hour cycle.
        interval_anchor = latest_run.started_at.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        next_due = interval_anchor + timedelta(minutes=retry_minutes)
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


_NEW_YORK = ZoneInfo("America/New_York")
_DUKASCOPY_WEEKEND_INSTRUMENTS = frozenset(
    {
        "commodity_spot:OTC:XAUUSD",
        "commodity_spot:OTC:XAGUSD",
        "cfd:OTC:LIGHT_CMD_USD",
    }
)
_WEEKEND_RWA_PROXY_INSTRUMENTS = frozenset(
    {"commodity_spot:OTC:XAUUSD", "cfd:OTC:LIGHT_CMD_USD"}
)


def _interval_market_reopens_at(
    monitor: MonitorDefinition,
    now: datetime,
    *,
    weekend_rwa_proxy_enabled: bool = False,
    ig_weekend_gold_enabled: bool = False,
) -> datetime | None:
    """Return the next known observation window for venue-scoped intervals.

    The supported Dukascopy OTC instruments use a New-York-aligned 24/5 schedule
    with a daily 17:00-18:00 ET break. A closed venue is a scheduling fact, not a
    failed observation, so the dispatcher waits instead of manufacturing recurring
    NOT_EVALUATED runs unless a labelled current weekend proxy is enabled.
    """
    if any(
        rule.rule_type
        not in {MonitorRuleType.PRICE_ABOVE, MonitorRuleType.PRICE_BELOW}
        and rule.fact_type is not TradePlanFactType.PRICE
        for rule in monitor.rules
    ):
        return None
    instrument_ids = {
        item
        for item in (
            monitor.primary_instrument_id,
            *(rule.instrument_id for rule in monitor.rules),
        )
        if item is not None
    }
    if not instrument_ids or not instrument_ids.issubset(_DUKASCOPY_WEEKEND_INSTRUMENTS):
        return None

    local = now.astimezone(_NEW_YORK)
    weekday = local.weekday()
    local_time = local.timetz().replace(tzinfo=None)
    close = time(17)
    reopen = time(18)

    weekend_closure = False
    if weekday == 4 and local_time >= close:  # Friday close through Sunday reopen.
        reopen_date = local.date() + timedelta(days=2)
        weekend_closure = True
    elif weekday == 5:
        reopen_date = local.date() + timedelta(days=1)
        weekend_closure = True
    elif (weekday == 6 and local_time < reopen) or (
        weekday in {0, 1, 2, 3} and close <= local_time < reopen
    ):
        reopen_date = local.date()
        weekend_closure = weekday == 6
    else:
        return None
    dukascopy_reopens_at = datetime.combine(
        reopen_date, reopen, tzinfo=_NEW_YORK
    ).astimezone(
        now.tzinfo
    )
    if (
        weekend_closure
        and weekend_rwa_proxy_enabled
        and instrument_ids.issubset(_WEEKEND_RWA_PROXY_INSTRUMENTS)
    ):
        return None
    if (
        ig_weekend_gold_enabled
        and instrument_ids == {"commodity_spot:OTC:XAUUSD"}
    ):
        ig_window = ig_weekend_gold_window(now)
        if ig_window.is_open:
            return None
        return min(dukascopy_reopens_at, ig_window.next_open_at)
    return dukascopy_reopens_at
