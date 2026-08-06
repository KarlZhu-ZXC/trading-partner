"""Dukascopy free API codec contract tests: success fixtures + malformed fail-closed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from domain.common.errors import DataContractError
from domain.us_market.enums import USBarInterval
from infrastructure.providers.cross_asset.dukascopy_codecs import (
    decode_current_prices,
    decode_historical_prices,
    decode_instrument_list,
    decode_jetta_candles,
    dukascopy_instrument_code,
    dukascopy_jetta_instrument_code,
    instrument_id_for_dukascopy_code,
    loads_dukascopy_json,
    supported_bar_intervals,
    timeframe_for_interval,
)

_QUOTE_OK = [
    {
        "instrument": "XAU/USD",
        "bid": "2348.10",
        "ask": "2348.40",
        "last": "2348.25",
        "timestamp": 1_721_779_200_000,
    }
]

_BARS_OK = [
    {
        "timestamp": 1_721_779_200_000,
        "open": "32.10",
        "high": "32.40",
        "low": "32.00",
        "close": "32.25",
        "volume": "120.5",
    },
    {
        "timestamp": 1_721_782_800_000,
        "open": "32.25",
        "high": "32.50",
        "low": "32.20",
        "close": "32.45",
        "volume": "98.0",
    },
]


def test_decode_current_prices_xau_quote() -> None:
    rows = decode_current_prices(_QUOTE_OK)
    assert len(rows) == 1
    assert rows[0].instrument_code == "XAU/USD"
    assert rows[0].bid == Decimal("2348.10")
    assert rows[0].ask == Decimal("2348.40")
    assert rows[0].quote_at == datetime(2024, 7, 24, 0, 0, tzinfo=UTC)


def test_decode_historical_prices_1h_bars() -> None:
    bars = decode_historical_prices(_BARS_OK)
    assert len(bars) == 2
    assert bars[0].open == Decimal("32.10")
    assert bars[1].close == Decimal("32.45")
    assert bars[0].timestamp < bars[1].timestamp


def test_decode_jetta_delta_candles_without_synthetic_flats() -> None:
    bars = decode_jetta_candles(
        {
            "timestamp": 1_721_779_200_000,
            "multiplier": Decimal("0.01"),
            "shift": 60_000,
            "open": Decimal("32.10"),
            "high": Decimal("32.40"),
            "low": Decimal("32.00"),
            "close": Decimal("32.25"),
            "times": [0, 2],
            "opens": [0, 15],
            "highs": [0, 10],
            "lows": [0, 20],
            "closes": [0, 20],
            "volumes": [Decimal("1.25"), Decimal("2.5")],
        }
    )
    assert len(bars) == 2
    assert bars[1].timestamp - bars[0].timestamp == timedelta(minutes=2)
    assert bars[1].open == Decimal("32.25")
    assert bars[1].close == Decimal("32.45")
    assert bars[1].volume == Decimal("2.5")


def test_decode_instrument_list_and_identity_map() -> None:
    rows = decode_instrument_list(
        [
            {"id": 1001, "name": "XAU/USD", "description": "Gold"},
            {"id": "1002", "instrument": "XAG/USD"},
            {"code": "COPPER.CMD/USD"},
        ]
    )
    assert [row.code for row in rows] == ["XAU/USD", "XAG/USD", "COPPER.CMD/USD"]
    assert [row.instrument_id for row in rows] == [1001, 1002, None]
    assert dukascopy_instrument_code("commodity_spot:OTC:XAUUSD") == "XAU/USD"
    assert dukascopy_instrument_code("cfd:OTC:COPPER_CMD_USD") == "COPPER.CMD/USD"
    assert dukascopy_instrument_code("cfd:OTC:LIGHT_CMD_USD") == "LIGHT.CMD/USD"
    assert instrument_id_for_dukascopy_code("LIGHT.CMD/USD") == "cfd:OTC:LIGHT_CMD_USD"
    assert dukascopy_jetta_instrument_code("commodity_spot:OTC:XAUUSD") == "XAU-USD"
    assert dukascopy_jetta_instrument_code("cfd:OTC:COPPER_CMD_USD") == "COPPER.CMD-USD"
    assert dukascopy_jetta_instrument_code("cfd:OTC:LIGHT_CMD_USD") == "LIGHT.CMD-USD"


def test_decode_instrument_list_rejects_non_integer_id() -> None:
    with pytest.raises(DataContractError):
        decode_instrument_list([{"id": "not-an-id", "name": "XAU/USD"}])


def test_verified_intervals_only() -> None:
    assert USBarInterval.ONE_MINUTE in supported_bar_intervals()
    assert USBarInterval.SIXTY_MINUTES in supported_bar_intervals()
    assert USBarInterval.ONE_DAY in supported_bar_intervals()
    assert USBarInterval.FIVE_MINUTES not in supported_bar_intervals()
    assert timeframe_for_interval(USBarInterval.SIXTY_MINUTES) == "1hour"
    with pytest.raises(DataContractError):
        timeframe_for_interval(USBarInterval.FIVE_MINUTES)


def test_loads_json_rejects_malformed() -> None:
    with pytest.raises(DataContractError):
        loads_dukascopy_json(b"not-json", operation="currentPrices")


def test_decode_current_prices_malformed_payload() -> None:
    with pytest.raises(DataContractError):
        decode_current_prices("not-a-list")


def test_decode_historical_prices_malformed_ohlc() -> None:
    with pytest.raises(DataContractError):
        decode_historical_prices(
            [
                {
                    "timestamp": 1_721_779_200_000,
                    "open": "10",
                    "high": "9",
                    "low": "8",
                    "close": "9.5",
                    "volume": "1",
                }
            ]
        )
