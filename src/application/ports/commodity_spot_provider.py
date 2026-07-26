"""Provider port for commodity spot / OTC quote and bars."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.common.enums import AdjustmentMethod
from domain.cross_asset.enums import OfferSide
from domain.cross_asset.spot_models import CommoditySpotBarSeries, SpotObservation
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval


@runtime_checkable
class CommoditySpotProvider(CategoryProvider, Protocol):
    async def get_quote(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[SpotObservation]: ...

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
    ) -> ProviderSuccess[CommoditySpotBarSeries]: ...


COMMODITY_SPOT_RUNTIME_PROTOCOLS: tuple[type, ...] = (CommoditySpotProvider,)
