"""Deterministic US mock market snapshot provider."""

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

_EXPECTED_ID = "equity:US:NVDA"
_ALGORITHM_VERSION = "mock-1.0.0"


class MockUSMarketSnapshotProvider:
    """In-memory deterministic mock for NVDA only."""

    @property
    def provider_name(self) -> str:
        return "mock_us"

    def supports(self, market: Market) -> bool:
        return market is Market.US

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> VerifiedMarketSnapshot:
        require_aware_datetime(as_of, field_name="as_of")
        if instrument.market is not Market.US:
            raise InvalidInstrument(
                "MockUSMarketSnapshotProvider only supports US",
                details={"market": instrument.market.value},
            )
        if instrument.instrument_id != _EXPECTED_ID and instrument.symbol != "NVDA":
            raise InvalidInstrument(
                f"Mock US provider does not support symbol={instrument.symbol!r}",
                details={"symbol": instrument.symbol},
            )

        bar = MarketBar(
            timestamp=as_of,
            open=Decimal("170.00"),
            high=Decimal("175.00"),
            low=Decimal("168.00"),
            close=Decimal("173.00"),
            volume=Decimal("50000000"),
        )
        return VerifiedMarketSnapshot(
            instrument=instrument,
            requested_as_of=as_of,
            latest_market_row=bar,
            indicators=TechnicalIndicators.empty(),
            recent_closes=(
                Decimal("168.00"),
                Decimal("169.50"),
                Decimal("171.00"),
                Decimal("172.00"),
                Decimal("173.00"),
            ),
            adjustment=AdjustmentMethod.NONE,
            session=TradingSession.REGULAR,
            algorithm_version=_ALGORITHM_VERSION,
        )
