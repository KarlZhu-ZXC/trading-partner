"""Yahoo active specific CME contract mapping tests."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from domain.common.enums import AdjustmentMethod, AssetType, DataCategory, Market
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from infrastructure.providers.us.yahoo_finance import YahooFinanceAdapter
from infrastructure.system.clock import SystemClock

NY = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 7, 24, 16, 0, tzinfo=NY)


def _chart_payload() -> dict[object, object]:
    day = AS_OF.date()
    ts = int(datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY).timestamp())
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "symbol": "GCZ26.CMX",
                        "regularMarketPrice": 2347.5,
                        "regularMarketTime": ts,
                        "regularMarketVolume": 1000,
                        "regularMarketOpen": 2340.0,
                        "regularMarketDayHigh": 2350.0,
                        "regularMarketDayLow": 2330.0,
                        "previousClose": 2340.0,
                        "fiftyTwoWeekLow": 2000.0,
                        "fiftyTwoWeekHigh": 2500.0,
                        "currentTradingPeriod": {
                            "pre": {"start": ts - 3600, "end": ts - 1800},
                            "regular": {"start": ts - 1800, "end": ts + 1800},
                            "post": {"start": ts + 1800, "end": ts + 3600},
                        },
                    },
                    "timestamp": [ts],
                    "indicators": {
                        "quote": [
                            {
                                "open": [2340.0],
                                "high": [2350.0],
                                "low": [2330.0],
                                "close": [2347.5],
                                "volume": [1000],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


class RecordingTransport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=self.body,
        )


class _FixedClock(SystemClock):
    def now(self) -> datetime:  # type: ignore[override]
        return AS_OF.astimezone(UTC) + timedelta(hours=1)


def _cme_instrument() -> Instrument:
    return Instrument(
        instrument_id="future:CME:GCZ26",
        symbol="GCZ26",
        name="COMEX Gold Dec 2026",
        market=Market.CME,
        exchange="COMEX",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.FUTURE,
    )


def _us_proxy() -> Instrument:
    return Instrument(
        instrument_id="future:US:GC=F",
        symbol="GC=F",
        name="Gold Continuous",
        market=Market.US,
        exchange="COMEX",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.FUTURE,
    )


@pytest.mark.asyncio
async def test_yahoo_maps_gcz26_to_cmx_not_continuous_proxy() -> None:
    transport = RecordingTransport(json.dumps(_chart_payload()).encode("utf-8"))
    adapter = YahooFinanceAdapter(transport, clock=_FixedClock())
    result = await adapter.get_quote(_cme_instrument(), AS_OF.astimezone(UTC))
    assert result.value.instrument_id == "future:CME:GCZ26"
    assert result.value.last == Decimal("2347.5")
    assert transport.requests[0].url.endswith("GCZ26.CMX")
    assert "GC%3DF" not in transport.requests[0].url
    assert "FUTURES_CONTRACT_NOT_SPOT" in result.meta.warnings
    assert "CONTINUOUS_FUTURES_ROLL_RISK" not in result.meta.warnings
    assert "YAHOO_ACTIVE_CONTRACT_NO_EXPIRED_HISTORY" in result.meta.warnings


@pytest.mark.asyncio
async def test_yahoo_specific_bars_use_cmx_symbol() -> None:
    transport = RecordingTransport(json.dumps(_chart_payload()).encode("utf-8"))
    adapter = YahooFinanceAdapter(transport, clock=_FixedClock())
    result = await adapter.get_bars(
        _cme_instrument(),
        start=date(2026, 7, 20),
        end=date(2026, 7, 24),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF.astimezone(UTC),
    )
    assert result.value.instrument_id == "future:CME:GCZ26"
    assert transport.requests[0].url.endswith("GCZ26.CMX")


def test_yahoo_rejects_invalid_cme_root() -> None:
    transport = RecordingTransport(b"{}")
    adapter = YahooFinanceAdapter(transport, clock=_FixedClock())
    bad = Instrument(
        instrument_id="future:CME:CLZ26",
        symbol="CLZ26",
        name="bad",
        market=Market.CME,
        exchange="NYMEX",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.FUTURE,
    )
    with pytest.raises(DataContractError):
        adapter._require_chart_instrument(bad)


def test_yahoo_supports_cme_and_us() -> None:
    adapter = YahooFinanceAdapter(RecordingTransport(b"{}"))
    assert adapter.supports(Market.CME, DataCategory.MARKET_QUOTE)
    assert adapter.supports(Market.US, DataCategory.MARKET_OHLCV)
    # Continuous US proxy still maps to GC=F symbol.
    assert adapter._require_chart_instrument(_us_proxy()) == "GC=F"
