"""Compact Phase 2D acceptance tests: indicators, structure, and PNG artifact."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from application.dto.technical import TechnicalAnalysisDTO
from domain.common.enums import Market
from domain.market.models import MarketBar
from domain.technical.models import TechnicalAnalysis
from infrastructure.technical import MatplotlibChartRenderer, TALibIndicatorEngine


def _bars(count: int = 260) -> tuple[MarketBar, ...]:
    start = datetime(2025, 1, 2, 21, tzinfo=UTC)
    out: list[MarketBar] = []
    for index in range(count):
        close = Decimal("100") + Decimal(index) / Decimal("5")
        open_ = close - (Decimal("0.4") if index % 2 else Decimal("-0.2"))
        out.append(
            MarketBar(
                timestamp=start + timedelta(days=index),
                open=open_,
                high=max(open_, close) + Decimal("1"),
                low=min(open_, close) - Decimal("1"),
                close=close,
                volume=Decimal(1_000_000 + index * 1_000),
            )
        )
    return tuple(out)


def test_ta_lib_engine_and_png_renderer() -> None:
    bars = _bars()
    analysis = TALibIndicatorEngine().analyze(bars, interval="1d")

    metrics = {metric.name: metric.value for metric in analysis.metrics}
    assert analysis.trend_state == "uptrend"
    assert metrics["rsi_14"] is not None
    assert metrics["adx_14"] is not None
    assert metrics["relative_volume_20"] is not None
    assert metrics["vwma_20"] is not None
    assert analysis.bar_count == 260
    assert all(level.basis == "five_bar_swing_cluster_within_0.75_atr" for level in analysis.levels)

    png = MatplotlibChartRenderer().render(
        instrument_id="equity:US:TEST",
        bars=bars,
        analysis=analysis,
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 10_000

    dto = TechnicalAnalysisDTO.from_domain(
        TechnicalAnalysis(
            instrument_id="equity:US:TEST",
            market=Market.US,
            as_of=bars[-1].timestamp,
            timeframes=(analysis,),
            price_basis="split_and_dividend_adjusted_daily_close",
        )
    )
    assert dto.bar_as_of == analysis.bar_as_of
    assert dto.indicators.rsi_14 == metrics["rsi_14"]
    assert dto.indicators.vwma == metrics["vwma_20"]
