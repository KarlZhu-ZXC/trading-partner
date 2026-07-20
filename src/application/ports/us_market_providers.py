"""US market CategoryProvider protocol surfaces (Phase 1F F1).

All protocols are ``@runtime_checkable`` and extend ``CategoryProvider``.
Router callbacks must narrow with ``isinstance`` — no getattr/reflection.

Quote and OHLCV are the only provider protocols in this slice; context and
technical snapshots are service-layer compositions over these surfaces.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.common.enums import AdjustmentMethod
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from domain.us_market.models import USBarSeries, USBreadthSnapshot, USQuote


@runtime_checkable
class USQuoteProvider(CategoryProvider, Protocol):
    async def get_quote(
        self, instrument: Instrument, as_of: datetime
    ) -> ProviderSuccess[USQuote]: ...


@runtime_checkable
class USBarsProvider(CategoryProvider, Protocol):
    async def get_bars(
        self,
        instrument: Instrument,
        *,
        start: date,
        end: date,
        interval: USBarInterval,
        adjustment: AdjustmentMethod,
        as_of: datetime,
    ) -> ProviderSuccess[USBarSeries]: ...


@runtime_checkable
class USMarketBreadthProvider(CategoryProvider, Protocol):
    async def get_market_breadth(
        self, *, as_of: datetime
    ) -> ProviderSuccess[USBreadthSnapshot]: ...


# Explicit inventory for architecture / completeness tests (order frozen).
US_MARKET_RUNTIME_PROTOCOLS: tuple[type, ...] = (
    USQuoteProvider,
    USBarsProvider,
    USMarketBreadthProvider,
)
