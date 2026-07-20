"""MockMarketSnapshotCoordinator tests (Phase 1D D8b: single routed_service)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from application.dto.instrument import InstrumentDTO
from application.dto.market import (
    MarketBarDTO,
    TechnicalIndicatorsDTO,
    VerifiedMarketSnapshotDTO,
)
from application.dto.tool_envelope import ErrorInfo, ToolEnvelope, WarningInfo
from application.services.mock_instrument_resolver import MockInstrumentResolver
from application.services.mock_market_snapshot_coordinator import (
    MockMarketSnapshotCoordinator,
)
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AdjustmentMethod,
    Freshness,
    Market,
    TradingSession,
)
from domain.instruments.models import Instrument
from domain.market.models import TechnicalIndicators
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
MOCK_WARNING = WarningInfo(
    code="MOCK_DATA",
    message="Response contains deterministic mock data.",
    details={},
)


def _minimal_dto(instrument: Instrument) -> VerifiedMarketSnapshotDTO:
    return VerifiedMarketSnapshotDTO(
        instrument=InstrumentDTO.from_domain(instrument),
        requested_as_of=AS_OF,
        latest_market_row=MarketBarDTO(
            timestamp=AS_OF,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        ),
        indicators=TechnicalIndicatorsDTO.from_domain(TechnicalIndicators.empty()),
        recent_closes=(Decimal("1"),),
        adjustment=AdjustmentMethod.NONE,
        session=TradingSession.CLOSED,
        algorithm_version="test-1.0.0",
    )


class _FakeRoutedService:
    """Minimal stand-in for RoutedMarketSnapshotService."""

    def __init__(self) -> None:
        self.calls: list[tuple[Instrument, datetime]] = []

    async def get_snapshot(
        self, instrument: Instrument, as_of: datetime
    ) -> ToolEnvelope[VerifiedMarketSnapshotDTO]:
        self.calls.append((instrument, as_of))
        return ToolEnvelope.success(
            request_id="req_test",
            market=instrument.market,
            as_of=as_of,
            fetched_at=as_of,
            freshness=Freshness.FRESH,
            sources=(),
            data=_minimal_dto(instrument),
            degraded=True,
            warnings=(MOCK_WARNING,),
        )


def _coordinator(
    routed: _FakeRoutedService | None = None,
) -> tuple[MockMarketSnapshotCoordinator, _FakeRoutedService]:
    clock = FixedClock()
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()
    fake = routed or _FakeRoutedService()
    coord = MockMarketSnapshotCoordinator(
        resolver=MockInstrumentResolver(),
        routed_service=fake,  # type: ignore[arg-type]
        clock=clock,
        id_generator=ids,
        secret_redactor=redactor,
    )
    return coord, fake


@pytest.mark.asyncio
async def test_coordinator_success_us_delegates_to_routed() -> None:
    c, routed = _coordinator()
    env = await c.get_snapshot(Market.US, "NVDA", AS_OF)
    assert env.ok is True
    assert env.data is not None
    assert env.data.instrument.symbol == "NVDA"
    assert len(routed.calls) == 1
    instrument, as_of = routed.calls[0]
    assert instrument.symbol == "NVDA"
    assert instrument.market is Market.US
    assert as_of == AS_OF


@pytest.mark.asyncio
async def test_coordinator_success_a_share_delegates_to_routed() -> None:
    c, routed = _coordinator()
    env = await c.get_snapshot(Market.A_SHARE, "600519.SH", AS_OF)
    assert env.ok is True
    assert env.data is not None
    assert env.data.instrument.symbol == "600519.SH"
    assert len(routed.calls) == 1
    instrument, _ = routed.calls[0]
    assert instrument.symbol == "600519.SH"
    assert instrument.market is Market.A_SHARE


@pytest.mark.asyncio
async def test_coordinator_invalid_instrument() -> None:
    c, routed = _coordinator()
    env = await c.get_snapshot(Market.US, "AAPL", AS_OF)
    assert env.ok is False
    assert env.errors[0].code == "INVALID_INSTRUMENT"
    assert env.data is None
    # Must not call routed service on resolve failure.
    assert routed.calls == []


@pytest.mark.asyncio
async def test_coordinator_accepts_single_routed_service_ctor() -> None:
    """Constructor surface: one routed_service (no dual MarketSnapshotService)."""
    clock = FixedClock()
    ids = SequentialIdGenerator()
    redactor = DefaultSecretRedactor()
    mock_routed = AsyncMock()
    mock_routed.get_snapshot = AsyncMock(
        return_value=ToolEnvelope.failure(
            request_id="req_x",
            market=Market.US,
            as_of=AS_OF,
            fetched_at=AS_OF,
            freshness=Freshness.UNKNOWN,
            errors=[
                ErrorInfo(
                    code="NO_MARKET_DATA",
                    message="stub",
                    details={},
                    retryable=False,
                )
            ],
        )
    )
    coord = MockMarketSnapshotCoordinator(
        resolver=MockInstrumentResolver(),
        routed_service=mock_routed,
        clock=clock,
        id_generator=ids,
        secret_redactor=redactor,
    )
    env = await coord.get_snapshot(Market.US, "NVDA", AS_OF)
    assert env.ok is False
    mock_routed.get_snapshot.assert_awaited_once()
