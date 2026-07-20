"""Contract tests for MockUSMarketSnapshotProvider."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain.common.enums import Market
from domain.common.errors import InvalidInstrument
from domain.instruments.models import Instrument
from infrastructure.providers.us.mock_market import MockUSMarketSnapshotProvider


def test_get_snapshot_is_async() -> None:
    provider = MockUSMarketSnapshotProvider()
    assert inspect.iscoroutinefunction(provider.get_snapshot)


def test_provider_name_and_supports() -> None:
    provider = MockUSMarketSnapshotProvider()
    assert provider.provider_name == "mock_us"
    assert provider.supports(Market.US) is True
    assert provider.supports(Market.A_SHARE) is False


@pytest.mark.asyncio
async def test_deterministic_fixture(us_instrument: Instrument) -> None:
    provider = MockUSMarketSnapshotProvider()
    as_of = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
    snap = await provider.get_snapshot(us_instrument, as_of)
    assert snap.instrument.instrument_id == "equity:US:NVDA"
    assert snap.instrument.exchange == "NASDAQ"
    assert snap.requested_as_of == as_of
    assert snap.latest_market_row.timestamp == as_of
    assert snap.latest_market_row.open == Decimal("170.00")
    assert snap.latest_market_row.high == Decimal("175.00")
    assert snap.latest_market_row.low == Decimal("168.00")
    assert snap.latest_market_row.close == Decimal("173.00")
    assert snap.latest_market_row.volume == Decimal("50000000")
    assert snap.recent_closes == (
        Decimal("168.00"),
        Decimal("169.50"),
        Decimal("171.00"),
        Decimal("172.00"),
        Decimal("173.00"),
    )
    assert snap.algorithm_version == "mock-1.0.0"
    assert snap.session.value == "regular"
    assert snap.adjustment.value == "none"
    assert snap.indicators.rsi_14 is None


@pytest.mark.asyncio
async def test_rejects_wrong_symbol(us_instrument: Instrument) -> None:
    provider = MockUSMarketSnapshotProvider()
    bad = Instrument(
        instrument_id="equity:US:AAPL",
        symbol="AAPL",
        name="Apple",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=us_instrument.asset_type,
    )
    with pytest.raises(InvalidInstrument):
        await provider.get_snapshot(bad, datetime(2026, 7, 16, tzinfo=UTC))
