"""Native futures date labels must not duplicate a daily fact before aggregation."""

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from test_yahoo_finance import NY, RecordingTransport, _chart_payload, _future_instrument

from conftest import FixedClock
from domain.common.enums import AdjustmentMethod
from domain.common.errors import DataContractError
from domain.us_market.enums import USBarInterval
from infrastructure.providers.us.yahoo_finance import YahooFinanceAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["SI=F", "GC=F"])
@pytest.mark.parametrize(
    "interval", [USBarInterval.ONE_DAY, USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH]
)
async def test_duplicate_local_daily_dates_fail_before_volume_aggregation(symbol, interval):
    day = date(2026, 7, 24)
    payload = _chart_payload(
        days=[day, day], opens=[100.0, 100.0], highs=[101.0, 101.0],
        lows=[99.0, 99.0], closes=[100.0, 100.0], volumes=[10, 10], symbol=symbol,
    )
    payload["chart"]["result"][0]["timestamp"][1] += 3600
    instrument = replace(_future_instrument(), symbol=symbol, instrument_id=f"future:US:{symbol}")
    as_of = datetime(2026, 7, 25, 0, tzinfo=NY)
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of)
    )
    with pytest.raises(DataContractError) as captured:
        await adapter.get_bars(
            instrument, start=day, end=day, interval=interval,
            adjustment=AdjustmentMethod.NONE, as_of=as_of,
        )
    assert captured.value.details["rule"] == "unique_daily_timestamps"


@pytest.mark.asyncio
async def test_distinct_local_dates_on_same_utc_date_keep_native_labels_and_volume():
    days = [date(2026, 7, 23), date(2026, 7, 24)]
    payload = _chart_payload(
        days=days, opens=[100.0, 100.0], highs=[101.0, 101.0], lows=[99.0, 99.0],
        closes=[100.0, 100.0], volumes=[10, 10], symbol="SI=F",
    )
    timestamps = [datetime(2026, 7, 23, 23, tzinfo=NY), datetime(2026, 7, 24, 0, tzinfo=NY)]
    payload["chart"]["result"][0]["timestamp"] = [int(ts.timestamp()) for ts in timestamps]
    instrument = replace(_future_instrument(), symbol="SI=F", instrument_id="future:US:SI=F")
    as_of = datetime(2026, 7, 25, 0, tzinfo=NY)
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of)
    )
    result = await adapter.get_bars(
        instrument, start=days[0], end=days[1], interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )
    assert [bar.timestamp for bar in result.value.bars] == timestamps
    assert sum(bar.volume for bar in result.value.bars) == Decimal("20")
