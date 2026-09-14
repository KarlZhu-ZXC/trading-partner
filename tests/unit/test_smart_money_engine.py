"""Deterministic Smart Money Concepts structure-analysis tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from domain.market.models import MarketBar
from infrastructure.technical.smart_money_engine import analyze_smart_money


def _bars() -> tuple[MarketBar, ...]:
    closes = [Decimal("100")] * 200
    closes[10] = Decimal("80")
    closes[70] = Decimal("120")
    closes[120] = Decimal("110")
    closes[121] = Decimal("125")
    closes[130] = Decimal("90")
    closes[160] = Decimal("110")
    closes[181] = Decimal("85")
    closes[188] = Decimal("86")
    closes[189] = Decimal("92")
    closes[190:] = [Decimal("100") + Decimal(i) / Decimal("10") for i in range(10)]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    values: list[MarketBar] = []
    for index, close in enumerate(closes):
        open_ = close - Decimal("0.4")
        if index == 189:
            open_ = Decimal("80")
        values.append(
            MarketBar(
                timestamp=start + timedelta(days=index),
                open=open_,
                high=max(open_, close) + Decimal("1"),
                low=min(open_, close) - Decimal("1"),
                close=close,
                volume=Decimal("1000") + Decimal(index * 10),
            )
        )
    return tuple(values)


def test_smc_detects_confirmed_structure_zones_and_liquidity() -> None:
    bars = _bars()
    result = analyze_smart_money(bars, atr=np.full(len(bars), 2.0))

    swing_events = [event for event in result.structure_events if event.scope == "swing"]
    assert [(event.event, event.direction) for event in swing_events] == [
        ("bos", "bullish"),
        ("choch", "bearish"),
    ]
    assert swing_events[0].occurred_at > swing_events[0].broken_swing_at
    assert result.trend == "bearish"
    assert [
        (block.scope, block.direction, block.low, block.high)
        for block in result.order_blocks
    ] == [("swing", "bearish", Decimal("108.6"), Decimal("111"))]
    assert [
        (gap.direction, gap.low, gap.high) for gap in result.fair_value_gaps
    ] == [("bullish", Decimal("87"), Decimal("98.6"))]
    assert {zone.kind for zone in result.value_zones} == {
        "discount",
        "equilibrium",
        "premium",
    }
    assert [(zone.kind, zone.low, zone.high) for zone in result.value_zones] == [
        ("discount", Decimal("79"), Decimal("81.35")),
        ("equilibrium", Decimal("101.325"), Decimal("103.675")),
        ("premium", Decimal("123.65"), Decimal("126")),
    ]
    assert result.algorithm_version == "tp_smc_v1"
    assert result.historically_validated is False
    assert result.atr_200_ready is True
    assert len(result.swings) <= 8
    assert len(result.structure_events) <= 6
    assert len(result.order_blocks) <= 4
    assert len(result.fair_value_gaps) <= 6
    assert len(result.liquidity_levels) <= 4


def test_swing_is_exposed_only_after_right_hand_confirmation() -> None:
    bars = _bars()
    pivot_time = bars[10].timestamp

    before_confirmation = analyze_smart_money(
        bars[:60], atr=np.full(60, 2.0)
    )
    after_confirmation = analyze_smart_money(
        bars[:61], atr=np.full(61, 2.0)
    )

    assert not any(
        swing.scope == "swing" and swing.occurred_at == pivot_time
        for swing in before_confirmation.swings
    )
    confirmed = next(
        swing
        for swing in after_confirmation.swings
        if swing.scope == "swing" and swing.occurred_at == pivot_time
    )
    assert confirmed.confirmed_at == bars[60].timestamp


def test_equal_high_becomes_a_swept_liquidity_level() -> None:
    closes = [100] * 18
    highs = [101] * 18
    lows = [99] * 18
    lows[0] = 90
    highs[3] = 110
    lows[6] = 90
    highs[9] = Decimal("110.1")
    highs[15] = 112
    start = datetime(2026, 2, 1, tzinfo=UTC)
    bars = tuple(
        MarketBar(
            timestamp=start + timedelta(days=index),
            open=Decimal(closes[index]),
            high=Decimal(highs[index]),
            low=Decimal(lows[index]),
            close=Decimal(closes[index]),
            volume=Decimal("1000"),
        )
        for index in range(len(closes))
    )

    result = analyze_smart_money(bars, atr=np.full(len(bars), 2.0))

    level = next(item for item in result.liquidity_levels if item.kind == "equal_high")
    assert level.scope == "internal"
    assert level.status == "swept"


def test_missing_atr_200_is_disclosed_without_blocking_price_structure() -> None:
    bars = _bars()[:120]

    result = analyze_smart_money(bars, atr=np.full(len(bars), np.nan))

    assert result.atr_200_ready is False
    assert result.limitations == (
        "SMC_ATR_200_UNAVAILABLE_ORDER_BLOCK_FILTER_AND_EQH_EQL",
    )
    assert result.swings
