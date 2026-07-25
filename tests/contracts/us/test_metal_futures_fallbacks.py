"""Lean contracts for the Phase 3A free metal-futures fallbacks."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import AdjustmentMethod, AssetType, DataCategory, Market, VendorId
from domain.common.errors import NoMarketData
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from infrastructure.providers.a_share.eastmoney_gate import (
    create_isolated_eastmoney_request_gate_for_tests,
)
from infrastructure.providers.us.eastmoney_futures import EastmoneyMetalFuturesAdapter
from infrastructure.providers.us.sina_futures import SinaMetalFuturesAdapter

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 23, 16, 0, tzinfo=SHANGHAI)


def _future(symbol: str, exchange: str = "COMEX") -> Instrument:
    return Instrument(
        instrument_id=f"future:US:{symbol}",
        symbol=symbol,
        name=f"{symbol} continuous future",
        market=Market.US,
        exchange=exchange,
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.FUTURE,
    )


class RecordingTransport:
    def __init__(self, body: bytes, *, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status_code=self.status_code,
            headers={"content-type": "application/json; charset=utf-8"},
            body=self.body,
        )


def _gate():  # type: ignore[no-untyped-def]
    return create_isolated_eastmoney_request_gate_for_tests(
        min_interval_seconds=0.001,
        jitter_seconds=0,
    )


@pytest.mark.asyncio
async def test_sina_quote_preserves_timestamp_and_unknown_sla() -> None:
    body = (
        'var hq_str_hf_GC="4093.892,,4095.700,4096.000,4144.000,4074.600,'
        '15:52:00,4151.900,4126.000,0,1,1,2026-07-23,纽约黄金,0";'
    ).encode("gb18030")
    transport = RecordingTransport(body)
    adapter = SinaMetalFuturesAdapter(transport, clock=FixedClock(NOW))

    result = await adapter.get_quote(_future("GC=F"), NOW)

    assert result.value.last == Decimal("4093.892")
    assert result.value.quote_at == datetime(2026, 7, 23, 15, 52, tzinfo=SHANGHAI)
    assert result.value.previous_close is None  # wire value is prior settlement
    assert result.meta.vendor is VendorId.SINA_FUTURES
    assert result.meta.category is DataCategory.MARKET_QUOTE
    assert result.meta.data_delay_seconds == 480
    assert "BEST_EFFORT_PUBLIC_FEED_NO_SLA" in result.meta.warnings
    assert transport.requests[0].params == {"list": "hf_GC"}


@pytest.mark.asyncio
async def test_sina_quote_does_not_substitute_unsupported_metal_contract() -> None:
    transport = RecordingTransport(b"")
    adapter = SinaMetalFuturesAdapter(transport, clock=FixedClock(NOW))

    with pytest.raises(NoMarketData):
        await adapter.get_quote(_future("PL=F", "NYMEX"), NOW)

    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interval", "expected_count", "expected_open", "expected_close", "expected_volume"),
    [
        (USBarInterval.ONE_DAY, 3, "4005.6", "4135.0", "346222"),
        (USBarInterval.ONE_WEEK, 1, "4005.6", "4135.0", "346222"),
    ],
)
async def test_eastmoney_daily_and_derived_weekly_bars(
    interval: USBarInterval,
    expected_count: int,
    expected_open: str,
    expected_close: str,
    expected_volume: str,
) -> None:
    rows = [
        "2026-07-20,4005.6,4011.8,4046.0,3986.5,100374,0,0,0,0,0,0,0,0",
        "2026-07-21,4013.4,4082.2,4092.5,4003.3,117248,0,0,0,0,0,0,0,0",
        "2026-07-22,4084.7,4135.0,4171.4,4081.0,128600,0,0,0,0,0,0,0,0",
        # Current date is after this test's as_of and must not leak through.
        "2026-07-23,4126.0,4097.9,4144.0,4074.6,34820,0,0,0,0,0,0,0,0",
    ]
    payload = json.dumps({"data": {"code": "GC00Y", "name": "COMEX黄金", "klines": rows}})
    transport = RecordingTransport(payload.encode())
    as_of = datetime(2026, 7, 22, 21, 0, tzinfo=UTC)
    adapter = EastmoneyMetalFuturesAdapter(
        transport,
        _gate(),
        clock=FixedClock(as_of),
    )

    result = await adapter.get_bars(
        _future("GC=F"),
        start=date(2026, 7, 20),
        end=date(2026, 7, 23),
        interval=interval,
        adjustment=AdjustmentMethod.NONE,
        as_of=as_of,
    )

    assert len(result.value.bars) == expected_count
    assert result.value.bars[0].open == Decimal(expected_open)
    assert result.value.bars[-1].close == Decimal(expected_close)
    assert result.value.bars[-1].timestamp.hour == 17
    assert sum((bar.volume for bar in result.value.bars), Decimal(0)) == Decimal(
        expected_volume
    )
    assert result.meta.vendor is VendorId.EASTMONEY_FUTURES
    assert "EASTMONEY_DAILY_DERIVED_BARS" in result.meta.warnings
    assert transport.requests[0].params["secid"] == "101.GC00Y"


@pytest.mark.asyncio
async def test_eastmoney_does_not_fabricate_intraday_ohlcv() -> None:
    transport = RecordingTransport(b"{}")
    adapter = EastmoneyMetalFuturesAdapter(
        transport,
        _gate(),
        clock=FixedClock(NOW),
    )

    with pytest.raises(NoMarketData, match="intraday"):
        await adapter.get_bars(
            _future("SI=F"),
            start=date(2026, 7, 23),
            end=date(2026, 7, 23),
            interval=USBarInterval.SIXTY_MINUTES,
            adjustment=AdjustmentMethod.NONE,
            as_of=NOW,
        )

    assert transport.requests == []
