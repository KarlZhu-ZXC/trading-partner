"""Explicit 1E/1F vendor stub before real adapters exist (Phase 1D D9).

Not registered by default bootstrap for real vendors — only for tests or
future explicit composition. Every data method raises typed
``ProviderNotConfigured`` (never unknown-error wrapping / secret leakage).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from application.dto.provider_routing import ProviderSuccess
from domain.common.enums import AdjustmentMethod, DataCategory, Market, VendorId
from domain.common.errors import ConfigurationError, ProviderNotConfigured
from domain.instruments.models import Instrument
from domain.market.models import MarketBar


class UnimplementedVendorAdapter:
    """Stub adapter for a frozen VendorId not yet implemented in Phase 1D.

    Use ``NullCategoryProvider`` for ``VendorId.NULL``; this class rejects NULL.
    """

    def __init__(self, vendor_id: VendorId) -> None:
        if not isinstance(vendor_id, VendorId):
            raise ConfigurationError(
                "vendor_id must be a VendorId",
                details={
                    "field": "vendor_id",
                    "type": type(vendor_id).__name__,
                },
            )
        if vendor_id is VendorId.NULL:
            raise ConfigurationError(
                "use NullCategoryProvider for null vendor",
                details={
                    "field": "vendor_id",
                    "rule": "null_use_null_provider",
                    "vendor_id": vendor_id.value,
                },
            )
        self._vendor_id = vendor_id

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        """Stable SourceReference.name — always ``vendor_id.value``."""
        return self._vendor_id.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        """Stub is reachable for any market/category; data methods refuse."""
        del market, category
        return True

    def is_configured(self) -> bool:
        """True: stub is explicitly assembled (not a missing secret/key)."""
        return True

    def _not_configured(self) -> ProviderNotConfigured:
        return ProviderNotConfigured(
            f"Vendor {self._vendor_id.value} not implemented in Phase 1D",
            details={"phase": "1D", "vendor": self._vendor_id.value},
        )

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
