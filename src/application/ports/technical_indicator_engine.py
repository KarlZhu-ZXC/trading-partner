"""Port for a deterministic technical-indicator calculation backend."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from domain.market.models import MarketBar
from domain.technical.models import TechnicalTimeframe


class TechnicalIndicatorEngine(Protocol):
    def analyze(self, bars: Sequence[MarketBar], *, interval: str) -> TechnicalTimeframe: ...
