"""Headless PNG candlestick renderer for MCP image content."""

from __future__ import annotations

from collections.abc import Sequence
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
        price_ax.legend(loc="upper left", frameon=False, labelcolor="#e2e8f0")
        price_ax.set_title(
            f"{instrument_id} · {analysis.interval} · {analysis.trend_state}",
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
