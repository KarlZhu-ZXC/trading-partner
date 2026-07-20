"""Time contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from domain.common.errors import DataContractError
from domain.common.time import ensure_utc, require_aware_datetime
from domain.market.models import MarketBar
from infrastructure.system.clock import SystemClock


def test_require_aware_accepts_aware() -> None:
    dt = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    assert require_aware_datetime(dt) is dt


def test_require_aware_rejects_naive() -> None:
    with pytest.raises(DataContractError):
        require_aware_datetime(datetime(2026, 7, 16, 12, 0))


def test_market_bar_rejects_naive_timestamp() -> None:
    with pytest.raises(DataContractError):
        MarketBar(
            timestamp=datetime(2026, 7, 16, 12, 0),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        )


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None
    # Equivalent to UTC offset 0
    assert now.utcoffset().total_seconds() == 0


def test_ensure_utc() -> None:
    plus8 = timezone(timedelta(hours=8))
    dt = datetime(2026, 7, 16, 20, 0, tzinfo=plus8)
    converted = ensure_utc(dt)
    assert converted.tzinfo == UTC
    assert converted.hour == 12
