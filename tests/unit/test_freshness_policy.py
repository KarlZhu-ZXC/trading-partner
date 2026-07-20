"""Phase 1D D7: classify_freshness equality boundaries and contract errors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain.common.enums import Freshness, TradingSession
from domain.common.errors import DataContractError
from domain.market.freshness import classify_freshness

NOW = datetime(2026, 7, 16, 15, 0, 0, tzinfo=UTC)


def _classify(
    *,
    age_seconds: float = 0,
    session: TradingSession = TradingSession.REGULAR,
    max_fresh_seconds: int = 60,
    max_delayed_seconds: int = 900,
    vendor_declared_delay_seconds: int | None = None,
    now: datetime = NOW,
) -> Freshness:
    return classify_freshness(
        now=now,
        data_timestamp=now - timedelta(seconds=age_seconds),
        session=session,
        max_fresh_seconds=max_fresh_seconds,
        max_delayed_seconds=max_delayed_seconds,
        vendor_declared_delay_seconds=vendor_declared_delay_seconds,
    )


def test_fresh_at_zero_age() -> None:
    assert _classify(age_seconds=0) is Freshness.FRESH


def test_fresh_at_exact_max_fresh_boundary() -> None:
    assert _classify(age_seconds=60, max_fresh_seconds=60) is Freshness.FRESH


def test_delayed_just_after_fresh_boundary() -> None:
    assert _classify(age_seconds=60.0001, max_fresh_seconds=60) is Freshness.DELAYED


def test_delayed_at_exact_max_delayed_boundary() -> None:
    assert (
        _classify(age_seconds=900, max_fresh_seconds=60, max_delayed_seconds=900)
        is Freshness.DELAYED
    )


def test_stale_just_after_delayed_boundary() -> None:
    assert (
        _classify(age_seconds=900.0001, max_fresh_seconds=60, max_delayed_seconds=900)
        is Freshness.STALE
    )


def test_vendor_delay_none_or_zero_allows_fresh() -> None:
    assert _classify(age_seconds=10, vendor_declared_delay_seconds=None) is Freshness.FRESH
    assert _classify(age_seconds=10, vendor_declared_delay_seconds=0) is Freshness.FRESH


def test_vendor_delay_positive_never_fresh_even_when_age_fresh() -> None:
    assert _classify(age_seconds=0, vendor_declared_delay_seconds=1) is Freshness.DELAYED
    assert _classify(age_seconds=30, vendor_declared_delay_seconds=15) is Freshness.DELAYED


def test_vendor_delay_positive_still_stale_when_age_exceeds_delayed() -> None:
    assert (
        _classify(
            age_seconds=901,
            max_fresh_seconds=60,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=15,
        )
        is Freshness.STALE
    )


def test_unknown_session_returns_unknown() -> None:
    assert _classify(session=TradingSession.UNKNOWN) is Freshness.UNKNOWN
    # Still UNKNOWN even when age would otherwise be STALE.
    assert (
        _classify(
            age_seconds=10_000,
            session=TradingSession.UNKNOWN,
            max_fresh_seconds=60,
            max_delayed_seconds=900,
        )
        is Freshness.UNKNOWN
    )


def test_future_data_timestamp_raises_without_echo() -> None:
    future = NOW + timedelta(seconds=1)
    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=future,
            session=TradingSession.REGULAR,
            max_fresh_seconds=60,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    err = exc_info.value
    assert err.details.get("rule") == "future_data_timestamp"
    assert err.details.get("field") == "data_timestamp"
    blob = f"{err!s}{err.details!s}"
    assert "2026" not in blob
    assert str(future) not in blob


def test_rejects_naive_now_and_data_timestamp() -> None:
    naive = datetime(2026, 7, 16, 15, 0, 0)
    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=naive,
            data_timestamp=NOW,
            session=TradingSession.REGULAR,
            max_fresh_seconds=60,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("field") == "now"

    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=naive,
            session=TradingSession.REGULAR,
            max_fresh_seconds=60,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("field") == "data_timestamp"


def test_rejects_bool_and_non_int_thresholds() -> None:
    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=NOW,
            session=TradingSession.REGULAR,
            max_fresh_seconds=True,  # type: ignore[arg-type]
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("field") == "max_fresh_seconds"
    assert exc_info.value.details.get("type") == "bool"

    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=NOW,
            session=TradingSession.REGULAR,
            max_fresh_seconds=60,
            max_delayed_seconds=False,  # type: ignore[arg-type]
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("field") == "max_delayed_seconds"

    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=NOW,
            session=TradingSession.REGULAR,
            max_fresh_seconds=60,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=True,  # type: ignore[arg-type]
        )
    assert exc_info.value.details.get("field") == "vendor_declared_delay_seconds"


def test_rejects_negative_thresholds_and_fresh_gt_delayed() -> None:
    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=NOW,
            session=TradingSession.REGULAR,
            max_fresh_seconds=-1,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("rule") == "nonnegative"

    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=NOW,
            session=TradingSession.REGULAR,
            max_fresh_seconds=100,
            max_delayed_seconds=50,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("rule") == "fresh_le_delayed"


def test_rejects_bad_session_type_before_unknown_short_circuit() -> None:
    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=NOW,
            session="unknown",  # type: ignore[arg-type]
            max_fresh_seconds=60,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("field") == "session"
    assert exc_info.value.details.get("rule") == "type"


def test_zero_thresholds_equality() -> None:
    assert (
        _classify(
            age_seconds=0,
            max_fresh_seconds=0,
            max_delayed_seconds=0,
            vendor_declared_delay_seconds=None,
        )
        is Freshness.FRESH
    )
    assert (
        _classify(
            age_seconds=0.0001,
            max_fresh_seconds=0,
            max_delayed_seconds=0,
            vendor_declared_delay_seconds=None,
        )
        is Freshness.STALE
    )


def test_illegal_input_checked_before_unknown_session_return() -> None:
    """Type/threshold errors must fire even when session is UNKNOWN."""
    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=NOW,
            session=TradingSession.UNKNOWN,
            max_fresh_seconds=True,  # type: ignore[arg-type]
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("field") == "max_fresh_seconds"

    future = NOW + timedelta(seconds=5)
    with pytest.raises(DataContractError) as exc_info:
        classify_freshness(
            now=NOW,
            data_timestamp=future,
            session=TradingSession.UNKNOWN,
            max_fresh_seconds=60,
            max_delayed_seconds=900,
            vendor_declared_delay_seconds=None,
        )
    assert exc_info.value.details.get("rule") == "future_data_timestamp"
