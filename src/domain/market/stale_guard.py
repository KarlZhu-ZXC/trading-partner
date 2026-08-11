"""Stale OHLCV guard pure rules (Phase 1D D7).

Compares latest bar time against ``min(now, as_of)``. Closed-session policy is
conservative by default (reject unless allow_closed_session_last_bar).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.common.enums import TradingSession
from domain.common.errors import DataContractError, StaleMarketData
from domain.common.time import require_aware_datetime

_ACTIVE_SESSIONS = frozenset(
    {
        TradingSession.REGULAR,
        TradingSession.PRE_MARKET,
        TradingSession.POST_MARKET,
        TradingSession.OVERNIGHT,
    }
)


@dataclass(frozen=True, slots=True)
class StaleGuardConfig:
    max_age_seconds: int
    respect_session: bool
    allow_closed_session_last_bar: bool

    def __post_init__(self) -> None:
        if not isinstance(self.max_age_seconds, int) or isinstance(
            self.max_age_seconds, bool
        ):
            raise DataContractError(
                "max_age_seconds must be an int",
                details={
                    "field": "max_age_seconds",
                    "type": type(self.max_age_seconds).__name__,
                },
            )
        if self.max_age_seconds < 0:
            raise DataContractError(
                "max_age_seconds must be nonnegative",
                details={"field": "max_age_seconds", "rule": "nonnegative"},
            )
        if type(self.respect_session) is not bool:
            raise DataContractError(
                "respect_session must be a bool",
                details={
                    "field": "respect_session",
                    "rule": "type",
                    "type": type(self.respect_session).__name__,
                },
            )
        if type(self.allow_closed_session_last_bar) is not bool:
            raise DataContractError(
                "allow_closed_session_last_bar must be a bool",
                details={
                    "field": "allow_closed_session_last_bar",
                    "rule": "type",
                    "type": type(self.allow_closed_session_last_bar).__name__,
                },
            )


def assert_ohlcv_not_stale(
    *,
    latest_bar_time: datetime,
    now: datetime,
    as_of: datetime,
    session: TradingSession,
    config: StaleGuardConfig,
) -> None:
    """Raise StaleMarketData or DataContractError when OHLCV violates guard rules.

    Stale details are fixed to ``field=latest_bar_time`` and rule
    ``max_age_exceeded`` or ``closed_session_not_allowed`` — never age/timestamps.
    """
    require_aware_datetime(latest_bar_time, field_name="latest_bar_time")
    require_aware_datetime(now, field_name="now")
    require_aware_datetime(as_of, field_name="as_of")

    if not isinstance(session, TradingSession):
        raise DataContractError(
            "session must be a TradingSession",
            details={
                "field": "session",
                "rule": "type",
                "type": type(session).__name__,
            },
        )
    if not isinstance(config, StaleGuardConfig):
        raise DataContractError(
            "config must be a StaleGuardConfig",
            details={
                "field": "config",
                "rule": "type",
                "type": type(config).__name__,
            },
        )

    if latest_bar_time > as_of:
        raise DataContractError(
            "latest_bar_time must not be after as_of",
            details={"field": "latest_bar_time", "rule": "after_as_of"},
        )
    if latest_bar_time > now:
        raise DataContractError(
            "latest_bar_time must not be after now",
            details={"field": "latest_bar_time", "rule": "future_latest_bar"},
        )

    reference = min(now, as_of)
    age_seconds = (reference - latest_bar_time).total_seconds()

    if not config.respect_session:
        if age_seconds > config.max_age_seconds:
            raise StaleMarketData(
                "OHLCV latest bar exceeds max age",
                details={
                    "field": "latest_bar_time",
                    "rule": "max_age_exceeded",
                },
            )
        return

    # respect_session=True
    if session is TradingSession.CLOSED:
        if not config.allow_closed_session_last_bar:
            raise StaleMarketData(
                "OHLCV not allowed for closed session",
                details={
                    "field": "latest_bar_time",
                    "rule": "closed_session_not_allowed",
                },
            )
        # Allowed last bar still must satisfy max age.
        if age_seconds > config.max_age_seconds:
            raise StaleMarketData(
                "OHLCV latest bar exceeds max age",
                details={
                    "field": "latest_bar_time",
                    "rule": "max_age_exceeded",
                },
            )
        return

    # REGULAR | PRE_MARKET | POST_MARKET | UNKNOWN: age only (no closed special case).
    if session in _ACTIVE_SESSIONS or session is TradingSession.UNKNOWN:
        if age_seconds > config.max_age_seconds:
            raise StaleMarketData(
                "OHLCV latest bar exceeds max age",
                details={
                    "field": "latest_bar_time",
                    "rule": "max_age_exceeded",
                },
            )
        return

    # Defensive: any future enum member falls back to age check.
    if age_seconds > config.max_age_seconds:
        raise StaleMarketData(
            "OHLCV latest bar exceeds max age",
            details={
                "field": "latest_bar_time",
                "rule": "max_age_exceeded",
            },
        )
