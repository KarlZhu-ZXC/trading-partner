"""Current-only cross-section trade_date policy (Phase 1E E2).

Eastmoney ``clist/get`` and push2ex pool endpoints return a *current*
cross-section, not a historical series. Labeling live rows as an arbitrary
``trade_date`` would be a hindsight leak.

This pure domain policy derives the single closed trading date that current
snapshots may truthfully answer from the actual clock in Asia/Shanghai and a
calendar surface (``is_trading_day`` / ``previous_trading_day``). No I/O.

Do **not** use ``infer_session_basic``: that heuristic treats the 11:30–13:00
lunch break as closed, but current clist already contains partial same-day
data from the morning session. The reject window is the full
[09:30, 15:00) Shanghai local interval on a trading day.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

_SHANGHAI = ZoneInfo("Asia/Shanghai")
# Continuous reject window on a trading day: from open inclusive to close exclusive.
# Includes the entire 11:30–13:00 lunch break (partial same-day clist data).
_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(15, 0)

IsTradingDay = Callable[[date], bool]
PreviousTradingDay = Callable[[date], date]


def resolve_supportable_closed_trade_date(
    *,
    now: datetime,
    is_trading_day: IsTradingDay,
    previous_trading_day: PreviousTradingDay,
) -> date:
    """Return the only closed trade_date a current cross-section may answer.

    Rules (Asia/Shanghai local clock):

    - On a trading day, local time in ``[09:30, 15:00)``: unavailable (raises).
      This includes the entire lunch break ``[11:30, 13:00)`` because current
      clist already contains partial same-day data.
    - On a trading day at or after ``15:00``: that trading day.
    - On a trading day before ``09:30``, or on a weekend/official holiday:
      previous trading day.
    """
    require_aware_datetime(now, field_name="now")
    local = now.astimezone(_SHANGHAI)
    local_day = local.date()
    local_t = local.time()

    if is_trading_day(local_day):
        if _SESSION_OPEN <= local_t < _SESSION_CLOSE:
            raise DataContractError(
                "current cross-section is unavailable while the trading day "
                "is open or mid-session (including lunch); live partial data "
                "must not be labeled as a closed trade_date",
                details={
                    "field": "trade_date",
                    "rule": "current_cross_section_in_session",
                    "local_date": local_day.isoformat(),
                },
            )
        if local_t >= _SESSION_CLOSE:
            return local_day
        # Before 09:30 on a trading day → previous closed session.
        return previous_trading_day(local_day)

    # Weekend or official holiday → previous closed trading day.
    return previous_trading_day(local_day)


def require_current_clist_trade_date(
    *,
    trade_date: date,
    now: datetime,
    is_trading_day: IsTradingDay,
    previous_trading_day: PreviousTradingDay,
    operation: str,
) -> date:
    """Require ``trade_date`` equals the single supportable closed trade_date.

    Returns the supportable date on success. Raises non-retryable
    ``DataContractError`` for future dates, arbitrary history, or in-session
    requests. Callers must not perform network I/O after this raises.
    """
    if type(trade_date) is not date:
        raise DataContractError(
            "trade_date must be a date (not datetime)",
            details={
                "field": "trade_date",
                "rule": "exact_date_type",
                "operation": operation,
            },
        )
    require_aware_datetime(now, field_name="now")
    supportable = resolve_supportable_closed_trade_date(
        now=now,
        is_trading_day=is_trading_day,
        previous_trading_day=previous_trading_day,
    )
    if trade_date != supportable:
        raise DataContractError(
            "trade_date is not the currently supportable closed session for "
            "current-only cross-section endpoints",
            details={
                "field": "trade_date",
                "rule": "current_only_trade_date",
                "operation": operation,
                "requested": trade_date.isoformat(),
                "supportable": supportable.isoformat(),
            },
        )
    return supportable
