"""Technical adapters defer numerical/plotting imports until explicitly used."""

from __future__ import annotations

from collections.abc import Sequence

from domain.common.errors import ProviderNotConfigured
from domain.market.models import MarketBar
from domain.technical.models import TechnicalTimeframe

__all__ = ["MatplotlibChartRenderer", "TALibIndicatorEngine"]


class TALibIndicatorEngine:
    def analyze(self, bars: Sequence[MarketBar], *, interval: str) -> TechnicalTimeframe:
        try:
            from infrastructure.technical.ta_lib_indicator_engine import (
                TALibIndicatorEngine as Engine,
            )
        except ModuleNotFoundError as error:
            if error.name not in {"talib", "numpy"}:
                raise
            raise ProviderNotConfigured(
                "Technical analysis is unavailable; install trading-partner[technical]",
                details={"extra": "technical", "operation": "technical_analysis"},
            ) from None

        return Engine().analyze(bars, interval=interval)


class MatplotlibChartRenderer:
    def render(
        self, *, instrument_id: str, bars: Sequence[MarketBar], analysis: TechnicalTimeframe
    ) -> bytes:
        try:
            from infrastructure.technical.matplotlib_chart_renderer import (
                MatplotlibChartRenderer as Renderer,
            )
        except ModuleNotFoundError as error:
            if error.name not in {"talib", "numpy"}:
                raise
            raise ProviderNotConfigured(
                "PNG chart rendering is unavailable; install trading-partner[chart]",
                details={"extra": "chart", "operation": "technical_chart"},
            ) from None

        return Renderer().render(instrument_id=instrument_id, bars=bars, analysis=analysis)
