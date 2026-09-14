"""Headless PNG candlestick renderer for MCP image content."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from io import BytesIO

import numpy as np
import talib

from domain.common.errors import ProviderNotConfigured
from domain.market.models import MarketBar
from domain.technical.models import TechnicalTimeframe


class MatplotlibChartRenderer:
    def render(
        self,
        *,
        instrument_id: str,
        bars: Sequence[MarketBar],
        analysis: TechnicalTimeframe,
    ) -> bytes:
        try:
            import matplotlib

            matplotlib.use("Agg")
            from matplotlib import pyplot as plt
            from matplotlib.patches import Rectangle
        except ImportError:
            raise ProviderNotConfigured(
                "PNG chart rendering is unavailable; install trading-partner[chart]"
            ) from None
        visible = tuple(bars[-120:])
        close = np.asarray([float(bar.close) for bar in visible])
        volume = np.asarray([float(bar.volume) for bar in visible])
        ema20 = talib.EMA(close, 20)
        sma50 = talib.SMA(close, 50)
        rsi = talib.RSI(close, 14)

        figure, (price_ax, volume_ax, rsi_ax) = plt.subplots(
            3,
            1,
            figsize=(13, 8),
            sharex=True,
            gridspec_kw={"height_ratios": [4, 1, 1]},
        )
        figure.patch.set_facecolor("#111827")
        for axis in (price_ax, volume_ax, rsi_ax):
            axis.set_facecolor("#111827")
            axis.tick_params(colors="#cbd5e1", labelsize=8)
            axis.grid(color="#334155", alpha=0.25)
            for spine in axis.spines.values():
                spine.set_color("#334155")

        colors: list[str] = []
        for index, bar in enumerate(visible):
            rising = bar.close >= bar.open
            color = "#22c55e" if rising else "#ef4444"
            colors.append(color)
            price_ax.vlines(index, float(bar.low), float(bar.high), color=color, linewidth=0.8)
            bottom = float(min(bar.open, bar.close))
            height = max(abs(float(bar.close - bar.open)), max(float(bar.close) * 0.0002, 1e-8))
            price_ax.add_patch(Rectangle((index - 0.32, bottom), 0.64, height, color=color))
        price_ax.plot(ema20, color="#38bdf8", linewidth=1.2, label="EMA20")
        price_ax.plot(sma50, color="#f59e0b", linewidth=1.2, label="SMA50")
        for level in analysis.levels:
            price_ax.axhline(
                float(level.price),
                color="#94a3b8",
                linewidth=0.7,
                linestyle="--",
                alpha=0.6,
            )
        smart_money = analysis.smart_money
        if smart_money is not None:
            index_by_time = {bar.timestamp: index for index, bar in enumerate(visible)}

            def zone_start(created_at: datetime) -> int | None:
                if created_at in index_by_time:
                    return index_by_time[created_at]
                if created_at < visible[0].timestamp:
                    return 0
                return None

            value_colors = {
                "premium": "#ef4444",
                "equilibrium": "#94a3b8",
                "discount": "#22c55e",
            }
            for zone in smart_money.value_zones:
                price_ax.axhspan(
                    float(zone.low),
                    float(zone.high),
                    color=value_colors.get(zone.kind, "#94a3b8"),
                    alpha=0.055,
                )
            visible_zones = (
                tuple(
                    zone
                    for zone in smart_money.order_blocks
                    if zone.scope == "swing" and zone.status == "active"
                )[-3:]
                + tuple(
                    zone
                    for zone in smart_money.fair_value_gaps
                    if zone.status == "active"
                )[-3:]
            )
            for zone in visible_zones:
                start = zone_start(zone.created_at)
                if start is None:
                    continue
                start = max(start, len(visible) - 80)
                color = "#22c55e" if zone.direction == "bullish" else "#ef4444"
                alpha = 0.13 if zone.kind == "order_block" else 0.075
                price_ax.add_patch(
                    Rectangle(
                        (start - 0.45, float(zone.low)),
                        len(visible) - start - 0.1,
                        max(float(zone.high - zone.low), 1e-8),
                        facecolor=color,
                        edgecolor=color,
                        linewidth=0.7,
                        alpha=alpha,
                    )
                )
                price_ax.annotate(
                    "OB" if zone.kind == "order_block" else "FVG",
                    (start, float((zone.low + zone.high) / 2)),
                    color=color,
                    fontsize=6,
                    ha="left",
                    va="center",
                )
            for liquidity in smart_money.liquidity_levels:
                start = zone_start(liquidity.first_swing_at)
                end = zone_start(liquidity.second_swing_at)
                if start is None or end is None:
                    continue
                price_ax.hlines(
                    float(liquidity.price),
                    start,
                    end,
                    color="#facc15",
                    linewidth=0.65,
                    linestyle=":",
                    alpha=0.8,
                )
                price_ax.annotate(
                    "EQH" if liquidity.kind == "equal_high" else "EQL",
                    ((start + end) / 2, float(liquidity.price)),
                    color="#facc15",
                    fontsize=6,
                    ha="center",
                    va="bottom" if liquidity.kind == "equal_high" else "top",
                )
            swing_events = tuple(
                event
                for event in smart_money.structure_events
                if event.scope == "swing"
            )
            chart_events = swing_events[-6:] or smart_money.structure_events[-4:]
            for event in chart_events:
                event_index = index_by_time.get(event.occurred_at)
                if event_index is None:
                    continue
                color = "#22c55e" if event.direction == "bullish" else "#ef4444"
                price_ax.annotate(
                    f"{event.scope[0].upper()} {event.event.upper()}",
                    (event_index, float(event.price)),
                    color=color,
                    fontsize=6.5,
                    ha="center",
                    va="center",
                    xytext=(0, 8 if event.direction == "bullish" else -8),
                    textcoords="offset points",
                )
            for swing in smart_money.swings[-10:]:
                if swing.scope != "swing":
                    continue
                swing_index = index_by_time.get(swing.occurred_at)
                if swing_index is None:
                    continue
                price_ax.annotate(
                    swing.label,
                    (swing_index, float(swing.price)),
                    color="#cbd5e1",
                    fontsize=6,
                    ha="center",
                    va="center",
                    xytext=(0, 7 if swing.kind == "high" else -7),
                    textcoords="offset points",
                )
        price_ax.legend(loc="upper left", frameon=False, labelcolor="#e2e8f0")
        price_ax.set_title(
            (
                f"{instrument_id} · {analysis.interval} · {analysis.trend_state}"
                + (
                    f" · SMC {smart_money.trend}"
                    if smart_money is not None
                    else ""
                )
            ),
            color="#f8fafc",
            loc="left",
            fontsize=13,
        )
        volume_ax.bar(range(len(visible)), volume, color=colors, width=0.7, alpha=0.75)
        volume_ax.set_ylabel("Volume", color="#94a3b8", fontsize=8)
        rsi_ax.plot(rsi, color="#a78bfa", linewidth=1)
        rsi_ax.axhline(70, color="#ef4444", linewidth=0.7, linestyle="--")
        rsi_ax.axhline(30, color="#22c55e", linewidth=0.7, linestyle="--")
        rsi_ax.set_ylim(0, 100)
        rsi_ax.set_ylabel("RSI14", color="#94a3b8", fontsize=8)
        positions = np.linspace(0, len(visible) - 1, min(8, len(visible)), dtype=int)
        rsi_ax.set_xticks(positions)
        rsi_ax.set_xticklabels(
            [visible[index].timestamp.date().isoformat() for index in positions],
            rotation=25,
            ha="right",
        )
        figure.tight_layout()
        output = BytesIO()
        figure.savefig(output, format="png", dpi=140, facecolor=figure.get_facecolor())
        plt.close(figure)
        return output.getvalue()
