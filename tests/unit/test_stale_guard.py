"""Phase 1D D7: StaleGuardConfig and assert_ohlcv_not_stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain.common.enums import TradingSession
from domain.common.errors import DataContractError, StaleMarketData
from domain.market.stale_guard import StaleGuardConfig, assert_ohlcv_not_stale

NOW = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
AS_OF = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)


def _cfg(
    *,
    max_age_seconds: int = 86400,
    respect_session: bool = True,
    allow_closed_session_last_bar: bool = False,
) -> StaleGuardConfig:
    return StaleGuardConfig(
        max_age_seconds=max_age_seconds,
        respect_session=respect_session,
        allow_closed_session_last_bar=allow_closed_session_last_bar,
    )


def _assert(
    *,
    latest_bar_time: datetime,
    now: datetime = NOW,
    as_of: datetime = AS_OF,
    session: TradingSession = TradingSession.REGULAR,
    config: StaleGuardConfig | None = None,
) -> None:
    assert_ohlcv_not_stale(
        latest_bar_time=latest_bar_time,
        now=now,
        as_of=as_of,
        session=session,
        config=config if config is not None else _cfg(),
    )


def test_config_defaults_validation_accepts_zero_max_age() -> None:
    cfg = _cfg(max_age_seconds=0)
    assert cfg.max_age_seconds == 0


def test_config_rejects_bool_and_negative_max_age() -> None:
    with pytest.raises(DataContractError) as exc_info:
        StaleGuardConfig(
            max_age_seconds=True,  # type: ignore[arg-type]
            respect_session=True,
            allow_closed_session_last_bar=False,
        )
    assert exc_info.value.details.get("field") == "max_age_seconds"
    assert exc_info.value.details.get("type") == "bool"

    with pytest.raises(DataContractError) as exc_info:
        StaleGuardConfig(
            max_age_seconds=-1,
            respect_session=True,
            allow_closed_session_last_bar=False,
        )
    assert exc_info.value.details.get("rule") == "nonnegative"


def test_config_rejects_non_bool_flags() -> None:
    with pytest.raises(DataContractError) as exc_info:
        StaleGuardConfig(
            max_age_seconds=10,
            respect_session=1,  # type: ignore[arg-type]
            allow_closed_session_last_bar=False,
        )
    assert exc_info.value.details.get("field") == "respect_session"

    with pytest.raises(DataContractError) as exc_info:
        StaleGuardConfig(
            max_age_seconds=10,
            respect_session=True,
            allow_closed_session_last_bar=0,  # type: ignore[arg-type]
        )
    assert exc_info.value.details.get("field") == "allow_closed_session_last_bar"


def test_reference_is_min_of_now_and_as_of() -> None:
    """Age uses min(now, as_of); earlier as_of makes bar appear older."""
    bar = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    # now far ahead, as_of close to bar → age small vs as_of
    _assert(
        latest_bar_time=bar,
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        as_of=datetime(2026, 7, 16, 12, 30, tzinfo=UTC),
        config=_cfg(max_age_seconds=3600, respect_session=False),
    )
    # as_of far ahead, now close → same
    _assert(
        latest_bar_time=bar,
        now=datetime(2026, 7, 16, 12, 30, tzinfo=UTC),
        as_of=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        config=_cfg(max_age_seconds=3600, respect_session=False),
    )
    # both far → stale
    with pytest.raises(StaleMarketData) as exc_info:
        _assert(
            latest_bar_time=bar,
            now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
            as_of=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
            config=_cfg(max_age_seconds=3600, respect_session=False),
        )
    assert exc_info.value.details == {
        "field": "latest_bar_time",
        "rule": "max_age_exceeded",
    }


def test_age_equality_boundary_not_stale() -> None:
    bar = NOW - timedelta(seconds=100)
    _assert(
        latest_bar_time=bar,
        config=_cfg(max_age_seconds=100, respect_session=False),
    )


def test_age_just_over_boundary_stale() -> None:
    bar = NOW - timedelta(seconds=100, microseconds=1)
    with pytest.raises(StaleMarketData) as exc_info:
        _assert(
            latest_bar_time=bar,
            config=_cfg(max_age_seconds=100, respect_session=False),
        )
    assert exc_info.value.details == {
        "field": "latest_bar_time",
        "rule": "max_age_exceeded",
    }
    assert "100" not in repr(exc_info.value.details)
    assert "2026" not in repr(exc_info.value.details)


def test_future_bar_vs_now_and_as_of() -> None:
    with pytest.raises(DataContractError) as exc_info:
        _assert(latest_bar_time=NOW + timedelta(seconds=1), as_of=NOW + timedelta(hours=1))
    assert exc_info.value.details.get("rule") == "future_latest_bar"

    with pytest.raises(DataContractError) as exc_info:
        _assert(
            latest_bar_time=AS_OF + timedelta(seconds=1),
            now=AS_OF + timedelta(hours=1),
            as_of=AS_OF,
        )
    assert exc_info.value.details.get("rule") == "after_as_of"


def test_respect_session_false_ignores_closed() -> None:
    bar = NOW - timedelta(seconds=10)
    _assert(
        latest_bar_time=bar,
        session=TradingSession.CLOSED,
        config=_cfg(max_age_seconds=60, respect_session=False),
    )


def test_closed_session_not_allowed_by_default() -> None:
    bar = NOW - timedelta(seconds=10)
    with pytest.raises(StaleMarketData) as exc_info:
        _assert(
            latest_bar_time=bar,
            session=TradingSession.CLOSED,
            config=_cfg(
                max_age_seconds=60,
                respect_session=True,
                allow_closed_session_last_bar=False,
            ),
        )
    assert exc_info.value.details == {
        "field": "latest_bar_time",
        "rule": "closed_session_not_allowed",
    }
    assert exc_info.value.code == "STALE_MARKET_DATA"
    assert exc_info.value.retryable is True


def test_closed_session_allowed_still_checks_age() -> None:
    fresh_bar = NOW - timedelta(seconds=10)
    _assert(
        latest_bar_time=fresh_bar,
        session=TradingSession.CLOSED,
        config=_cfg(
            max_age_seconds=60,
            respect_session=True,
            allow_closed_session_last_bar=True,
        ),
    )
    old_bar = NOW - timedelta(seconds=120)
    with pytest.raises(StaleMarketData) as exc_info:
        _assert(
            latest_bar_time=old_bar,
            session=TradingSession.CLOSED,
            config=_cfg(
                max_age_seconds=60,
                respect_session=True,
                allow_closed_session_last_bar=True,
            ),
        )
    assert exc_info.value.details.get("rule") == "max_age_exceeded"


def test_active_sessions_use_age_only() -> None:
    bar = NOW - timedelta(seconds=10)
    for session in (
        TradingSession.REGULAR,
        TradingSession.PRE_MARKET,
        TradingSession.POST_MARKET,
    ):
        _assert(
            latest_bar_time=bar,
            session=session,
            config=_cfg(max_age_seconds=60, respect_session=True),
        )
    old = NOW - timedelta(seconds=120)
    for session in (
        TradingSession.REGULAR,
        TradingSession.PRE_MARKET,
        TradingSession.POST_MARKET,
    ):
        with pytest.raises(StaleMarketData) as exc_info:
            _assert(
                latest_bar_time=old,
                session=session,
                config=_cfg(max_age_seconds=60, respect_session=True),
            )
        assert exc_info.value.details.get("rule") == "max_age_exceeded"


def test_unknown_session_uses_age_not_closed_special_case() -> None:
    bar = NOW - timedelta(seconds=10)
    # UNKNOWN must not be treated as closed_session_not_allowed.
    _assert(
        latest_bar_time=bar,
        session=TradingSession.UNKNOWN,
        config=_cfg(
            max_age_seconds=60,
            respect_session=True,
            allow_closed_session_last_bar=False,
        ),
    )
    old = NOW - timedelta(seconds=120)
    with pytest.raises(StaleMarketData) as exc_info:
        _assert(
            latest_bar_time=old,
            session=TradingSession.UNKNOWN,
            config=_cfg(max_age_seconds=60, respect_session=True),
        )
    assert exc_info.value.details.get("rule") == "max_age_exceeded"
    assert exc_info.value.details.get("rule") != "closed_session_not_allowed"


def test_rejects_naive_and_bad_types() -> None:
    naive = datetime(2026, 7, 16, 15, 0)
    with pytest.raises(DataContractError) as exc_info:
        _assert(latest_bar_time=naive)
    assert exc_info.value.details.get("field") == "latest_bar_time"

    with pytest.raises(DataContractError) as exc_info:
        assert_ohlcv_not_stale(
            latest_bar_time=NOW,
            now=NOW,
            as_of=AS_OF,
            session="regular",  # type: ignore[arg-type]
            config=_cfg(),
        )
    assert exc_info.value.details.get("field") == "session"

    with pytest.raises(DataContractError) as exc_info:
        assert_ohlcv_not_stale(
            latest_bar_time=NOW,
            now=NOW,
            as_of=AS_OF,
            session=TradingSession.REGULAR,
            config={"max_age_seconds": 1},  # type: ignore[arg-type]
        )
    assert exc_info.value.details.get("field") == "config"


def test_stale_details_stable_no_malicious_echo() -> None:
    bar = NOW - timedelta(days=2)
    with pytest.raises(StaleMarketData) as exc_info:
        _assert(
            latest_bar_time=bar,
            config=_cfg(max_age_seconds=1, respect_session=False),
        )
    details = exc_info.value.details
    assert set(details.keys()) == {"field", "rule"}
    assert details["field"] == "latest_bar_time"
    assert details["rule"] == "max_age_exceeded"
