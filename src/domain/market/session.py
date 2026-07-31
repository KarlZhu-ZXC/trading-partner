"""Basic trading-session inference heuristics (Phase 1D D7).

Fixed weekday open/close windows only — not an official exchange calendar.
Holidays and temporary closures may be misclassified as REGULAR or CLOSED.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from domain.common.enums import Market, TradingSession
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

# Expected IANA zones per market. Validated before ZoneInfo construction so
# malicious timezone strings are never passed to zoneinfo or echoed in errors.
_MARKET_TIMEZONES: dict[Market, str] = {
    Market.A_SHARE: "Asia/Shanghai",
    Market.US: "America/New_York",
    Market.KR: "Asia/Seoul",
}

# Half-open local intervals [start, end).
_A_SHARE_MORNING = (time(9, 30), time(11, 30))
_A_SHARE_AFTERNOON = (time(13, 0), time(15, 0))
_US_PRE = (time(4, 0), time(9, 30))
_US_REGULAR = (time(9, 30), time(16, 0))
_US_POST = (time(16, 0), time(20, 0))
_KR_REGULAR = (time(9, 0), time(15, 30))


def _safe_timezone_error() -> DataContractError:
    """Stable contract error; never echo timezone payload or ZoneInfo cause."""
    return DataContractError(
        "timezone is invalid for market",
        details={"field": "timezone", "rule": "expected_market_timezone"},
    )


def _in_half_open(local_t: time, start: time, end: time) -> bool:
    return start <= local_t < end


def infer_session_basic(
    market: Market,
    at: datetime,
    *,
    timezone: str,
) -> TradingSession:
    """Infer session from fixed weekday windows in the market local timezone.

    ``Market.A_SHARE`` requires ``timezone="Asia/Shanghai"``;
    ``Market.US`` requires ``timezone="America/New_York"`` and ``Market.KR``
    requires ``timezone="Asia/Seoul"``.
    Weekend → CLOSED. Unknown market → UNKNOWN.
    """
    require_aware_datetime(at, field_name="at")

    if not isinstance(market, Market):
        raise DataContractError(
            "market must be a Market",
            details={
                "field": "market",
                "rule": "type",
                "type": type(market).__name__,
            },
        )

    if not isinstance(timezone, str):
        raise DataContractError(
            "timezone must be a string",
            details={
                "field": "timezone",
                "rule": "type",
                "type": type(timezone).__name__,
            },
        )
    if not timezone or not timezone.strip() or timezone != timezone.strip():
        raise _safe_timezone_error()

    expected = _MARKET_TIMEZONES.get(market)
    if expected is None:
        return TradingSession.UNKNOWN

    # Validate expected zone string before any ZoneInfo construction.
    if timezone != expected:
        raise _safe_timezone_error()

    try:
        zone = ZoneInfo(expected)
    except ZoneInfoNotFoundError:
        raise _safe_timezone_error() from None
    except Exception:
        # Sanitize any zoneinfo/platform failure: no cause, context, or echo.
        raise _safe_timezone_error() from None

    local = at.astimezone(zone)
    # Monday=0 … Sunday=6
    if local.weekday() >= 5:
        return TradingSession.CLOSED

    local_t = local.time()

    if market is Market.A_SHARE:
        if _in_half_open(local_t, *_A_SHARE_MORNING) or _in_half_open(
            local_t, *_A_SHARE_AFTERNOON
        ):
            return TradingSession.REGULAR
        return TradingSession.CLOSED

    if market is Market.US:
        if _in_half_open(local_t, *_US_PRE):
            return TradingSession.PRE_MARKET
        if _in_half_open(local_t, *_US_REGULAR):
            return TradingSession.REGULAR
        if _in_half_open(local_t, *_US_POST):
            return TradingSession.POST_MARKET
        return TradingSession.CLOSED

    if market is Market.KR:
        return (
            TradingSession.REGULAR
            if _in_half_open(local_t, *_KR_REGULAR)
            else TradingSession.CLOSED
        )

    return TradingSession.UNKNOWN
