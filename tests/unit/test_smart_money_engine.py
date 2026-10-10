"""Deterministic Smart Money Concepts structure-analysis tests."""

from __future__ import annotations

from dataclasses import replace
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


def _price_bars(rows: list[tuple[str, str, str, str]]) -> tuple[MarketBar, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        MarketBar(
            timestamp=start + timedelta(days=index),
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("1000"),
        )
        for index, (open_, high, low, close) in enumerate(rows)
    )


def test_zero_open_discloses_fvg_limitation_without_blocking_structure() -> None:
    bars = list(_bars())
    bars[189] = replace(bars[189], open=Decimal("0"), low=Decimal("0"))

    result = analyze_smart_money(bars, atr=np.full(len(bars), 2.0))

    assert result.limitations == ("SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE",)
    assert result.atr_200_ready is True
    assert result.swings
    assert result.structure_events
    assert result.order_blocks
    assert result.value_zones


def test_negative_open_cannot_reverse_displacement_direction() -> None:
    # A falling middle candle at negative prices produces a positive ratio
    # if divided by its signed open. It must not create a bullish FVG.
    bars = _price_bars([
        ("-120", "-119", "-121", "-120"),
        ("-120", "-119", "-121", "-120"),
        ("-100", "-99", "-111", "-110"),
        ("-110", "-109", "-111", "-110"),
    ])

    result = analyze_smart_money(bars, atr=np.full(len(bars), 2.0))

    assert result.fair_value_gaps == ()
    assert result.limitations == ("SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE",)


def _bearish_gap_with_zero_open() -> tuple[MarketBar, ...]:
    return _price_bars([
        ("100", "101", "99", "100"),
        ("100", "101", "99", "100"),
        ("100", "101", "89", "90"),
        ("90", "91", "89", "90"),
        ("0", "91", "0", "90"),
        ("100", "101", "99", "100"),
    ])


def test_existing_gap_can_fill_when_middle_open_skips_new_gap_detection() -> None:
    bars = _bearish_gap_with_zero_open()
    before = analyze_smart_money(bars[:5], atr=np.full(5, 2.0))
    after = analyze_smart_money(bars, atr=np.full(len(bars), 2.0))

    assert [(gap.direction, gap.low, gap.high) for gap in before.fair_value_gaps] == [
        ("bearish", Decimal("91"), Decimal("99")),
    ]
    assert after.fair_value_gaps == ()
    assert after.limitations == ("SMC_FVG_NONPOSITIVE_OPEN_UNAVAILABLE",)


def test_nonpositive_open_limitation_and_gap_confirmation_do_not_look_ahead() -> None:
    bars = _bearish_gap_with_zero_open()
    unconfirmed = analyze_smart_money(bars[:3], atr=np.full(3, 2.0))
    confirmed = analyze_smart_money(bars[:4], atr=np.full(4, 2.0))
    zero_open_is_last = analyze_smart_money(bars[:5], atr=np.full(5, 2.0))

    assert unconfirmed.fair_value_gaps == ()
    assert len(confirmed.fair_value_gaps) == 1
    gap = confirmed.fair_value_gaps[0]
    assert gap.created_at == bars[2].timestamp
    assert gap.confirmed_at == bars[3].timestamp
    assert confirmed.limitations == ()
    # The zero-open bar has not become the middle of a three-bar window yet.
    assert zero_open_is_last.limitations == ()
    assert zero_open_is_last.fair_value_gaps == confirmed.fair_value_gaps
