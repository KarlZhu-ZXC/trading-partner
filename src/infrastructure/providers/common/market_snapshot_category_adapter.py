"""Adapter wrapping Phase 1A MarketSnapshotProvider as CategoryProvider (D8a).

Bridges the old single-market mock providers into the VendorRegistry /
ProviderRouter surface without changing fixture numerics or mock behaviour.
"""

from __future__ import annotations

from datetime import datetime

from application.ports.market_snapshot_provider import MarketSnapshotProvider
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import ConfigurationError, DataContractError
from domain.instruments.models import Instrument
from domain.market.models import VerifiedMarketSnapshot

_ALLOWED_VENDORS: frozenset[VendorId] = frozenset(
    {VendorId.MOCK_A_SHARE, VendorId.MOCK_US}
)


class MarketSnapshotCategoryAdapter:
    """Explicit Category adapter over a frozen old MarketSnapshotProvider.

    ``provider_name`` is always ``vendor_id.value`` — never the free-text name
    from the underlying mock provider instance.
    """

    def __init__(
        self,
        *,
        vendor_id: VendorId,
        provider: MarketSnapshotProvider,
    ) -> None:
        if not isinstance(vendor_id, VendorId):
            raise ConfigurationError(
                "vendor_id must be a VendorId",
                details={"field": "vendor_id", "type": type(vendor_id).__name__},
            )
        if vendor_id not in _ALLOWED_VENDORS:
            raise ConfigurationError(
                "MarketSnapshotCategoryAdapter only accepts MOCK_A_SHARE or MOCK_US",
                details={
                    "field": "vendor_id",
                    "rule": "allowed_mock_vendors",
                    "vendor_id": vendor_id.value,
                },
            )
        self._vendor_id = vendor_id
        self._provider = provider

    @property
    def vendor_id(self) -> VendorId:
        return self._vendor_id

    @property
    def provider_name(self) -> str:
        """Stable SourceReference.name — always the frozen VendorId wire value."""
        return self._vendor_id.value

    def is_configured(self) -> bool:
        """Mock adapters are always configured."""
        return True

    def supports(self, market: Market, category: DataCategory) -> bool:
        """Support only MARKET_SNAPSHOT when the old provider supports ``market``.

        Delegates to ``provider.supports(market)`` with exception-safe wrapping.
        Non-bool returns raise DataContractError (exact bool only).
        """
        if category is not DataCategory.MARKET_SNAPSHOT:
            return False
        try:
            result = self._provider.supports(market)
        except Exception:
            # Never surface provider exception chains (may contain secrets).
            raise DataContractError(
                "underlying provider.supports failed",
                details={
                    "field": "provider.supports",
                    "rule": "exception_safe",
                },
            ) from None
        # Exact bool only — never truthiness; never echo the raw return value.
        if type(result) is not bool:
            raise DataContractError(
                "provider.supports must return exact bool",
                details={
                    "field": "provider.supports",
                    "rule": "exact_bool",
                },
            )
        return result is True

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> VerifiedMarketSnapshot:
        """Delegate only — no meta, no fixture mutation, no exception swallowing."""
        return await self._provider.get_snapshot(instrument, as_of)
