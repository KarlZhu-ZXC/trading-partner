"""Vendor chain configuration port (read-only, market × category → vendors)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from domain.common.enums import DataCategory, Market, VendorId


class VendorChainConfig(Protocol):
    """Explicit, immutable vendor order per market and data category."""

    def chain_for(self, market: Market, category: DataCategory) -> tuple[VendorId, ...]:
        """Return the configured vendor chain, or ``()`` when the category is omitted."""
        ...

    def all_categories(self, market: Market) -> Mapping[DataCategory, tuple[VendorId, ...]]:
        """Return an immutable mapping of categories explicitly declared for ``market``."""
        ...
