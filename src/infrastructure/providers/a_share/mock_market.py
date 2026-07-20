"""Deterministic A-share mock market snapshot provider."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from domain.common.enums import AdjustmentMethod, Market, TradingSession
from domain.common.errors import InvalidInstrument
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.market.models import (
    MarketBar,
    TechnicalIndicators,
    VerifiedMarketSnapshot,
)

_EXPECTED_ID = "equity:A_SHARE:600519.SH"
_ALGORITHM_VERSION = "mock-1.0.0"


class MockAShareMarketSnapshotProvider:
    """In-memory deterministic mock for 600519.SH only."""

    @property
    def provider_name(self) -> str:
        return "mock_a_share"

    def supports(self, market: Market) -> bool:
        return market is Market.A_SHARE

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> VerifiedMarketSnapshot:
        require_aware_datetime(as_of, field_name="as_of")
        if instrument.market is not Market.A_SHARE:
            raise InvalidInstrument(
                "MockAShareMarketSnapshotProvider only supports A_SHARE",
                details={"market": instrument.market.value},
            )
        if instrument.instrument_id != _EXPECTED_ID and instrument.symbol != "600519.SH":
            raise InvalidInstrument(
                f"Mock A-share provider does not support symbol={instrument.symbol!r}",
                details={"symbol": instrument.symbol},
            )

        bar = MarketBar(
            timestamp=as_of,
            open=Decimal("1500.00"),
            high=Decimal("1510.00"),
            low=Decimal("1490.00"),
            close=Decimal("1505.00"),
            volume=Decimal("100000"),
        )
        return VerifiedMarketSnapshot(
            instrument=instrument,
            requested_as_of=as_of,
            latest_market_row=bar,
            indicators=TechnicalIndicators.empty(),
            recent_closes=(
                Decimal("1498.00"),
                Decimal("1501.00"),
                Decimal("1496.00"),
                Decimal("1502.00"),
                Decimal("1505.00"),
            ),
            adjustment=AdjustmentMethod.NONE,
            session=TradingSession.CLOSED,
            algorithm_version=_ALGORITHM_VERSION,
        )
