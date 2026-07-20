"""Contract tests for MockAShareMarketSnapshotProvider."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain.common.enums import Market
from domain.common.errors import InvalidInstrument
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.mock_market import (
    MockAShareMarketSnapshotProvider,
)


def test_get_snapshot_is_async() -> None:
    provider = MockAShareMarketSnapshotProvider()
    assert inspect.iscoroutinefunction(provider.get_snapshot)


def test_provider_name_and_supports() -> None:
    provider = MockAShareMarketSnapshotProvider()
    assert provider.provider_name == "mock_a_share"
    assert provider.supports(Market.A_SHARE) is True
    assert provider.supports(Market.US) is False


@pytest.mark.asyncio
async def test_deterministic_fixture(a_share_instrument: Instrument) -> None:
    provider = MockAShareMarketSnapshotProvider()
    as_of = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
    snap = await provider.get_snapshot(a_share_instrument, as_of)
    assert snap.instrument.instrument_id == "equity:A_SHARE:600519.SH"
    assert snap.instrument.exchange == "SSE"
    assert snap.requested_as_of == as_of
    assert snap.latest_market_row.timestamp == as_of
    assert snap.latest_market_row.open == Decimal("1500.00")
    assert snap.latest_market_row.high == Decimal("1510.00")
    assert snap.latest_market_row.low == Decimal("1490.00")
    assert snap.latest_market_row.close == Decimal("1505.00")
    assert snap.latest_market_row.volume == Decimal("100000")
    assert snap.recent_closes == (
        Decimal("1498.00"),
        Decimal("1501.00"),
        Decimal("1496.00"),
        Decimal("1502.00"),
        Decimal("1505.00"),
    )
    assert snap.algorithm_version == "mock-1.0.0"
    assert snap.session.value == "closed"
    assert snap.adjustment.value == "none"
    assert snap.indicators.ema_10 is None


@pytest.mark.asyncio
async def test_rejects_wrong_symbol(a_share_instrument: Instrument) -> None:
    provider = MockAShareMarketSnapshotProvider()
    bad = Instrument(
        instrument_id="equity:A_SHARE:000001.SZ",
        symbol="000001.SZ",
        name="x",
        market=Market.A_SHARE,
        exchange="SZSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=a_share_instrument.asset_type,
    )
    with pytest.raises(InvalidInstrument):
        await provider.get_snapshot(bad, datetime(2026, 7, 16, tzinfo=UTC))
