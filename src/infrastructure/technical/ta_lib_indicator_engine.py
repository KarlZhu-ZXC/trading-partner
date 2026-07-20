"""TA-Lib-backed standard indicators plus project-owned structure analysis."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

import numpy as np
import talib

from domain.common.errors import DataContractError
from domain.market.models import MarketBar
from domain.technical.models import (
    TechnicalLevel,
    TechnicalMetric,
    TechnicalPattern,
    TechnicalTimeframe,
)


def _decimal(value: float) -> Decimal | None:
    if math.isnan(value) or math.isinf(value):
        return None
    return Decimal(str(round(float(value), 10)))


def _latest(values: np.ndarray) -> Decimal | None:
    return _decimal(float(values[-1])) if len(values) else None


def _metric(name: str, values: np.ndarray, unit: str, basis: str) -> TechnicalMetric:
    return TechnicalMetric(name=name, value=_latest(values), unit=unit, basis=basis)


def _state(value: float, *, upper: float, lower: float) -> str:
    if math.isnan(value):
        return "insufficient_data"
    if value >= upper:
        return "high"
    if value <= lower:
        return "low"
    return "neutral"


def _cluster_levels(
    bars: Sequence[MarketBar],
    atr: float,
) -> tuple[TechnicalLevel, ...]:
    if len(bars) < 20 or not math.isfinite(atr) or atr <= 0:
        return ()
    pivots: list[tuple[str, Decimal]] = []
    for index in range(2, len(bars) - 2):
        window = bars[index - 2 : index + 3]
        bar = bars[index]
        if bar.low == min(item.low for item in window):
            pivots.append(("support", bar.low))
        if bar.high == max(item.high for item in window):
            pivots.append(("resistance", bar.high))
    tolerance = Decimal(str(atr * 0.75))
    last_close = bars[-1].close
    clusters: list[list[Decimal]] = []
    for _kind, price in pivots[-80:]:
        target = next(
            (
                cluster
                for cluster in clusters
                if abs(
                    price - sum(cluster, Decimal("0")) / Decimal(len(cluster))
                )
                <= tolerance
            ),
            None,
        )
        if target is None:
            clusters.append([price])
        else:
            target.append(price)
    levels: list[TechnicalLevel] = []
    for cluster in clusters:
        price = sum(cluster, Decimal("0")) / Decimal(len(cluster))
        kind = "support" if price <= last_close else "resistance"
        levels.append(
            TechnicalLevel(
                kind=kind,
                price=price,
                touches=len(cluster),
                basis="five_bar_swing_cluster_within_0.75_atr",
            )
        )
    supports = sorted(
        (level for level in levels if level.kind == "support"),
        key=lambda level: level.price,
        reverse=True,
    )[:3]
    resistances = sorted(
        (level for level in levels if level.kind == "resistance"),
        key=lambda level: level.price,
    )[:3]
    return tuple((*supports, *resistances))


def _patterns(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> tuple[TechnicalPattern, ...]:
    functions = (
        ("engulfing", talib.CDLENGULFING),
        ("hammer", talib.CDLHAMMER),
        ("shooting_star", talib.CDLSHOOTINGSTAR),
        ("doji", talib.CDLDOJI),
    )
    found: list[TechnicalPattern] = []
    for name, fn in functions:
        values = fn(open_, high, low, close)
        recent = values[-3:]
        nonzero = next((int(value) for value in reversed(recent) if value != 0), 0)
        if nonzero:
            found.append(
                TechnicalPattern(
                    name=name,
                    direction="bullish" if nonzero > 0 else "bearish",
                    strength=abs(nonzero),
                    basis="TA-Lib candlestick recognition; latest three bars",
                )
            )
    return tuple(found)


class TALibIndicatorEngine:
    """Calculate disclosed, non-predictive daily or weekly technical facts."""

    def analyze(self, bars: Sequence[MarketBar], *, interval: str) -> TechnicalTimeframe:
        if interval not in {"1d", "1w"}:
            raise DataContractError("technical interval must be 1d or 1w")
        if len(bars) < 20:
            raise DataContractError("at least 20 bars are required for technical analysis")
        open_ = np.asarray([float(bar.open) for bar in bars], dtype=np.float64)
        high = np.asarray([float(bar.high) for bar in bars], dtype=np.float64)
        low = np.asarray([float(bar.low) for bar in bars], dtype=np.float64)
        close = np.asarray([float(bar.close) for bar in bars], dtype=np.float64)
        volume = np.asarray([float(bar.volume) for bar in bars], dtype=np.float64)

        ema10 = talib.EMA(close, timeperiod=10)
        ema20 = talib.EMA(close, timeperiod=20)
        sma50 = talib.SMA(close, timeperiod=50)
        sma200 = talib.SMA(close, timeperiod=200)
        rsi = talib.RSI(close, timeperiod=14)
        macd, macd_signal, macd_hist = talib.MACD(close, 12, 26, 9)
        atr = talib.ATR(high, low, close, timeperiod=14)
        bb_upper, bb_mid, bb_lower = talib.BBANDS(close, 20, 2, 2)
        adx = talib.ADX(high, low, close, timeperiod=14)
        plus_di = talib.PLUS_DI(high, low, close, timeperiod=14)
        minus_di = talib.MINUS_DI(high, low, close, timeperiod=14)
        slow_k, slow_d = talib.STOCH(high, low, close)
        roc = talib.ROC(close, timeperiod=20)
        mfi = talib.MFI(high, low, close, volume, timeperiod=14)
        obv = talib.OBV(close, volume)
        volume_sma20 = talib.SMA(volume, timeperiod=20)
        price_volume_sma20 = talib.SMA(close * volume, timeperiod=20)
        vwma20 = np.divide(
            price_volume_sma20,
            volume_sma20,
            out=np.full_like(volume, np.nan),
            where=volume_sma20 != 0,
        )
        relative_volume = np.divide(
            volume,
            volume_sma20,
            out=np.full_like(volume, np.nan),
            where=volume_sma20 != 0,
        )
        bb_width = (
            np.divide(
                bb_upper - bb_lower,
                bb_mid,
                out=np.full_like(close, np.nan),
                where=bb_mid != 0,
            )
            * 100
        )
        atr_pct = (
            np.divide(
                atr,
                close,
                out=np.full_like(close, np.nan),
                where=close != 0,
            )
            * 100
        )

        last_close = close[-1]
        trend_state = "mixed"
        if not math.isnan(ema20[-1]) and not math.isnan(sma50[-1]):
            if last_close > ema20[-1] > sma50[-1]:
                trend_state = "uptrend"
            elif last_close < ema20[-1] < sma50[-1]:
                trend_state = "downtrend"
        momentum_state = _state(float(rsi[-1]), upper=60, lower=40)
        if momentum_state == "high":
            momentum_state = "positive"
        elif momentum_state == "low":
            momentum_state = "negative"
        volatility_state = _state(float(atr_pct[-1]), upper=4, lower=1.5)
        volume_state = _state(float(relative_volume[-1]), upper=1.5, lower=0.7)

        metrics = (
            _metric("ema_10", ema10, "price", "TA-Lib EMA(10)"),
            _metric("ema_20", ema20, "price", "TA-Lib EMA(20)"),
            _metric("sma_50", sma50, "price", "TA-Lib SMA(50)"),
            _metric("sma_200", sma200, "price", "TA-Lib SMA(200)"),
            _metric("rsi_14", rsi, "index", "TA-Lib RSI(14)"),
            _metric("macd", macd, "price", "TA-Lib MACD(12,26,9)"),
            _metric("macd_signal", macd_signal, "price", "TA-Lib MACD signal(9)"),
            _metric("macd_histogram", macd_hist, "price", "TA-Lib MACD histogram"),
            _metric("atr_14", atr, "price", "TA-Lib ATR(14)"),
            _metric("atr_percent", atr_pct, "percent", "ATR(14) / close"),
            _metric("bollinger_upper", bb_upper, "price", "TA-Lib BBANDS(20,2)"),
            _metric("bollinger_mid", bb_mid, "price", "TA-Lib BBANDS(20,2)"),
            _metric("bollinger_lower", bb_lower, "price", "TA-Lib BBANDS(20,2)"),
            _metric("bollinger_width", bb_width, "percent", "band width / middle band"),
            _metric("adx_14", adx, "index", "TA-Lib ADX(14)"),
            _metric("plus_di_14", plus_di, "index", "TA-Lib PLUS_DI(14)"),
            _metric("minus_di_14", minus_di, "index", "TA-Lib MINUS_DI(14)"),
            _metric("stochastic_k", slow_k, "index", "TA-Lib STOCH(5,3,3)"),
            _metric("stochastic_d", slow_d, "index", "TA-Lib STOCH(5,3,3)"),
            _metric("roc_20", roc, "percent", "TA-Lib ROC(20)"),
            _metric("mfi_14", mfi, "index", "TA-Lib MFI(14)"),
            _metric(
                "vwma_20",
                vwma20,
                "price",
                "SMA(close * volume,20) / SMA(volume,20)",
            ),
            _metric("obv", obv, "volume", "TA-Lib OBV"),
            _metric("relative_volume_20", relative_volume, "ratio", "volume / SMA(volume,20)"),
        )
        return TechnicalTimeframe(
            interval=interval,
            bar_as_of=bars[-1].timestamp,
            bar_count=len(bars),
            trend_state=trend_state,
            momentum_state=momentum_state,
            volatility_state=volatility_state,
            volume_state=volume_state,
            metrics=metrics,
            levels=_cluster_levels(bars, float(atr[-1])),
            patterns=_patterns(open_, high, low, close),
        )
