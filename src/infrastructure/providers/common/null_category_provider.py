"""Null vendor adapter — explicit YAML ``null`` chain placeholder (Phase 1D D9).

Registered under ``VendorId.NULL`` so config chains that list ``null`` do not
depend on accidental registry misses. Identity/support/config are always
reachable; every data method raises typed ``ProviderNotConfigured``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from application.dto.provider_routing import ProviderSuccess
from domain.common.enums import AdjustmentMethod, DataCategory, Market, VendorId
from domain.common.errors import ProviderNotConfigured
from domain.instruments.models import Instrument
from domain.market.models import MarketBar, VerifiedMarketSnapshot


class NullCategoryProvider:
    """Chain placeholder for explicit ``null`` vendors (design §9.4).

    ``supports`` and ``is_configured`` are always True so the Router reaches
    data methods; those methods raise ``ProviderNotConfigured`` without
    unknown-error wrapping or secret-bearing details.
    """

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.NULL

    @property
    def provider_name(self) -> str:
        """Stable SourceReference.name — always ``VendorId.NULL.value``."""
        return VendorId.NULL.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        """Null is a universal placeholder; data methods always refuse."""
        del market, category
        return True

    def is_configured(self) -> bool:
        """True: placeholder is intentionally present (not a missing key)."""
        return True

    @staticmethod
    def _not_configured() -> ProviderNotConfigured:
        return ProviderNotConfigured(
            "null vendor is a chain placeholder",
            details={"vendor": VendorId.NULL.value},
        )

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> VerifiedMarketSnapshot:
        del instrument, as_of
        raise self._not_configured()

    async def get_quote(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[MarketBar]:
        del instrument, as_of
        raise self._not_configured()

    async def get_ohlcv(
        self,
        instrument: Instrument,
        *,
        start: datetime,
        end: datetime,
        as_of: datetime,
        adjustment: AdjustmentMethod,
    ) -> ProviderSuccess[tuple[MarketBar, ...]]:
        del instrument, start, end, as_of, adjustment
        raise self._not_configured()

    async def get_fundamentals(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[Mapping[str, object]]:
        del instrument, as_of
        raise self._not_configured()

    async def list_filings(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Mapping[str, object], ...]]:
        del instrument, as_of
        raise self._not_configured()

    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Mapping[str, object], ...]]:
        del instrument, start, end, as_of
        raise self._not_configured()

    async def get_macro_series(
        self,
        series_id: str,
        as_of: datetime,
    ) -> ProviderSuccess[Mapping[str, object]]:
        del series_id, as_of
        raise self._not_configured()

    async def get_sentiment(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> ProviderSuccess[Mapping[str, object]]:
        del instrument, as_of
        raise self._not_configured()

    async def get_account_snapshot(
        self,
        as_of: datetime,
    ) -> ProviderSuccess[Mapping[str, object]]:
        del as_of
        raise self._not_configured()

    async def lookup(
        self,
        market: Market,
        query: str,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Instrument, ...]]:
        del market, query, as_of
        raise self._not_configured()
