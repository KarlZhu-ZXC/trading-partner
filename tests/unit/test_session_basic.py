"""Phase 1D D7: infer_session_basic half-open windows, weekend, DST, timezone safety."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from domain.common.enums import Market, TradingSession
from domain.common.errors import DataContractError
from domain.market.session import infer_session_basic

SH = ZoneInfo("Asia/Shanghai")
NY = ZoneInfo("America/New_York")


def _a(local: datetime) -> TradingSession:
    assert local.tzinfo is not None
    return infer_session_basic(Market.A_SHARE, local, timezone="Asia/Shanghai")


def _us(local: datetime) -> TradingSession:
    assert local.tzinfo is not None
    return infer_session_basic(Market.US, local, timezone="America/New_York")


# --- A-share half-open boundaries (local Asia/Shanghai) ---


def test_a_share_morning_open_inclusive() -> None:
    assert _a(datetime(2026, 7, 16, 9, 30, tzinfo=SH)) is TradingSession.REGULAR


def test_a_share_morning_close_exclusive() -> None:
    assert _a(datetime(2026, 7, 16, 11, 30, tzinfo=SH)) is TradingSession.CLOSED
    assert _a(datetime(2026, 7, 16, 11, 29, 59, tzinfo=SH)) is TradingSession.REGULAR


def test_a_share_afternoon_open_inclusive() -> None:
    assert _a(datetime(2026, 7, 16, 13, 0, tzinfo=SH)) is TradingSession.REGULAR


def test_a_share_afternoon_close_exclusive() -> None:
    assert _a(datetime(2026, 7, 16, 15, 0, tzinfo=SH)) is TradingSession.CLOSED
    assert _a(datetime(2026, 7, 16, 14, 59, 59, tzinfo=SH)) is TradingSession.REGULAR


def test_a_share_lunch_and_pre_post_closed() -> None:
    assert _a(datetime(2026, 7, 16, 9, 29, tzinfo=SH)) is TradingSession.CLOSED
    assert _a(datetime(2026, 7, 16, 12, 0, tzinfo=SH)) is TradingSession.CLOSED
    assert _a(datetime(2026, 7, 16, 15, 1, tzinfo=SH)) is TradingSession.CLOSED
    assert _a(datetime(2026, 7, 16, 8, 0, tzinfo=SH)) is TradingSession.CLOSED


# --- US half-open boundaries (local America/New_York) ---


def test_us_pre_open_inclusive() -> None:
    assert _us(datetime(2026, 7, 16, 4, 0, tzinfo=NY)) is TradingSession.PRE_MARKET


def test_us_pre_end_regular_start() -> None:
    assert _us(datetime(2026, 7, 16, 9, 30, tzinfo=NY)) is TradingSession.REGULAR
    assert _us(datetime(2026, 7, 16, 9, 29, 59, tzinfo=NY)) is TradingSession.PRE_MARKET


def test_us_regular_end_post_start() -> None:
    assert _us(datetime(2026, 7, 16, 16, 0, tzinfo=NY)) is TradingSession.POST_MARKET
    assert _us(datetime(2026, 7, 16, 15, 59, 59, tzinfo=NY)) is TradingSession.REGULAR


def test_us_post_end_exclusive() -> None:
    assert _us(datetime(2026, 7, 16, 20, 0, tzinfo=NY)) is TradingSession.CLOSED
    assert _us(datetime(2026, 7, 16, 19, 59, 59, tzinfo=NY)) is TradingSession.POST_MARKET


def test_us_overnight_closed() -> None:
    assert _us(datetime(2026, 7, 16, 3, 59, tzinfo=NY)) is TradingSession.CLOSED
    assert _us(datetime(2026, 7, 16, 21, 0, tzinfo=NY)) is TradingSession.CLOSED


# --- Weekend always CLOSED ---


def test_weekend_closed_a_share_and_us() -> None:
    # 2026-07-18 Saturday, 2026-07-19 Sunday
    for day in (18, 19):
        assert _a(datetime(2026, 7, day, 10, 0, tzinfo=SH)) is TradingSession.CLOSED
        assert _us(datetime(2026, 7, day, 12, 0, tzinfo=NY)) is TradingSession.CLOSED


# --- DST conversion: UTC → America/New_York ---


def test_us_dst_ed_t_summer_conversion() -> None:
    # 2026-07-16 13:30 UTC = 09:30 EDT (UTC-4) → REGULAR open
    utc = datetime(2026, 7, 16, 13, 30, tzinfo=UTC)
    assert infer_session_basic(Market.US, utc, timezone="America/New_York") is (
        TradingSession.REGULAR
    )


def test_us_standard_time_est_winter_conversion() -> None:
    # 2026-01-15 14:30 UTC = 09:30 EST (UTC-5) → REGULAR open
    utc = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    assert infer_session_basic(Market.US, utc, timezone="America/New_York") is (
        TradingSession.REGULAR
    )
    # 13:30 UTC = 08:30 EST → PRE_MARKET
    assert (
        infer_session_basic(
            Market.US,
            datetime(2026, 1, 15, 13, 30, tzinfo=UTC),
            timezone="America/New_York",
        )
        is TradingSession.PRE_MARKET
    )


def test_a_share_utc_conversion() -> None:
    # 2026-07-16 01:30 UTC = 09:30 Asia/Shanghai → REGULAR
    utc = datetime(2026, 7, 16, 1, 30, tzinfo=UTC)
    assert infer_session_basic(Market.A_SHARE, utc, timezone="Asia/Shanghai") is (
        TradingSession.REGULAR
    )


# --- Timezone safety ---


def test_timezone_mismatch_safe_error_no_echo() -> None:
    at = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    malicious = "America/New_York'; DROP TABLE secrets;--"
    with pytest.raises(DataContractError) as exc_info:
        infer_session_basic(Market.A_SHARE, at, timezone=malicious)
    err = exc_info.value
    assert err.details.get("rule") == "expected_market_timezone"
    assert err.details.get("field") == "timezone"
    assert set(err.details.keys()) == {"field", "rule"}
    assert malicious not in str(err)
    assert malicious not in repr(err.details)
    assert err.__cause__ is None
    assert err.__context__ is None


def test_blank_and_padded_timezone_rejected_without_echo() -> None:
    at = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    for bad in ("", "   ", " Asia/Shanghai", "Asia/Shanghai "):
        with pytest.raises(DataContractError) as exc_info:
            infer_session_basic(Market.A_SHARE, at, timezone=bad)
        assert exc_info.value.details.get("rule") == "expected_market_timezone"
        assert bad.strip() == "" or bad not in str(exc_info.value.details)


def test_us_wrong_zone_string_rejected() -> None:
    at = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with pytest.raises(DataContractError) as exc_info:
        infer_session_basic(Market.US, at, timezone="Asia/Shanghai")
    assert exc_info.value.details.get("rule") == "expected_market_timezone"
    assert "Asia/Shanghai" not in repr(exc_info.value.details)


def test_rejects_naive_at() -> None:
    with pytest.raises(DataContractError) as exc_info:
        infer_session_basic(
            Market.US,
            datetime(2026, 7, 16, 10, 0),
            timezone="America/New_York",
        )
    assert exc_info.value.details.get("field") == "at"


def test_rejects_non_string_timezone() -> None:
    at = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with pytest.raises(DataContractError) as exc_info:
        infer_session_basic(Market.US, at, timezone=123)  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "timezone"
    assert exc_info.value.details.get("rule") == "type"


def test_rejects_bad_market_type() -> None:
    at = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with pytest.raises(DataContractError) as exc_info:
        infer_session_basic("US", at, timezone="America/New_York")  # type: ignore[arg-type]
    assert exc_info.value.details.get("field") == "market"
