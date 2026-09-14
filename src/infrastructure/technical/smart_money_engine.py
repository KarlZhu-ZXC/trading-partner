"""Deterministic Smart Money Concepts structure analysis over confirmed bars.

The calculation profile targets LuxAlgo's published defaults, but this is an
independent Python implementation. Pivots become usable only after their right-hand
confirmation bars, so structure events are never backdated to unavailable information.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from domain.market.models import MarketBar
from domain.technical.models import (
    SmartMoneyAnalysis,
    SmartMoneyLiquidity,
    SmartMoneyStructureEvent,
    SmartMoneySwing,
    SmartMoneyZone,
)

_BASIS = "confirmed pivots; close-through breaks; no look-ahead"


@dataclass(slots=True)
class _Pivot:
    scope: str
    kind: str
    index: int
    confirmed_index: int
    price: Decimal
    label: str = ""
    broken: bool = False


def _pivots(bars: Sequence[MarketBar], *, scope: str, span: int) -> list[_Pivot]:
    pivots: list[_Pivot] = []
    previous: dict[str, Decimal] = {}
    leg = 0
    previous_leg = 0
    for confirmed_index in range(span, len(bars)):
        index = confirmed_index - span
        confirmation_window = bars[index + 1 : confirmed_index + 1]
        new_high = bars[index].high > max(item.high for item in confirmation_window)
        new_low = bars[index].low < min(item.low for item in confirmation_window)
        if new_high:
            leg = 0
        elif new_low:
            leg = 1
        if leg != previous_leg:
            kind = "low" if leg - previous_leg == 1 else "high"
            price = bars[index].low if kind == "low" else bars[index].high
            prior = previous.get(kind)
            if kind == "high":
                label = "HH" if prior is None or price > prior else "LH"
            else:
                label = "LL" if prior is None or price < prior else "HL"
            previous[kind] = price
            pivots.append(
                _Pivot(
                    scope=scope,
                    kind=kind,
                    index=index,
                    confirmed_index=confirmed_index,
                    price=price,
                    label=label,
                )
            )
        previous_leg = leg
    return pivots


def _order_block_status(
    bars: Sequence[MarketBar],
    *,
    start: int,
    low: Decimal,
    high: Decimal,
    direction: str,
) -> str:
    for bar in bars[start:]:
        if direction == "bullish":
            if bar.low < low:
                return "invalidated"
        else:
            if bar.high > high:
                return "invalidated"
    return "active"


def _order_block(
    bars: Sequence[MarketBar],
    *,
    pivot: _Pivot,
    break_index: int,
    direction: str,
    parsed_highs: Sequence[Decimal],
    parsed_lows: Sequence[Decimal],
) -> SmartMoneyZone | None:
    candidates = range(pivot.index, break_index)
    if not candidates:
        return None
    if direction == "bullish":
        chosen = min(
            candidates,
            key=lambda index: parsed_lows[index],
        )
    else:
        chosen = max(
            candidates,
            key=lambda index: parsed_highs[index],
        )
    low = min(parsed_lows[chosen], parsed_highs[chosen])
    high = max(parsed_lows[chosen], parsed_highs[chosen])
    return SmartMoneyZone(
        kind="order_block",
        scope=pivot.scope,
        direction=direction,
        low=low,
        high=high,
        created_at=bars[chosen].timestamp,
        confirmed_at=bars[break_index].timestamp,
        status=_order_block_status(
            bars,
            start=break_index,
            low=low,
            high=high,
            direction=direction,
        ),
        basis="LuxAlgo-compatible filtered extreme between pivot and structure break",
    )


def _structure(
    bars: Sequence[MarketBar],
    pivots: list[_Pivot],
    *,
    parsed_highs: Sequence[Decimal],
    parsed_lows: Sequence[Decimal],
    swing_pivots: list[_Pivot] | None = None,
) -> tuple[str, list[SmartMoneyStructureEvent], list[SmartMoneyZone]]:
    by_confirmation: dict[int, list[_Pivot]] = defaultdict(list)
    for pivot in pivots:
        by_confirmation[pivot.confirmed_index].append(pivot)
    latest: dict[str, _Pivot] = {}
    trend = "neutral"
    events: list[SmartMoneyStructureEvent] = []
    order_blocks: list[SmartMoneyZone] = []
    swing_by_confirmation: dict[int, list[_Pivot]] = defaultdict(list)
    for pivot in swing_pivots or ():
        swing_by_confirmation[pivot.confirmed_index].append(pivot)
    latest_swing: dict[str, _Pivot] = {}
    for index, bar in enumerate(bars):
        for pivot in swing_by_confirmation.get(index, ()):
            latest_swing[pivot.kind] = pivot
        for pivot in by_confirmation.get(index, ()):  # usable from this bar onward
            latest[pivot.kind] = pivot
        high = latest.get("high")
        low = latest.get("low")
        prior_close = bars[index - 1].close if index else None
        high_is_distinct = (
            swing_pivots is None
            or latest_swing.get("high") is None
            or high is None
            or high.price != latest_swing["high"].price
        )
        if (
            high is not None
            and not high.broken
            and prior_close is not None
            and prior_close <= high.price < bar.close
            and high_is_distinct
        ):
            event = "choch" if trend == "bearish" else "bos"
            trend = "bullish"
            high.broken = True
            events.append(
                SmartMoneyStructureEvent(
                    scope=high.scope,
                    event=event,
                    direction="bullish",
                    price=high.price,
                    occurred_at=bar.timestamp,
                    broken_swing_at=bars[high.index].timestamp,
                    basis=_BASIS,
                )
            )
            block = _order_block(
                bars,
                pivot=high,
                break_index=index,
                direction="bullish",
                parsed_highs=parsed_highs,
                parsed_lows=parsed_lows,
            )
            if block is not None:
                order_blocks.append(block)
        low_is_distinct = (
            swing_pivots is None
            or latest_swing.get("low") is None
            or low is None
            or low.price != latest_swing["low"].price
        )
        if (
            low is not None
            and not low.broken
            and prior_close is not None
            and prior_close >= low.price > bar.close
            and low_is_distinct
        ):
            event = "choch" if trend == "bullish" else "bos"
            trend = "bearish"
            low.broken = True
            events.append(
                SmartMoneyStructureEvent(
                    scope=low.scope,
                    event=event,
                    direction="bearish",
                    price=low.price,
                    occurred_at=bar.timestamp,
                    broken_swing_at=bars[low.index].timestamp,
                    basis=_BASIS,
                )
            )
            block = _order_block(
                bars,
                pivot=low,
                break_index=index,
                direction="bearish",
                parsed_highs=parsed_highs,
                parsed_lows=parsed_lows,
            )
            if block is not None:
                order_blocks.append(block)
    return trend, events, order_blocks


def _fair_value_gaps(
    bars: Sequence[MarketBar],
) -> list[SmartMoneyZone]:
    zones: list[SmartMoneyZone] = []
    cumulative_delta = 0.0
    for index in range(2, len(bars)):
        surviving: list[SmartMoneyZone] = []
        for zone in zones:
            filled = (
                zone.direction == "bullish" and bars[index].low < zone.low
            ) or (zone.direction == "bearish" and bars[index].high > zone.high)
            if not filled:
                surviving.append(zone)
        zones = surviving
        first = bars[index - 2]
        middle = bars[index - 1]
        current = bars[index]
        delta = float((middle.close - middle.open) / (middle.open * Decimal("100")))
        cumulative_delta += abs(delta)
        threshold = cumulative_delta / index * 2
        if (
            current.low > first.high
            and middle.close > first.high
            and delta > threshold
        ):
            zones.append(
                SmartMoneyZone(
                    kind="fair_value_gap",
                    scope="swing",
                    direction="bullish",
                    low=first.high,
                    high=current.low,
                    created_at=middle.timestamp,
                    confirmed_at=current.timestamp,
                    status="active",
                    basis="LuxAlgo-compatible three-bar imbalance with auto threshold",
                )
            )
        elif (
            current.high < first.low
            and middle.close < first.low
            and -delta > threshold
        ):
            zones.append(
                SmartMoneyZone(
                    kind="fair_value_gap",
                    scope="swing",
                    direction="bearish",
                    low=current.high,
                    high=first.low,
                    created_at=middle.timestamp,
                    confirmed_at=current.timestamp,
                    status="active",
                    basis="LuxAlgo-compatible three-bar imbalance with auto threshold",
                )
            )
    return zones


def _liquidity(
    bars: Sequence[MarketBar], pivots: list[_Pivot], atr: np.ndarray
) -> list[SmartMoneyLiquidity]:
    values: list[SmartMoneyLiquidity] = []
    previous: dict[str, _Pivot] = {}
    for pivot in pivots:
        prior = previous.get(pivot.kind)
        previous[pivot.kind] = pivot
        if prior is None:
            continue
        atr_value = float(atr[pivot.confirmed_index])
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        tolerance = Decimal(str(round(atr_value * 0.1, 10)))
        if abs(pivot.price - prior.price) > tolerance:
            continue
        price = pivot.price
        status = "active"
        for bar in bars[pivot.confirmed_index + 1 :]:
            if pivot.kind == "high" and bar.high > price + tolerance:
                status = "swept"
                break
            if pivot.kind == "low" and bar.low < price - tolerance:
                status = "swept"
                break
        values.append(
            SmartMoneyLiquidity(
                kind="equal_high" if pivot.kind == "high" else "equal_low",
                scope=pivot.scope,
                price=price,
                first_swing_at=bars[prior.index].timestamp,
                second_swing_at=bars[pivot.index].timestamp,
                confirmed_at=bars[pivot.confirmed_index].timestamp,
                status=status,
                tolerance=tolerance,
            )
        )
    return values


def _value_zones(bars: Sequence[MarketBar], pivots: list[_Pivot]) -> list[SmartMoneyZone]:
    by_confirmation = {pivot.confirmed_index: pivot for pivot in pivots}
    top: Decimal | None = None
    bottom: Decimal | None = None
    range_index: int | None = None
    for index, bar in enumerate(bars):
        if top is not None:
            top = max(top, bar.high)
        if bottom is not None:
            bottom = min(bottom, bar.low)
        pivot = by_confirmation.get(index)
        if pivot is not None:
            if pivot.kind == "high":
                top = pivot.price
            else:
                bottom = pivot.price
            range_index = pivot.index
    if top is None or bottom is None or top <= bottom or range_index is None:
        return []
    width = top - bottom
    created_at = bars[range_index].timestamp
    bounds = (
        ("discount", bottom, bottom + width * Decimal("0.05")),
        (
            "equilibrium",
            bottom + width * Decimal("0.475"),
            bottom + width * Decimal("0.525"),
        ),
        ("premium", top - width * Decimal("0.05"), top),
    )
    return [
        SmartMoneyZone(
            kind=kind,
            scope="swing",
            direction=None,
            low=zone_low,
            high=zone_high,
            created_at=created_at,
            confirmed_at=bars[-1].timestamp,
            status="current",
            basis="latest confirmed swing range; LuxAlgo-style 5%/midpoint bands",
        )
        for kind, zone_low, zone_high in bounds
    ]


def analyze_smart_money(
    bars: Sequence[MarketBar], *, atr: np.ndarray
) -> SmartMoneyAnalysis:
    """Return bounded internal/swing SMC structure for one bar series."""

    atr_200_ready = bool(len(atr) and math.isfinite(float(atr[-1])))
    limitations = (
        ()
        if atr_200_ready
        else ("SMC_ATR_200_UNAVAILABLE_ORDER_BLOCK_FILTER_AND_EQH_EQL",)
    )
    internal = _pivots(bars, scope="internal", span=5)
    swing = _pivots(bars, scope="swing", span=50)
    parsed_highs: list[Decimal] = []
    parsed_lows: list[Decimal] = []
    for index, bar in enumerate(bars):
        volatile = (
            index < len(atr)
            and math.isfinite(float(atr[index]))
            and (bar.high - bar.low) >= Decimal(str(2 * float(atr[index])))
        )
        parsed_highs.append(bar.low if volatile else bar.high)
        parsed_lows.append(bar.high if volatile else bar.low)
    internal_trend, internal_events, internal_blocks = _structure(
        bars,
        internal,
        parsed_highs=parsed_highs,
        parsed_lows=parsed_lows,
        swing_pivots=swing,
    )
    swing_trend, swing_events, swing_blocks = _structure(
        bars,
        swing,
        parsed_highs=parsed_highs,
        parsed_lows=parsed_lows,
    )
    trend = swing_trend if swing_trend != "neutral" else internal_trend
    swings = [
        SmartMoneySwing(
            scope=pivot.scope,
            kind=pivot.kind,
            label=pivot.label,
            price=pivot.price,
            occurred_at=bars[pivot.index].timestamp,
            confirmed_at=bars[pivot.confirmed_index].timestamp,
        )
        for pivot in (*internal, *swing)
    ]
    swings.sort(key=lambda item: (item.confirmed_at, item.scope, item.kind))
    events = sorted(
        (*internal_events, *swing_events), key=lambda item: (item.occurred_at, item.scope)
    )
    blocks = sorted(
        (
            block
            for block in (*internal_blocks, *swing_blocks)
            if block.status == "active"
        ),
        key=lambda item: (item.confirmed_at, item.scope),
    )
    equal = _pivots(bars, scope="internal", span=3)
    liquidity = sorted(
        _liquidity(bars, equal, atr), key=lambda item: item.confirmed_at
    )
    return SmartMoneyAnalysis(
        trend=trend,
        atr_200_ready=atr_200_ready,
        limitations=limitations,
        swings=tuple(swings[-8:]),
        structure_events=tuple(events[-6:]),
        order_blocks=tuple(blocks[-4:]),
        fair_value_gaps=tuple(_fair_value_gaps(bars)[-6:]),
        liquidity_levels=tuple(liquidity[-4:]),
        value_zones=tuple(_value_zones(bars, swing)),
    )
