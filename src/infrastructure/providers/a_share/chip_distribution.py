"""Pure, Decimal-only derived chip-distribution algorithm (E4d)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from domain.common.errors import DataContractError

QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class ChipInputBar:
    low: Decimal
    high: Decimal
    close: Decimal
    turnover_percent: Decimal


@dataclass(frozen=True, slots=True)
class DerivedChip:
    edges: tuple[Decimal, ...]
    weights: tuple[Decimal, ...]
    average_cost: Decimal
    profit_ratio: Decimal
    concentration_90: Decimal
    concentration_70: Decimal


def derive_tp_chip_v1(bars: Sequence[ChipInputBar]) -> DerivedChip:
    if len(bars) != 120:
        raise DataContractError(
            "chip input requires 120 bars", details={"field": "bars", "rule": "exact_120"}
        )
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        for index, bar in enumerate(bars):
            if type(bar) is not ChipInputBar or any(
                type(value) is not Decimal
                for value in (bar.low, bar.high, bar.close, bar.turnover_percent)
            ):
                raise DataContractError(
                    "chip input must use exact ChipInputBar and Decimal values",
                    details={"field": "bars", "index": index, "rule": "type"},
                )
            if any(
                not value.is_finite()
                for value in (bar.low, bar.high, bar.close, bar.turnover_percent)
            ):
                raise DataContractError(
                    "chip input must be finite",
                    details={"field": "bars", "index": index, "rule": "finite"},
                )
            if (
                bar.low <= 0
                or bar.high < bar.low
                or not bar.low <= bar.close <= bar.high
                or bar.turnover_percent < 0
            ):
                raise DataContractError(
                    "chip input range invalid",
                    details={"field": "bars", "index": index, "rule": "range"},
                )
        global_low = min(bar.low for bar in bars)
        global_high = max(bar.high for bar in bars)
        edges: tuple[Decimal, ...]
        if global_low == global_high:
            edges = (global_low, global_high)
        else:
            width = (global_high - global_low) / Decimal(100)
            edges = tuple(global_low + width * Decimal(i) for i in range(100)) + (global_high,)
            if any(edges[index] >= edges[index + 1] for index in range(100)):
                raise DataContractError(
                    "chip bin edge collapsed",
                    details={"field": "edges", "rule": "bin_edge_collapse"},
                )
        weights: tuple[Decimal, ...] = ()
        for bar in bars:
            daily = _daily_weights(bar, edges)
            ratio = min(bar.turnover_percent / Decimal(100), Decimal(1))
            weights = (
                daily
                if not weights
                else tuple(
                    old * (Decimal(1) - ratio) + new * ratio
                    for old, new in zip(weights, daily, strict=True)
                )
            )
        quantized = [weight.quantize(QUANTUM, rounding=ROUND_HALF_EVEN) for weight in weights]
        winner = min(
            (index for index, weight in enumerate(quantized) if weight == max(quantized)), default=0
        )
        quantized[winner] += Decimal(1) - sum(quantized)
        if any(weight < 0 or weight > 1 for weight in quantized) or sum(quantized) != Decimal(1):
            raise DataContractError(
                "chip quantized weights invalid",
                details={"field": "weights", "rule": "quantized_sum"},
            )
        mids = tuple(
            (edges[index] + edges[index + 1]) / Decimal(2) for index in range(len(quantized))
        )
        average = sum(
            (mid * weight for mid, weight in zip(mids, quantized, strict=True)), Decimal(0)
        )
        profit = sum(
            (weight for mid, weight in zip(mids, quantized, strict=True) if mid <= bars[-1].close),
            Decimal(0),
        )
        return DerivedChip(
            edges,
            tuple(quantized),
            average,
            profit,
            _band(mids, tuple(quantized), Decimal("0.05"), Decimal("0.95")),
            _band(mids, tuple(quantized), Decimal("0.15"), Decimal("0.85")),
        )


def _daily_weights(bar: ChipInputBar, edges: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if bar.low == bar.high:
        index = next(
            i
            for i in range(len(edges) - 1)
            if edges[i] <= bar.low < edges[i + 1]
            or (i == len(edges) - 2 and bar.low == edges[i + 1])
        )
        return tuple(Decimal(1) if i == index else Decimal(0) for i in range(len(edges) - 1))
    span = bar.high - bar.low
    positive: list[int] = []
    raw = [Decimal(0)] * (len(edges) - 1)
    for i in range(len(raw)):
        overlap = max(Decimal(0), min(bar.high, edges[i + 1]) - max(bar.low, edges[i]))
        if overlap > 0:
            positive.append(i)
            raw[i] = overlap / span
    if not positive:
        raise DataContractError(
            "chip daily range has no bin", details={"field": "bars", "rule": "bin_assignment"}
        )
    residual = Decimal(1) - sum(raw[i] for i in positive[:-1])
    if residual < 0 or residual > 1:
        raise DataContractError(
            "chip daily residual is outside the probability range",
            details={"field": "bars", "rule": "daily_residual"},
        )
    raw[positive[-1]] = residual
    return tuple(raw)


def _band(
    mids: tuple[Decimal, ...], weights: tuple[Decimal, ...], low_q: Decimal, high_q: Decimal
) -> Decimal:
    def percentile(q: Decimal) -> Decimal:
        total = Decimal(0)
        for mid, weight in zip(mids, weights, strict=True):
            total += weight
            if total >= q:
                return mid
        return mids[-1]

    low, high = percentile(low_q), percentile(high_q)
    if low + high <= 0:
        raise DataContractError(
            "chip percentile denominator invalid",
            details={"field": "concentration", "rule": "denominator"},
        )
    return (high - low) / (high + low)
