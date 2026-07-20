"""Market-snapshot category provider protocol (Phase 1D D8a).

Extends :class:`~application.ports.category_provider.CategoryProvider` with the
typed ``get_snapshot`` data method. Callers must use this runtime-checkable
Protocol (no reflection / ``getattr`` dispatch).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from application.ports.category_provider import CategoryProvider
from domain.instruments.models import Instrument
from domain.market.models import VerifiedMarketSnapshot


@runtime_checkable
class MarketSnapshotCategoryProvider(CategoryProvider, Protocol):
    """CategoryProvider that can serve MARKET_SNAPSHOT via get_snapshot."""

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> VerifiedMarketSnapshot:
        """Fetch a verified market snapshot for ``instrument`` as of ``as_of``."""
        ...
