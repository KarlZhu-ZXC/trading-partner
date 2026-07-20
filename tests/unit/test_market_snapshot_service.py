"""MarketSnapshotService unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from application.services.market_snapshot_service import MarketSnapshotService
from conftest import FixedClock, SequentialIdGenerator
from domain.instruments.models import Instrument
from infrastructure.providers.a_share.mock_market import (
    MockAShareMarketSnapshotProvider,
)
from infrastructure.providers.us.mock_market import MockUSMarketSnapshotProvider
from infrastructure.system.redactor import DefaultSecretRedactor


@pytest.mark.asyncio
async def test_us_snapshot_envelope(
    us_instrument: Instrument,
    fixed_clock: FixedClock,
    id_generator: SequentialIdGenerator,
) -> None:
    service = MarketSnapshotService(
        provider=MockUSMarketSnapshotProvider(),
        clock=fixed_clock,
        id_generator=id_generator,
        secret_redactor=DefaultSecretRedactor(),
    )
    as_of = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
    envelope = await service.get_snapshot(us_instrument, as_of)
    assert envelope.ok is True
    assert envelope.degraded is True
    assert envelope.warnings[0].code == "MOCK_DATA"
    assert envelope.freshness == "fresh" or envelope.freshness.value == "fresh"  # type: ignore[union-attr]
    assert envelope.data is not None
    assert envelope.data.instrument.symbol == "NVDA"
    assert envelope.sources[0].name == "mock_us"
    assert envelope.as_of == as_of


@pytest.mark.asyncio
async def test_a_share_snapshot_envelope(
    a_share_instrument: Instrument,
    fixed_clock: FixedClock,
    id_generator: SequentialIdGenerator,
) -> None:
    service = MarketSnapshotService(
        provider=MockAShareMarketSnapshotProvider(),
        clock=fixed_clock,
        id_generator=id_generator,
        secret_redactor=DefaultSecretRedactor(),
    )
    as_of = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    envelope = await service.get_snapshot(a_share_instrument, as_of)
    assert envelope.ok is True
    assert envelope.data is not None
    assert envelope.data.latest_market_row.close == __import__("decimal").Decimal("1505.00")
    assert envelope.sources[0].name == "mock_a_share"
