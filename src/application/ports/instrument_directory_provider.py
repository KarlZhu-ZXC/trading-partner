"""External instrument discovery port.

The local Instrument Master remains the authority/cache. Directory providers
only discover and validate candidates; the application service decides whether
they are safe to persist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.common.enums import AssetType, Market
from domain.instruments.models import Instrument


@runtime_checkable
class InstrumentDirectoryProvider(CategoryProvider, Protocol):
    async def lookup(
        self,
        *,
        market: Market,
        query: str,
        asset_type_hint: AssetType | None,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[Instrument, ...]]: ...
