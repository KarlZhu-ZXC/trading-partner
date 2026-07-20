"""E4d frozen golden/adversarial coverage for ``tp_chip_v1``."""
# ruff: noqa: E501

from __future__ import annotations

from decimal import Context, Decimal, localcontext

import pytest

from domain.common.errors import DataContractError
from infrastructure.providers.a_share.chip_distribution import (
    QUANTUM,
    ChipInputBar,
    derive_tp_chip_v1,
)


def _bars(
    *, low: str = "10", high: str = "20", close: str = "15", turnover: str = "0"
) -> list[ChipInputBar]:
    return [
        ChipInputBar(Decimal(low), Decimal(high), Decimal(close), Decimal(turnover))
        for _ in range(120)
    ]


def test_constant_price_is_one_bin_and_exact_sum() -> None:
    result = derive_tp_chip_v1(_bars(low="10", high="10", close="10"))
    assert result.edges == (Decimal("10"), Decimal("10"))
    assert result.weights == (Decimal("1.000000000000"),)
    assert result.average_cost == Decimal("10")
    assert result.profit_ratio == Decimal("1.000000000000")


def test_zero_turnover_retains_first_day_distribution() -> None:
    bars = _bars(low="1", high="101", close="1", turnover="0")
    bars[0] = ChipInputBar(Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"))
    result = derive_tp_chip_v1(bars)
    assert result.weights[0] == Decimal("1.000000000000")
    assert sum(result.weights) == Decimal(1)


def test_full_turnover_replaces_prior_distribution() -> None:
    bars = _bars(low="1", high="101", close="101", turnover="0")
    bars[0] = ChipInputBar(Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"))
    bars[-1] = ChipInputBar(Decimal("101"), Decimal("101"), Decimal("101"), Decimal("100"))
    result = derive_tp_chip_v1(bars)
    assert result.weights[-1] == Decimal("1.000000000000")


def test_turnover_decay_half_is_exact() -> None:
    bars = _bars(low="1", high="3", close="3", turnover="0")
    bars[0] = ChipInputBar(Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"))
    bars[-1] = ChipInputBar(Decimal("3"), Decimal("3"), Decimal("3"), Decimal("50"))
    result = derive_tp_chip_v1(bars)
    assert result.weights[0] == Decimal("0.500000000000")
    assert result.weights[-1] == Decimal("0.500000000000")


def test_shared_boundary_goes_to_higher_bin_and_quantizes_exactly() -> None:
    bars = _bars(low="1", high="101", close="51", turnover="0")
    bars[0] = ChipInputBar(Decimal("51"), Decimal("51"), Decimal("51"), Decimal("0"))
    result = derive_tp_chip_v1(bars)
    assert result.weights[50] == Decimal("1.000000000000")
    assert all(weight == weight.quantize(QUANTUM) for weight in result.weights)
    assert sum(result.weights) == Decimal(1)


def test_quantiles_and_relative_band_are_bounded() -> None:
    result = derive_tp_chip_v1(_bars(low="1", high="101", close="51", turnover="100"))
    assert Decimal(0) <= result.concentration_90 <= Decimal(1)
    assert Decimal(0) <= result.concentration_70 <= Decimal(1)


@pytest.mark.parametrize(
    "bars",
    [
        _bars()[:-1],
        _bars(turnover="-1"),
        _bars(low="0"),
    ],
)
def test_invalid_input_fails_closed(bars: list[ChipInputBar]) -> None:
    with pytest.raises(DataContractError):
        derive_tp_chip_v1(bars)


def test_bin_edge_collapse_fails_closed() -> None:
    bars = _bars(
        low="10000000000000000000000000000000000000000000000000",
        high="10000000000000000000000000000000000000000000000001",
        close="10000000000000000000000000000000000000000000000000",
    )
    with pytest.raises(DataContractError) as exc:
        derive_tp_chip_v1(bars)
    assert exc.value.details.get("rule") == "bin_edge_collapse"


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_nonfinite_values_fail_closed(bad: Decimal) -> None:
    bars = _bars()
    bars[0] = ChipInputBar(Decimal("10"), Decimal("20"), Decimal("15"), bad)
    with pytest.raises(DataContractError) as exc:
        derive_tp_chip_v1(bars)
    assert exc.value.details.get("rule") == "finite"


def test_close_outside_daily_range_fails_closed() -> None:
    bars = _bars()
    bars[0] = ChipInputBar(Decimal("10"), Decimal("20"), Decimal("21"), Decimal("1"))
    with pytest.raises(DataContractError) as exc:
        derive_tp_chip_v1(bars)
    assert exc.value.details.get("rule") == "range"


def test_global_decimal_context_cannot_change_golden_result_or_residual() -> None:
    bars = _bars(low="1", high="4", close="3", turnover="33.333333333333333333")
    expected = derive_tp_chip_v1(bars)
    with localcontext(Context(prec=6)):
        actual = derive_tp_chip_v1(bars)
    assert actual == expected
    assert sum(actual.weights) == Decimal("1")
    assert all(weight == weight.quantize(QUANTUM) for weight in actual.weights)


def test_wrong_runtime_input_type_is_typed_contract_error() -> None:
    bars = _bars()
    bars[0] = ChipInputBar(10, Decimal("20"), Decimal("15"), Decimal("1"))  # type: ignore[arg-type]
    with pytest.raises(DataContractError) as exc:
        derive_tp_chip_v1(bars)
    assert exc.value.details.get("rule") == "type"
