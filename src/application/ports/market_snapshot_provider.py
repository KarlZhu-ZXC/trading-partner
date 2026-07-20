"""Market snapshot provider port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import Market
from domain.instruments.models import Instrument
from domain.market.models import VerifiedMarketSnapshot


class MarketSnapshotProvider(Protocol):
    @property
    def provider_name(self) -> str:
        """Stable provider name used in SourceReference."""
        ...

    def supports(self, market: Market) -> bool:
        """Return whether this provider handles the given market."""
        ...

    async def get_snapshot(
        self,
        instrument: Instrument,
        as_of: datetime,
    ) -> VerifiedMarketSnapshot:
        """Fetch a verified market snapshot for the instrument as of a point in time."""
        ...
