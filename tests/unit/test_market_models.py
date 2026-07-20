"""Domain market model tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain.common.enums import AdjustmentMethod, AssetType, Market, TradingSession
from domain.common.errors import DataContractError
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument
from domain.market.models import (
    MarketBar,
    TechnicalIndicators,
    VerifiedMarketSnapshot,
)


def test_instrument_id_format() -> None:
    assert build_instrument_id(AssetType.EQUITY, Market.US, "NVDA") == "equity:US:NVDA"
    assert (
        build_instrument_id(AssetType.EQUITY, Market.A_SHARE, "600519.SH")
        == "equity:A_SHARE:600519.SH"
    )


def test_verified_snapshot_requires_aware_as_of() -> None:
    instrument = Instrument(
        instrument_id="equity:US:NVDA",
        symbol="NVDA",
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    bar = MarketBar(
        timestamp=datetime(2026, 7, 16, tzinfo=UTC),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
    )
    with pytest.raises(DataContractError):
        VerifiedMarketSnapshot(
            instrument=instrument,
            requested_as_of=datetime(2026, 7, 16),
            latest_market_row=bar,
            indicators=TechnicalIndicators.empty(),
            recent_closes=(),
            adjustment=AdjustmentMethod.NONE,
            session=TradingSession.REGULAR,
            algorithm_version="mock-1.0.0",
        )


def test_technical_indicators_empty() -> None:
    ind = TechnicalIndicators.empty()
    assert ind.ema_10 is None
    assert ind.mfi is None
