"""Compact Phase 2D acceptance tests: indicators, structure, and PNG artifact."""

from __future__ import annotations

import builtins
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from application.dto.technical import (
    TechnicalAnalysisDTO,
    TechnicalAnalysisInput,
    TechnicalChartInput,
)
from domain.common.enums import Market
from domain.common.errors import ProviderNotConfigured
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
    assert analysis.smart_money is not None
    assert analysis.smart_money.algorithm_version == "tp_smc_v1"
    assert analysis.smart_money.atr_200_ready is True
    assert analysis.smart_money.limitations == ()
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
    assert dto.algorithm_version == "tp_technical_v3"
    assert dto.timeframes[0].smart_money is not None
    assert dto.bars is None
    assert dto.bars_interval is None


def test_png_renderer_reports_missing_optional_chart_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def without_matplotlib(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "matplotlib" or name.startswith("matplotlib."):
            raise ImportError("optional chart extra is absent")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", without_matplotlib)
    bars = _bars()
    analysis = TALibIndicatorEngine().analyze(bars, interval="1d")

    with pytest.raises(ProviderNotConfigured, match=r"trading-partner\[chart\]"):
        MatplotlibChartRenderer().render(
            instrument_id="equity:US:TEST",
            bars=bars,
            analysis=analysis,
        )


def test_technical_inputs_normalize_interval_aliases_and_publish_wire_enum() -> None:
    analysis = TechnicalAnalysisInput(
        instrument_id="equity:US:TEST",
        intervals=(" DAILY ", "1Wk"),
    )
    chart = TechnicalChartInput(
        instrument_id="equity:US:TEST",
        interval=" weekly ",
    )

    assert analysis.intervals == ("1d", "1w")
    assert chart.interval == "1w"
    assert TechnicalAnalysisInput.model_json_schema()["properties"]["intervals"]["items"][
        "enum"
    ] == ["1d", "1w"]
    assert TechnicalChartInput.model_json_schema()["properties"]["interval"]["enum"] == [
        "1d",
        "1w",
    ]


def test_technical_inputs_reject_unknown_or_duplicate_normalized_intervals() -> None:
    with pytest.raises(ValidationError):
        TechnicalAnalysisInput(
            instrument_id="equity:US:TEST",
            intervals=("daily", "1d"),
        )
    with pytest.raises(ValidationError):
        TechnicalChartInput(
            instrument_id="equity:US:TEST",
            interval="2d",
        )


@pytest.mark.parametrize(
    "limitations, expected",
    [
        (
            ("SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL",),
            ("calendar weeks may be partial", "remain provisional"),
        ),
        (
            ("SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE",),
            ("nonpositive opening prices are unavailable",),
        ),
        (("SMC_ATR_200_UNAVAILABLE_ORDER_BLOCK_FILTER_AND_EQH_EQL",), ("ATR200 is unavailable",)),
        (
            (
                "SMC_CALENDAR_WEEK_BOUNDARIES_MAY_BE_PARTIAL",
                "SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE",
                "SMC_ATR_200_UNAVAILABLE_ORDER_BLOCK_FILTER_AND_EQH_EQL",
            ),
            (
                "calendar weeks may be partial",
                "remain provisional",
                "nonpositive opening prices",
                "ATR200 is unavailable",
            ),
        ),
        (("SMC_FUTURE_LIMITATION",), ("SMC FUTURE LIMITATION",)),
    ],
)
def test_png_carries_visible_smc_limitations_when_atr_is_ready(
    monkeypatch: pytest.MonkeyPatch,
    limitations: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    from matplotlib.figure import Figure

    bars = _bars()
    analysis = TALibIndicatorEngine().analyze(bars, interval="1w")
    assert analysis.smart_money is not None and analysis.smart_money.atr_200_ready
    analysis = replace(analysis, smart_money=replace(analysis.smart_money, limitations=limitations))
    savefig = Figure.savefig
    footers: list[str] = []

    def inspect_figure(figure: Figure, *args: object, **kwargs: object) -> None:
        figure.canvas.draw()
        for text in figure.texts:
            footers.append(text.get_text())
            bounds = text.get_window_extent(figure.canvas.get_renderer())
            # Meaningful standalone guidance must fit on the exported canvas,
            # below the axes, rather than existing only in the JSON receipt.
            assert figure.bbox.contains(bounds.x0, bounds.y0)
            assert figure.bbox.contains(bounds.x1, bounds.y1)
            assert all(axis.get_window_extent().y0 > bounds.y1 for axis in figure.axes)
        savefig(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", inspect_figure)
    png = MatplotlibChartRenderer().render(
        instrument_id="equity:US:TEST", bars=bars, analysis=analysis
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    visible = " ".join(" ".join(footers).split())
    assert all(message in visible for message in expected)
    assert "has not been historically validated" in visible
