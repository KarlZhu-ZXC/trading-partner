"""Category-level provider protocol (Phase 1D D6a).

Category-specific data methods (quote/ohlcv/fundamentals/...) are frozen in
design §9.3 for 1E+; D6a only freezes the shared CategoryProvider surface used
by VendorRegistry and (later) ProviderRouter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.common.enums import DataCategory, Market, VendorId


@runtime_checkable
class CategoryProvider(Protocol):
    """Minimal vendor adapter surface for registry lookup and chain routing."""

    @property
    def vendor_id(self) -> VendorId:
        """Stable VendorId for this adapter."""
        ...

    @property
    def provider_name(self) -> str:
        """Must equal ``vendor_id.value``; used as SourceReference.name."""
        ...

    def supports(self, market: Market, category: DataCategory) -> bool:
        """Return whether this adapter can serve ``market`` + ``category``."""
        ...

    def is_configured(self) -> bool:
        """False when required keys/endpoints are missing.

        Null vendor may return True while still raising ProviderNotConfigured
        on data methods (chain placeholder semantics).
        """
        ...
