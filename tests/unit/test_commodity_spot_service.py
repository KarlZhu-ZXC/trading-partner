"""CommoditySpotService unit tests over a fake provider."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.services.commodity_spot_service import CommoditySpotService
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.cross_asset.enums import OfferSide, PriceBasis, SpotVenueBasis, SpotVolumeBasis
from domain.cross_asset.spot_models import CommoditySpotBarSeries, SpotObservation
from domain.instruments.models import Instrument
from domain.market.models import MarketBar
from domain.us_market.enums import USBarInterval
from infrastructure.system.clock import SystemClock

AS_OF = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)

_XAU = Instrument(
    instrument_id="commodity_spot:OTC:XAUUSD",
    symbol="XAUUSD",
    name="OTC Gold",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.COMMODITY_SPOT,
)
_COPPER = Instrument(
    instrument_id="cfd:OTC:COPPER_CMD_USD",
    symbol="COPPER_CMD_USD",
    name="Copper Rolling CFD",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.CFD,
)
_LIGHT_OIL = Instrument(
    instrument_id="cfd:OTC:LIGHT_CMD_USD",
    symbol="LIGHT_CMD_USD",
    name="Dukascopy Light Oil Rolling CFD (not WTI spot, not a NYMEX future)",
    market=Market.OTC,
    exchange="DUKASCOPY_SWFX",
    currency="USD",
    timezone="UTC",
    asset_type=AssetType.CFD,
)


def _meta(category: DataCategory, warnings: tuple[str, ...]) -> ProviderResultMeta:
    return ProviderResultMeta(
        vendor=VendorId.DUKASCOPY,
        category=category,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.FRESH,
        session=TradingSession.UNKNOWN,
        latency_ms=None,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=warnings,
    )


class _FakeProvider:
    vendor_id = VendorId.DUKASCOPY
    provider_name = VendorId.DUKASCOPY.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.OTC

    def is_configured(self) -> bool:
        return True

    async def get_quote(self, instrument: Instrument, as_of: datetime) -> ProviderSuccess:
        return ProviderSuccess(
            value=SpotObservation(
                instrument_id=instrument.instrument_id,
                currency="USD",
                unit="USD/oz",
                quote_at=as_of,
                venue_basis=SpotVenueBasis.DUKASCOPY_SWFX,
                source="dukascopy",
                bid=Decimal("2348.10"),
                ask=Decimal("2348.40"),
                mid=Decimal("2348.25"),
            ),
            meta=_meta(
                DataCategory.MARKET_QUOTE,
                ("DUKASCOPY_SWFX_NOT_LBMA", "ROLLING_CFD_NOT_SPOT")
                if instrument.asset_type is AssetType.CFD
                else ("DUKASCOPY_SWFX_NOT_LBMA",),
            ),
        )

    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
        offer_side: OfferSide = OfferSide.BID,
    ) -> ProviderSuccess:
        bar = MarketBar(
            timestamp=datetime(2026, 7, 23, 15, 0, tzinfo=UTC),
            open=Decimal("32.1"),
            high=Decimal("32.5"),
            low=Decimal("32.0"),
            close=Decimal("32.4"),
            volume=Decimal("10"),
        )
        return ProviderSuccess(
            value=CommoditySpotBarSeries(
                instrument_id=instrument.instrument_id,
                interval=interval,
                offer_side=offer_side,
                start=start,
                end=end,
                adjustment=adjustment,
                bars=(bar,),
                volume_basis=SpotVolumeBasis.BEST_BID_ASK_VOLUME,
            ),
            meta=_meta(
                DataCategory.MARKET_OHLCV,
                ("VOLUME_BEST_BID_ASK_NOT_EXCHANGE",),
            ),
        )


class _FixedClock(SystemClock):
    def now(self) -> datetime:  # type: ignore[override]
        return AS_OF


@pytest.mark.asyncio
async def test_service_quote_xau_and_copper_warnings() -> None:
    service = CommoditySpotService(provider=_FakeProvider(), clock=_FixedClock())
    xau = await service.get_quote(_XAU, as_of=AS_OF)
    assert xau.ok is True
    assert xau.data is not None
    assert xau.data.instrument_id == "commodity_spot:OTC:XAUUSD"
    assert xau.data.venue_basis is SpotVenueBasis.DUKASCOPY_SWFX
    assert xau.data.display_price == Decimal("2348.25")
    assert xau.data.price_basis is PriceBasis.MID
    assert any(w.code == "DUKASCOPY_SWFX_NOT_LBMA" for w in xau.warnings)
    assert not any(w.code == "ROLLING_CFD_NOT_SPOT" for w in xau.warnings)

    copper = await service.get_quote(_COPPER, as_of=AS_OF)
    assert copper.ok is True
    assert copper.data is not None
    assert copper.data.instrument_id == "cfd:OTC:COPPER_CMD_USD"
    assert any(w.code == "ROLLING_CFD_NOT_SPOT" for w in copper.warnings)
    copper_warning = next(w for w in copper.warnings if w.code == "ROLLING_CFD_NOT_SPOT")
    assert "copper" in copper_warning.message.lower()
    assert "light-oil" not in copper_warning.message.lower()
    assert not any(w.code == "DUKASCOPY_SWFX_NOT_LBMA" for w in copper.warnings)

    oil = await service.get_quote(_LIGHT_OIL, as_of=AS_OF)
    assert oil.ok is True
    oil_warning = next(w for w in oil.warnings if w.code == "ROLLING_CFD_NOT_SPOT")
    oil_message = oil_warning.message.lower()
    assert "dukascopy otc rolling light-oil cfd" in oil_message
    assert "not wti spot" in oil_message
    assert "nymex cl" in oil_message
    assert "specific futures contract" in oil_message
    assert "continuous futures series" in oil_message
    assert not any(w.code == "DUKASCOPY_SWFX_NOT_LBMA" for w in oil.warnings)


@pytest.mark.asyncio
async def test_service_bars_series_dto() -> None:
    service = CommoditySpotService(provider=_FakeProvider(), clock=_FixedClock())
    result = await service.get_bars(
        _XAU,
        start=date(2026, 7, 23),
        end=date(2026, 7, 23),
        interval=USBarInterval.SIXTY_MINUTES,
        as_of=AS_OF,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data.volume_basis is SpotVolumeBasis.BEST_BID_ASK_VOLUME
    assert result.data.offer_side is OfferSide.BID
    assert len(result.data.bars) == 1
