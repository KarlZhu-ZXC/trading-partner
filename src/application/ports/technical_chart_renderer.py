"""Port for rendering a technical-analysis chart artifact."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from domain.market.models import MarketBar
from domain.technical.models import TechnicalTimeframe


class TechnicalChartRenderer(Protocol):
    def render(
        self,
        *,
        instrument_id: str,
        bars: Sequence[MarketBar],
        analysis: TechnicalTimeframe,
    ) -> bytes: ...
