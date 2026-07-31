"""Focused technical bar-loading paths for one CME and one OTC instrument."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.cross_asset import CommoditySpotBarSeriesDTO
from application.dto.market import MarketBarDTO
from application.dto.provider_routing import (
    ProviderResultMeta,
    RouterExecutionResult,
)
from application.dto.tool_envelope import WarningInfo
from application.services.commodity_spot_service import CommoditySpotBarsResult
from application.services.instrument_access_service import InstrumentAccessService
from application.services.technical_tool_coordinator import TechnicalToolCoordinator
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.cross_asset.enums import OfferSide, SpotVolumeBasis
from domain.instruments.models import Instrument
from domain.market.models import MarketBar
from domain.technical.models import TechnicalTimeframe
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries
from infrastructure.system.redactor import DefaultSecretRedactor

AS_OF = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)

_CME = Instrument(
    instrument_id="future:CME:GCZ26",
    symbol="GCZ26",
    name="Gold 2026-12",
    market=Market.CME,
    exchange="COMEX",
    currency="USD",
    timezone="America/New_York",
    asset_type=AssetType.FUTURE,
    multiplier=Decimal("100"),
    tick_size=Decimal("0.1"),
)
_OTC = Instrument(
    instrument_id="commodity_spot:OTC:XAUUSD",
    symbol="XAUUSD",
    name="OTC Gold",
    market=Market.OTC,
    exchange="SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.COMMODITY_SPOT,
)
_DCE = Instrument(
    instrument_id="future:DCE:LH2609",
    symbol="LH2609",
    name="Live Hogs 2026-09",
    market=Market.DCE,
    exchange="DCE",
    currency="CNY",
    timezone="Asia/Shanghai",
    asset_type=AssetType.FUTURE,
    multiplier=Decimal("16"),
    tick_size=Decimal("5"),
)


def _daily_bars(n: int = 80) -> tuple[MarketBar, ...]:
    start = date(2026, 1, 1)
    out: list[MarketBar] = []
    for i in range(n):
        day = start + timedelta(days=i)
        ts = datetime(day.year, day.month, day.day, 16, 0, tzinfo=UTC)
        px = Decimal("100") + Decimal(i)
        out.append(
            MarketBar(
                timestamp=ts,
                open=px,
                high=px + Decimal("1"),
                low=px - Decimal("1"),
                close=px,
                volume=Decimal("10"),
            )
        )
    return tuple(out)


def _timeframe(interval: str = "1d") -> TechnicalTimeframe:
    return TechnicalTimeframe(
        interval=interval,
        bar_as_of=AS_OF,
        bar_count=80,
        trend_state="unknown",
        momentum_state="unknown",
        volatility_state="unknown",
        volume_state="unknown",
        metrics=(),
        levels=(),
        patterns=(),
    )


def _coordinator(
    *,
    instruments: dict[str, Instrument],
    us: MagicMock | None = None,
    commodity: MagicMock | None = None,
) -> TechnicalToolCoordinator:
    master = MagicMock()
    master.get.side_effect = lambda iid: instruments[iid]
    indicator = MagicMock()
    indicator.analyze.side_effect = lambda bars, interval: _timeframe(interval)
    return TechnicalToolCoordinator(
        instrument_access=InstrumentAccessService(master, MagicMock()),
        clock=FixedClock(AS_OF),
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
        us_data_service=us or MagicMock(),
        a_share_data_service=MagicMock(),
        indicator_engine=indicator,
        chart_renderer=MagicMock(),
        commodity_spot_service=commodity,
    )


@pytest.mark.asyncio
async def test_technical_snapshot_cme_uses_unadjusted_us_bars() -> None:
    bars = _daily_bars()
    series = USBarSeries(
        instrument_id=_CME.instrument_id,
        interval=USBarInterval.ONE_DAY,
        start=bars[0].timestamp.date(),
        end=bars[-1].timestamp.date(),
        adjustment=AdjustmentMethod.NONE,
        bars=bars,
    )
    us = MagicMock()
    us.get_bars = AsyncMock(
        return_value=RouterExecutionResult(
            value=series,
            ok=True,
            criticality=DataCriticality.CORE,
            meta=ProviderResultMeta(
                vendor=VendorId.YFINANCE,
                category=DataCategory.MARKET_OHLCV,
                role=SourceRole.PRIMARY,
                as_of=AS_OF,
                fetched_at=AS_OF,
                freshness=Freshness.DELAYED,
                session=TradingSession.UNKNOWN,
                latency_ms=1,
                cache_disposition=CacheDisposition.MISS,
                adjustment=AdjustmentMethod.NONE,
                data_delay_seconds=600,
                warnings=("FUTURES_CONTRACT_NOT_SPOT",),
            ),
            attempts=(),
            warnings=(),
            error=None,
        )
    )
    coord = _coordinator(instruments={_CME.instrument_id: _CME}, us=us)
    from application.dto.technical import TechnicalAnalysisInput

    env = await coord.get_snapshot(
        TechnicalAnalysisInput(instrument_id=_CME.instrument_id, as_of=AS_OF)
    )

    assert env.ok is True
    assert env.market == Market.CME.value
    assert env.data is not None
    kwargs = us.get_bars.await_args.kwargs
    assert kwargs["adjustment"] is AdjustmentMethod.NONE
    assert kwargs["interval"] is USBarInterval.ONE_DAY
    # price_basis is on domain analysis via DTO
    assert env.data.price_basis == "unadjusted_specific_futures_close"


@pytest.mark.asyncio
async def test_technical_snapshot_otc_uses_commodity_spot_unadjusted() -> None:
    bars = _daily_bars()
    series = CommoditySpotBarSeriesDTO(
        instrument_id=_OTC.instrument_id,
        interval=USBarInterval.ONE_DAY,
        offer_side=OfferSide.BID,
        start=bars[0].timestamp.date(),
        end=bars[-1].timestamp.date(),
        adjustment=AdjustmentMethod.NONE,
        bars=tuple(
            MarketBarDTO(
                timestamp=b.timestamp,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in bars
        ),
        volume_basis=SpotVolumeBasis.BEST_BID_ASK_VOLUME,
    )
    commodity = MagicMock()
    commodity.get_bars = AsyncMock(
        return_value=CommoditySpotBarsResult(
            ok=True,
            data=series,
            warnings=(WarningInfo(code="DUKASCOPY_SWFX_NOT_LBMA", message="not lbma"),),
            error=None,
            meta=ProviderResultMeta(
                vendor=VendorId.DUKASCOPY,
                category=DataCategory.MARKET_OHLCV,
                role=SourceRole.PRIMARY,
                as_of=AS_OF,
                fetched_at=AS_OF,
                freshness=Freshness.DELAYED,
                session=TradingSession.UNKNOWN,
                latency_ms=1,
                cache_disposition=CacheDisposition.MISS,
                adjustment=AdjustmentMethod.NONE,
                data_delay_seconds=600,
                warnings=("DUKASCOPY_SWFX_NOT_LBMA",),
            ),
        )
    )
    coord = _coordinator(
        instruments={_OTC.instrument_id: _OTC},
        commodity=commodity,
    )
    from application.dto.technical import TechnicalAnalysisInput

    env = await coord.get_snapshot(
        TechnicalAnalysisInput(instrument_id=_OTC.instrument_id, as_of=AS_OF)
    )

    assert env.ok is True
    assert env.market == Market.OTC.value
    assert env.data is not None
    assert env.data.price_basis == "unadjusted_otc_broker_daily_close"
    kwargs = commodity.get_bars.await_args.kwargs
    assert kwargs["adjustment"] is AdjustmentMethod.NONE


@pytest.mark.asyncio
async def test_technical_snapshot_dce_is_typed_unavailable() -> None:
    coord = _coordinator(instruments={_DCE.instrument_id: _DCE})
    from application.dto.technical import TechnicalAnalysisInput

    env = await coord.get_snapshot(
        TechnicalAnalysisInput(instrument_id=_DCE.instrument_id, as_of=AS_OF)
    )

    assert env.ok is False
    assert any(err.code == "NO_MARKET_DATA" for err in env.errors)
