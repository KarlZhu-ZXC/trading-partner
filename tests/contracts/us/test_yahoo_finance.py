"""Phase 1F F2a: Yahoo Finance chart adapter focused contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from application.ports.market_session_calendar import MarketSession
from conftest import FixedClock
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    DataCategory,
    Market,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from infrastructure.providers.us.yahoo_finance import YahooFinanceAdapter

NY = ZoneInfo("America/New_York")
SEOUL = ZoneInfo("Asia/Seoul")
# Friday regular session (not weekend closed).
AS_OF = datetime(2026, 7, 17, 16, 5, tzinfo=NY)
CLOCK = FixedClock(AS_OF)


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:US:NVDA",
        symbol="NVDA",
        name="NVIDIA",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _future_instrument() -> Instrument:
    return Instrument(
        instrument_id="future:US:GC=F",
        symbol="GC=F",
        name="COMEX Gold Futures Continuous",
        market=Market.US,
        exchange="COMEX",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.FUTURE,
    )


@pytest.mark.parametrize(
    ("symbol", "yahoo_symbol"),
    (("SPX", "^GSPC"), ("NDX", "^NDX"), ("IXIC", "^IXIC")),
)
def test_yahoo_maps_canonical_us_indexes_to_chart_symbols(
    symbol: str,
    yahoo_symbol: str,
) -> None:
    adapter = YahooFinanceAdapter(RecordingTransport(body=b"{}"), clock=CLOCK)
    instrument = Instrument(
        instrument_id=f"index:US:{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.US,
        exchange="INDEX",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.INDEX,
    )

    assert adapter._require_chart_instrument(instrument) == yahoo_symbol


def _kr_instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:KR:005930",
        symbol="005930",
        name="Samsung Electronics",
        market=Market.KR,
        exchange="KOSPI",
        currency="KRW",
        timezone="Asia/Seoul",
        asset_type=AssetType.EQUITY,
    )


def _unix(dt: datetime) -> int:
    return int(dt.timestamp())


def _chart_payload(
    *,
    days: list[date],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float | None],
    volumes: list[int],
    adjcloses: list[float] | None = None,
    regular_market_time: datetime | None = None,
    regular_market_price: float | None = None,
    include_adjclose: bool = True,
    trading_period_at: datetime | None = None,
    chart_previous_close: float = 119.0,
    previous_close: float | None = 119.0,
    regular_market_previous_close: float | None = None,
    symbol: str = "NVDA",
) -> dict[str, Any]:
    timestamps = [_unix(datetime(d.year, d.month, d.day, 0, 0, tzinfo=NY)) for d in days]
    rmt = regular_market_time or datetime(
        days[-1].year, days[-1].month, days[-1].day, 16, 0, tzinfo=NY
    )
    price = regular_market_price if regular_market_price is not None else closes[-1]
    # Trading windows may describe a later pre/post session than regularMarketTime.
    day = (trading_period_at or rmt).astimezone(NY).date()
    pre_s = _unix(datetime(day.year, day.month, day.day, 4, 0, tzinfo=NY))
    reg_s = _unix(datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY))
    reg_e = _unix(datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY))
    post_e = _unix(datetime(day.year, day.month, day.day, 20, 0, tzinfo=NY))
    indicators: dict[str, Any] = {
        "quote": [
            {
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": volumes,
            }
        ]
    }
    if include_adjclose:
        indicators["adjclose"] = [{"adjclose": adjcloses if adjcloses is not None else closes}]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "symbol": symbol,
                        "exchangeTimezoneName": "America/New_York",
                        "regularMarketTime": _unix(rmt),
                        "regularMarketPrice": price,
                        "regularMarketVolume": volumes[-1],
                        "chartPreviousClose": chart_previous_close,
                        "previousClose": previous_close,
                        "regularMarketPreviousClose": regular_market_previous_close,
                        "regularMarketOpen": opens[-1],
                        "regularMarketDayHigh": highs[-1],
                        "regularMarketDayLow": lows[-1],
                        "fiftyTwoWeekLow": 90.0,
                        "fiftyTwoWeekHigh": 140.0,
                        "currentTradingPeriod": {
                            "pre": {
                                "timezone": "EDT",
                                "start": pre_s,
                                "end": reg_s,
                                "gmtoffset": -14400,
                            },
                            "regular": {
                                "timezone": "EDT",
                                "start": reg_s,
                                "end": reg_e,
                                "gmtoffset": -14400,
                            },
                            "post": {
                                "timezone": "EDT",
                                "start": reg_e,
                                "end": post_e,
                                "gmtoffset": -14400,
                            },
                        },
                    },
                    "timestamp": timestamps,
                    "indicators": indicators,
                }
            ],
            "error": None,
        }
    }


class RecordingTransport:
    def __init__(
        self,
        *,
        body: bytes,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json; charset=utf-8"}
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status_code=self.status_code,
            headers=self.headers,
            body=self.body,
        )


class SequenceTransport:
    def __init__(self, bodies: list[bytes]) -> None:
        self._bodies = bodies
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        index = len(self.requests) - 1
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json; charset=utf-8"},
            body=self._bodies[index],
        )


def _intraday_body(
    *,
    timestamps: list[datetime],
    closes: list[float],
    regular_market_time: datetime,
    regular_market_price: float,
    trading_period_at: datetime,
    symbol: str = "NVDA",
) -> bytes:
    payload = _chart_payload(
        days=[item.astimezone(NY).date() for item in timestamps],
        opens=closes,
        highs=closes,
        lows=closes,
        closes=closes,
        volumes=[0] * len(closes),
        regular_market_time=regular_market_time,
        regular_market_price=regular_market_price,
        trading_period_at=trading_period_at,
        include_adjclose=False,
        symbol=symbol,
    )
    result = payload["chart"]["result"][0]
    result["timestamp"] = [_unix(item) for item in timestamps]
    return json.dumps(payload).encode("utf-8")


def _success_days() -> list[date]:
    # Mon–Fri week ending 2026-07-17
    return [
        date(2026, 7, 13),
        date(2026, 7, 14),
        date(2026, 7, 15),
        date(2026, 7, 16),
        date(2026, 7, 17),
    ]


def _success_body(**kwargs: Any) -> bytes:
    days = _success_days()
    payload = _chart_payload(
        days=days,
        opens=[118.0, 119.0, 120.0, 121.0, 122.0],
        highs=[119.0, 120.0, 121.0, 122.0, 123.0],
        lows=[117.0, 118.0, 119.0, 120.0, 121.0],
        closes=[118.5, 119.5, 120.5, 121.5, 122.5],
        volumes=[1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000],
        regular_market_time=datetime(2026, 7, 17, 16, 0, tzinfo=NY),
        regular_market_price=122.5,
        **kwargs,
    )
    return json.dumps(payload).encode("utf-8")


def _adapter(transport: RecordingTransport) -> YahooFinanceAdapter:
    return YahooFinanceAdapter(
        transport,
        clock=CLOCK,
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


@pytest.mark.asyncio
async def test_yahoo_quote_and_bars_success() -> None:
    transport = RecordingTransport(body=_success_body())
    adapter = _adapter(transport)

    quote = await adapter.get_quote(_instrument(), AS_OF)
    assert quote.meta.vendor is VendorId.YFINANCE
    assert quote.meta.category is DataCategory.MARKET_QUOTE
    assert quote.value.instrument_id == "equity:US:NVDA"
    assert quote.value.last == Decimal("122.5")
    assert type(quote.value.last) is Decimal
    assert quote.value.quote_at <= AS_OF
    assert quote.value.session.value in {
        "pre_market",
        "regular",
        "post_market",
        "closed",
    }
    assert quote.value.previous_close == Decimal("121.5")

    bars = await adapter.get_bars(
        _instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    assert bars.meta.vendor is VendorId.YFINANCE
    assert bars.meta.category is DataCategory.MARKET_OHLCV
    assert bars.meta.adjustment is AdjustmentMethod.NONE
    assert bars.value.end == date(2026, 7, 17)
    assert len(bars.value.bars) == 5
    # Inclusive end: last bar is 2026-07-17 session close NY.
    assert bars.value.bars[-1].timestamp == datetime(2026, 7, 17, 16, 0, tzinfo=NY)
    assert bars.value.bars[-1].close == Decimal("122.5")
    assert all(b.timestamp <= AS_OF for b in bars.value.bars)

    # Fixed host/path + GET only; symbol URL-encoded in path.
    assert transport.requests
    for req in transport.requests:
        assert req.method == "GET"
        parts = urlsplit(req.url)
        assert parts.scheme == "https"
        assert parts.hostname == "query1.finance.yahoo.com"
        assert parts.path == "/v8/finance/chart/NVDA"


@pytest.mark.asyncio
async def test_end_inclusive_and_as_of_cutoff() -> None:
    # Extra future day after as_of must be filtered even if present in payload.
    days = _success_days() + [date(2026, 7, 20)]
    payload = _chart_payload(
        days=days,
        opens=[118.0, 119.0, 120.0, 121.0, 122.0, 999.0],
        highs=[119.0, 120.0, 121.0, 122.0, 123.0, 999.0],
        lows=[117.0, 118.0, 119.0, 120.0, 121.0, 998.0],
        closes=[118.5, 119.5, 120.5, 121.5, 122.5, 999.0],
        volumes=[1, 1, 1, 1, 1, 1],
        regular_market_time=datetime(2026, 7, 20, 16, 0, tzinfo=NY),
        regular_market_price=999.0,
    )
    transport = RecordingTransport(body=json.dumps(payload).encode("utf-8"))
    adapter = _adapter(transport)

    result = await adapter.get_bars(
        _instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    assert result.value.bars[-1].timestamp.astimezone(NY).date() == date(2026, 7, 17)
    assert all(b.close != Decimal("999.0") for b in result.value.bars)

    # period2 should request exclusive day after inclusive end.
    bars_req = transport.requests[-1]
    period2 = int(bars_req.params["period2"])
    exclusive = int(datetime(2026, 7, 18, 0, 0, tzinfo=NY).timestamp())
    assert period2 == exclusive

    # Quote must not use regularMarketTime after as_of.
    quote = await adapter.get_quote(_instrument(), AS_OF)
    assert quote.value.quote_at <= AS_OF
    assert quote.value.last != Decimal("999.0")
    assert quote.value.previous_close == Decimal("121.5")
    assert quote.value.week_52_high is None


@pytest.mark.asyncio
async def test_quote_previous_close_ignores_chart_window_baseline() -> None:
    """TTWO regression: chartPreviousClose is period1 baseline, not prior day."""
    as_of = datetime(2026, 7, 23, 11, 30, tzinfo=NY)
    payload = _chart_payload(
        days=[date(2026, 6, 23), date(2026, 7, 22), date(2026, 7, 23)],
        opens=[240.0, 236.24, 232.0],
        highs=[243.0, 236.5, 233.18],
        lows=[239.0, 232.57, 229.11],
        closes=[242.64, 233.58, 229.43],
        volumes=[1_000_000, 1_273_800, 340_645],
        regular_market_time=datetime(2026, 7, 23, 11, 17, tzinfo=NY),
        regular_market_price=229.43,
        chart_previous_close=239.57,
        previous_close=None,
    )
    transport = RecordingTransport(body=json.dumps(payload).encode("utf-8"))
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(as_of))

    result = await adapter.get_quote(_instrument(), as_of)

    assert result.value.last == Decimal("229.43")
    assert result.value.previous_close == Decimal("233.58")
    assert result.value.previous_close != Decimal("239.57")


@pytest.mark.asyncio
async def test_quote_selects_latest_premarket_minute_bar() -> None:
    as_of = datetime(2026, 7, 24, 5, 18, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 22), date(2026, 7, 23)],
        opens=[236.24, 232.0],
        highs=[236.5, 233.18],
        lows=[232.57, 229.11],
        closes=[233.58, 230.25],
        volumes=[1_273_800, 1_100_000],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    intraday = _intraday_body(
        timestamps=[
            datetime(2026, 7, 24, 5, 7, tzinfo=NY),
            datetime(2026, 7, 24, 5, 14, tzinfo=NY),
        ],
        # The recovered extended-hours price is above the stale regular high.
        closes=[230.33, 234.0],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert len(transport.requests) == 2
    assert transport.requests[1].params["interval"] == "1m"
    assert result.value.last == Decimal("234.0")
    assert result.value.quote_at == datetime(2026, 7, 24, 5, 14, tzinfo=NY)
    assert result.value.session.value == "pre_market"
    assert result.value.previous_close == Decimal("230.25")
    assert "EXTENDED_HOURS_PRICE" in result.meta.warnings
    assert "EXTENDED_HOURS_SESSION_RANGE_UNAVAILABLE" in result.meta.warnings
    assert result.value.open is None
    assert result.value.high is None
    assert result.value.low is None
    assert result.value.volume is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "bars"])
@pytest.mark.parametrize("symbol", [None, "", "SPY"])
async def test_yahoo_rejects_unverified_chart_identity(
    operation: str, symbol: str | None
) -> None:
    payload = json.loads(_success_body())
    meta = payload["chart"]["result"][0]["meta"]
    if symbol is None:
        meta.pop("symbol")
    else:
        meta["symbol"] = symbol
    adapter = _adapter(RecordingTransport(body=json.dumps(payload).encode()))

    with pytest.raises(DataContractError) as captured:
        if operation == "quote":
            await adapter.get_quote(_instrument(), AS_OF)
        else:
            await adapter.get_bars(
                _instrument(),
                start=date(2026, 7, 13),
                end=date(2026, 7, 17),
                interval=USBarInterval.ONE_DAY,
                adjustment=AdjustmentMethod.NONE,
                as_of=AS_OF,
            )

    assert captured.value.details["rule"] == "instrument_identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "bars"])
@pytest.mark.parametrize("row_count", [0, 2])
async def test_yahoo_requires_one_chart_result(operation: str, row_count: int) -> None:
    payload = json.loads(_success_body())
    payload["chart"]["result"] *= row_count
    adapter = _adapter(RecordingTransport(body=json.dumps(payload).encode()))
    expected_error = NoMarketData if row_count == 0 else DataContractError

    with pytest.raises(expected_error):
        if operation == "quote":
            await adapter.get_quote(_instrument(), AS_OF)
        else:
            await adapter.get_bars(
                _instrument(),
                start=date(2026, 7, 13),
                end=date(2026, 7, 17),
                interval=USBarInterval.ONE_DAY,
                adjustment=AdjustmentMethod.NONE,
                as_of=AS_OF,
            )


@pytest.mark.asyncio
async def test_yahoo_does_not_recover_from_another_instruments_intraday_price() -> None:
    intraday = _intraday_body(
        timestamps=[datetime(2026, 7, 17, 16, 4, tzinfo=NY)],
        closes=[500.0],
        regular_market_time=datetime(2026, 7, 17, 16, 0, tzinfo=NY),
        regular_market_price=500.0,
        trading_period_at=AS_OF,
        symbol="SPY",
    )
    transport = SequenceTransport([_success_body(), intraday])

    result = await _adapter(transport).get_quote(_instrument(), AS_OF)

    assert result.value.last == Decimal("122.5")
    assert result.value.quote_at == datetime(2026, 7, 17, 16, 0, tzinfo=NY)
    assert "INTRADAY_QUOTE_UNAVAILABLE" in result.meta.warnings
    assert len(transport.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("symbol", "yahoo_symbol"),
    (("SPX", "^GSPC"), ("NDX", "^NDX"), ("IXIC", "^IXIC")),
)
async def test_yahoo_chart_identity_accepts_the_mapped_index_symbol(
    symbol: str, yahoo_symbol: str
) -> None:
    instrument = Instrument(
        instrument_id=f"index:US:{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.US,
        exchange="INDEX",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.INDEX,
    )
    transport = RecordingTransport(body=_success_body(symbol=yahoo_symbol))

    result = await _adapter(transport).get_quote(instrument, AS_OF)

    assert result.value.instrument_id == instrument.instrument_id
    assert result.value.last == Decimal("122.5")


@pytest.mark.asyncio
async def test_premarket_previous_close_recovers_latest_completed_regular_session() -> None:
    """TSLA regression: never expose regularMarketPreviousClose as 前收."""
    as_of = datetime(2026, 7, 27, 8, 0, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 23), date(2026, 7, 24)],
        opens=[320.0, 320.88],
        highs=[323.0, 322.96],
        lows=[316.0, 306.51],
        closes=[319.69, None],
        volumes=[70_000_000, 62_648_724],
        regular_market_time=datetime(2026, 7, 24, 16, 0, 1, tzinfo=NY),
        regular_market_price=313.03,
        trading_period_at=as_of,
        chart_previous_close=319.69,
        previous_close=319.69,
        regular_market_previous_close=319.69,
    )
    intraday = _intraday_body(
        timestamps=[datetime(2026, 7, 27, 7, 59, tzinfo=NY)],
        closes=[317.1],
        regular_market_time=datetime(2026, 7, 24, 16, 0, 1, tzinfo=NY),
        regular_market_price=313.03,
        trading_period_at=as_of,
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert result.value.session is TradingSession.PRE_MARKET
    assert result.value.last == Decimal("317.1")
    assert result.value.previous_close == Decimal("313.03")
    assert result.value.previous_close != Decimal("319.69")
    assert "PREVIOUS_CLOSE_REGULAR_SESSION_RECOVERY" in result.meta.warnings


@pytest.mark.asyncio
async def test_premarket_without_current_print_keeps_prior_postmarket_basis() -> None:
    """A prior-day post-market print must not push previous_close back two days."""
    as_of = datetime(2026, 8, 6, 9, 15, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 8, 4), date(2026, 8, 5)],
        opens=[244.19, 242.76],
        highs=[246.25, 243.51],
        lows=[239.51, 232.34],
        closes=[240.22, 234.91],
        volumes=[2_096_600, 2_108_000],
        regular_market_time=datetime(2026, 8, 5, 16, 0, tzinfo=NY),
        regular_market_price=234.91,
    )
    intraday = _intraday_body(
        timestamps=[datetime(2026, 8, 5, 19, 59, tzinfo=NY)],
        closes=[237.7607],
        regular_market_time=datetime(2026, 8, 5, 16, 0, tzinfo=NY),
        regular_market_price=234.91,
        trading_period_at=datetime(2026, 8, 5, 19, 59, tzinfo=NY),
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert result.value.last == Decimal("237.7607")
    assert result.value.quote_at == datetime(2026, 8, 5, 19, 59, tzinfo=NY)
    assert result.value.session is TradingSession.POST_MARKET
    assert result.value.previous_close == Decimal("234.91")
    assert result.value.previous_close != Decimal("240.22")
    assert "EXTENDED_HOURS_PRICE" in result.meta.warnings
    assert "INTRADAY_QUOTE_UNAVAILABLE" in result.meta.warnings


@pytest.mark.asyncio
async def test_premarket_regular_fallback_keeps_its_own_session_baseline() -> None:
    """A regular-session fallback keeps that quote's prior regular close."""
    as_of = datetime(2026, 8, 6, 9, 15, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 8, 4), date(2026, 8, 5)],
        opens=[244.19, 242.76],
        highs=[246.25, 243.51],
        lows=[239.51, 232.34],
        closes=[240.22, 234.91],
        volumes=[2_096_600, 2_108_000],
        regular_market_time=datetime(2026, 8, 5, 16, 0, tzinfo=NY),
        regular_market_price=234.91,
        trading_period_at=as_of,
    )
    intraday = _intraday_body(
        timestamps=[datetime(2026, 8, 5, 15, 59, tzinfo=NY)],
        closes=[234.90],
        regular_market_time=datetime(2026, 8, 5, 16, 0, tzinfo=NY),
        regular_market_price=234.91,
        trading_period_at=datetime(2026, 8, 5, 15, 59, tzinfo=NY),
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert len(transport.requests) == 2
    assert result.value.last == Decimal("234.91")
    assert result.value.quote_at == datetime(2026, 8, 5, 16, 0, tzinfo=NY)
    assert result.value.session is TradingSession.REGULAR
    assert result.value.previous_close == Decimal("240.22")
    assert result.value.previous_close != Decimal("234.91")
    assert "INTRADAY_QUOTE_UNAVAILABLE" in result.meta.warnings


@pytest.mark.asyncio
async def test_quote_uses_same_day_regular_close_as_postmarket_previous_close() -> None:
    as_of = datetime(2026, 7, 23, 17, 10, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 22), date(2026, 7, 23)],
        opens=[236.24, 232.0],
        highs=[236.5, 233.18],
        lows=[232.57, 229.11],
        closes=[233.58, 230.25],
        volumes=[1_273_800, 1_100_000],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    intraday = _intraday_body(
        timestamps=[datetime(2026, 7, 23, 17, 5, tzinfo=NY)],
        closes=[234.0],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=900,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert result.value.last == Decimal("234.0")
    assert result.value.session.value == "post_market"
    assert result.value.previous_close == Decimal("230.25")
    assert "EXTENDED_HOURS_PRICE" in result.meta.warnings
    assert "EXTENDED_HOURS_SESSION_RANGE_UNAVAILABLE" in result.meta.warnings
    assert result.value.open is None
    assert result.value.high is None
    assert result.value.low is None
    assert result.value.volume is None


@pytest.mark.asyncio
async def test_quote_checks_early_postmarket_before_regular_quote_is_stale() -> None:
    """A real 16:04 print must beat the 16:00 close during early post-market."""
    as_of = datetime(2026, 7, 23, 16, 5, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 22), date(2026, 7, 23)],
        opens=[236.24, 232.0],
        highs=[236.5, 233.18],
        lows=[232.57, 229.11],
        closes=[233.58, 230.25],
        volumes=[1_273_800, 1_100_000],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    intraday = _intraday_body(
        timestamps=[datetime(2026, 7, 23, 16, 4, tzinfo=NY)],
        closes=[231.1],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=900,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert len(transport.requests) == 2
    assert result.value.last == Decimal("231.1")
    assert result.value.quote_at == datetime(2026, 7, 23, 16, 4, tzinfo=NY)
    assert result.value.session is TradingSession.POST_MARKET
    assert result.value.previous_close == Decimal("230.25")
    assert "EXTENDED_HOURS_PRICE" in result.meta.warnings


@pytest.mark.asyncio
async def test_quote_keeps_latest_postmarket_print_after_window_closes() -> None:
    """A closed request still returns a newer real post-market latest-known price."""
    as_of = datetime(2026, 7, 23, 21, 5, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 22), date(2026, 7, 23)],
        opens=[236.24, 232.0],
        highs=[236.5, 233.18],
        lows=[232.57, 229.11],
        closes=[233.58, 230.25],
        volumes=[1_273_800, 1_100_000],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    intraday = _intraday_body(
        timestamps=[datetime(2026, 7, 23, 19, 58, tzinfo=NY)],
        closes=[232.4],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=900,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert len(transport.requests) == 2
    assert result.value.last == Decimal("232.4")
    assert result.value.quote_at == datetime(2026, 7, 23, 19, 58, tzinfo=NY)
    assert result.value.session is TradingSession.POST_MARKET
    assert result.value.previous_close == Decimal("230.25")
    assert "EXTENDED_HOURS_PRICE" in result.meta.warnings
    assert "OVERNIGHT_QUOTE_UNAVAILABLE" in result.meta.warnings


@pytest.mark.asyncio
async def test_overnight_regular_fallback_does_not_relabel_its_baseline() -> None:
    """Without an overnight/post print, the regular quote keeps regular semantics."""
    as_of = datetime(2026, 7, 23, 21, 5, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 22), date(2026, 7, 23)],
        opens=[236.24, 232.0],
        highs=[236.5, 233.18],
        lows=[232.57, 229.11],
        closes=[233.58, 230.25],
        volumes=[1_273_800, 1_100_000],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    intraday = _intraday_body(
        timestamps=[datetime(2026, 7, 23, 16, 0, tzinfo=NY)],
        closes=[230.25],
        regular_market_time=datetime(2026, 7, 23, 16, 0, tzinfo=NY),
        regular_market_price=230.25,
        trading_period_at=as_of,
    )
    transport = SequenceTransport([json.dumps(daily).encode("utf-8"), intraday])
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=900,
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert result.value.session is TradingSession.REGULAR
    assert result.value.last == Decimal("230.25")
    assert result.value.previous_close == Decimal("233.58")
    assert "OVERNIGHT_QUOTE_UNAVAILABLE" in result.meta.warnings


@pytest.mark.asyncio
async def test_future_quote_recovers_from_newer_intraday_bar() -> None:
    as_of = datetime(2026, 7, 24, 21, 51, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 23), date(2026, 7, 24)],
        opens=[4100.0, 4060.0],
        highs=[4120.0, 4070.0],
        lows=[4040.0, 4050.0],
        closes=[4062.4, 4055.7],
        volumes=[100, 100],
        regular_market_time=datetime(2026, 7, 24, 16, 59, 59, tzinfo=NY),
        regular_market_price=4055.7,
        trading_period_at=as_of,
        symbol="GC=F",
    )
    futures_period = daily["chart"]["result"][0]["meta"]["currentTradingPeriod"]
    futures_period["regular"] = {
        "timezone": "EDT",
        "start": _unix(datetime(2026, 7, 24, 18, 0, tzinfo=NY)),
        "end": _unix(datetime(2026, 7, 25, 17, 0, tzinfo=NY)),
        "gmtoffset": -14400,
    }
    futures_period["pre"] = {}
    futures_period["post"] = {}
    intraday = _intraday_body(
        timestamps=[datetime(2026, 7, 24, 21, 50, tzinfo=NY)],
        closes=[4061.2],
        regular_market_time=datetime(2026, 7, 24, 16, 59, 59, tzinfo=NY),
        regular_market_price=4055.7,
        trading_period_at=as_of,
        symbol="GC=F",
    )
    intraday_payload = json.loads(intraday)
    intraday_payload["chart"]["result"][0]["meta"]["currentTradingPeriod"] = (
        futures_period
    )
    transport = SequenceTransport(
        [
            json.dumps(daily).encode("utf-8"),
            json.dumps(intraday_payload).encode("utf-8"),
        ]
    )
    adapter = YahooFinanceAdapter(
        transport,
        clock=FixedClock(as_of),
        max_fresh_seconds=60,
        max_delayed_seconds=900,
    )

    result = await adapter.get_quote(_future_instrument(), as_of)

    assert result.value.last == Decimal("4061.2")
    assert result.value.quote_at == datetime(2026, 7, 24, 21, 50, tzinfo=NY)
    assert result.value.session.value == "regular"
    assert result.value.previous_close == Decimal("4055.7")
    assert "INTRADAY_QUOTE_RECOVERY" in result.meta.warnings


@pytest.mark.asyncio
async def test_stale_daily_bar_fail_closed() -> None:
    # Latest bar 10 natural days before expected end/as_of.
    stale_day = date(2026, 7, 7)
    payload = _chart_payload(
        days=[stale_day],
        opens=[100.0],
        highs=[101.0],
        lows=[99.0],
        closes=[100.5],
        volumes=[1_000],
        regular_market_time=datetime(2026, 7, 7, 16, 0, tzinfo=NY),
    )
    transport = RecordingTransport(body=json.dumps(payload).encode("utf-8"))
    adapter = _adapter(transport)
    with pytest.raises(StaleMarketData) as exc:
        await adapter.get_bars(
            _instrument(),
            start=date(2026, 7, 1),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert exc.value.details.get("rule") == "stale_daily_bar"
    blob = f"{exc.value.message}{exc.value.details}"
    assert "100.5" not in blob  # no price body leak


@pytest.mark.asyncio
async def test_adjustment_fail_closed() -> None:
    transport = RecordingTransport(body=_success_body(include_adjclose=False))
    adapter = _adapter(transport)

    with pytest.raises(DataContractError) as split_only:
        await adapter.get_bars(
            _instrument(),
            start=date(2026, 7, 13),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_ADJUSTED,
            as_of=AS_OF,
        )
    assert split_only.value.details.get("rule") == "unsupported_adjustment"

    with pytest.raises(DataContractError) as missing_adj:
        await adapter.get_bars(
            _instrument(),
            start=date(2026, 7, 13),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
            as_of=AS_OF,
        )
    assert missing_adj.value.details.get("rule") == "adjustment_unavailable"

    # With adjclose present, closes become adjusted (close*factor when factor=adj/close).
    body = _success_body(
        adjcloses=[59.25, 59.75, 60.25, 60.75, 61.25],  # half of raw closes
    )
    transport2 = RecordingTransport(body=body)
    adapter2 = _adapter(transport2)
    adjusted = await adapter2.get_bars(
        _instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        as_of=AS_OF,
    )
    assert adjusted.value.adjustment is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
    assert adjusted.value.bars[-1].close == Decimal("61.25")
    assert adjusted.value.bars[-1].open == Decimal("61.0")  # 122 * 0.5


@pytest.mark.asyncio
async def test_terminal_unfinalized_adjusted_bar_is_omitted_with_warning() -> None:
    body = _success_body(adjcloses=[59.25, 59.75, 60.25, 60.75, None])
    adapter = _adapter(RecordingTransport(body=body))

    result = await adapter.get_bars(
        _instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        as_of=AS_OF,
    )

    assert len(result.value.bars) == 4
    assert result.value.bars[-1].timestamp.date() == date(2026, 7, 16)
    assert "LATEST_ADJUSTED_BAR_UNAVAILABLE" in result.meta.warnings


@pytest.mark.asyncio
async def test_http_status_and_body_never_leaked() -> None:
    secret = b'{"chart":{"result":null,"error":{"description":"SECRET_TOKEN_XYZ"}}}'
    transport = RecordingTransport(body=secret, status_code=500)
    adapter = _adapter(transport)
    with pytest.raises(ProviderUnavailableError) as exc:
        await adapter.get_quote(_instrument(), AS_OF)
    blob = f"{exc.value.message}{exc.value.details}"
    assert "SECRET_TOKEN_XYZ" not in blob
    assert exc.value.details.get("status_class") == "5xx"

    transport429 = RecordingTransport(body=secret, status_code=429)
    adapter429 = _adapter(transport429)
    with pytest.raises(ProviderRateLimitError):
        await adapter429.get_quote(_instrument(), AS_OF)

    # Schema drift body must not appear in contract error.
    drift = RecordingTransport(
        body=b'{"not_chart":"SECRET_BODY_ABC"}',
        status_code=200,
    )
    adapter_drift = _adapter(drift)
    with pytest.raises(DataContractError) as drift_exc:
        await adapter_drift.get_quote(_instrument(), AS_OF)
    drift_blob = f"{drift_exc.value.message}{drift_exc.value.details}"
    assert "SECRET_BODY_ABC" not in drift_blob
    assert "not_chart" not in drift_blob


def test_supports_us_quote_and_ohlcv_only() -> None:
    transport = RecordingTransport(body=_success_body())
    adapter = YahooFinanceAdapter(transport)
    assert adapter.supports(Market.US, DataCategory.MARKET_QUOTE)
    assert adapter.supports(Market.US, DataCategory.MARKET_OHLCV)
    assert not adapter.supports(Market.A_SHARE, DataCategory.MARKET_QUOTE)
    assert not adapter.supports(Market.US, DataCategory.NEWS)
    assert adapter.vendor_id is VendorId.YFINANCE
    assert adapter.provider_name == VendorId.YFINANCE.value
    assert adapter.is_configured() is True
    disabled = YahooFinanceAdapter(transport, enabled=False)
    assert disabled.is_configured() is False


@pytest.mark.asyncio
async def test_future_bars_are_unadjusted_and_disclose_proxy_roll_risk() -> None:
    transport = RecordingTransport(body=_success_body(symbol="GC=F"))
    result = await _adapter(transport).get_bars(
        _future_instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.SIXTY_MINUTES,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    assert result.value.instrument_id == "future:US:GC=F"
    assert result.value.adjustment is AdjustmentMethod.NONE
    assert result.meta.warnings == (
        "FUTURES_CONTRACT_NOT_SPOT",
        "CONTINUOUS_FUTURES_ROLL_RISK",
    )
    assert transport.requests[0].url.endswith("GC%3DF")


@pytest.mark.asyncio
async def test_daily_stale_boundary_four_natural_days_ok() -> None:
    # Exactly 4 natural days lag is accepted; 5 would fail.
    latest = AS_OF.astimezone(NY).date() - timedelta(days=4)
    payload = _chart_payload(
        days=[latest],
        opens=[100.0],
        highs=[101.0],
        lows=[99.0],
        closes=[100.5],
        volumes=[500],
        regular_market_time=datetime(latest.year, latest.month, latest.day, 16, 0, tzinfo=NY),
    )
    transport = RecordingTransport(body=json.dumps(payload).encode("utf-8"))
    adapter = _adapter(transport)
    result = await adapter.get_bars(
        _instrument(),
        start=latest,
        end=AS_OF.astimezone(NY).date(),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    assert len(result.value.bars) == 1


@pytest.mark.asyncio
async def test_yahoo_korean_quote_uses_seoul_day_and_provider_symbol() -> None:
    days = [date(2026, 7, 29), date(2026, 7, 30)]
    timestamps = [
        _unix(datetime(day.year, day.month, day.day, 0, 0, tzinfo=SEOUL))
        for day in days
    ]
    regular_at = datetime(2026, 7, 30, 15, 30, tzinfo=SEOUL)
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "KRW",
                        "symbol": "005930.KS",
                        "regularMarketTime": _unix(regular_at),
                        "regularMarketPrice": 70000,
                        "regularMarketVolume": 123456,
                        "regularMarketOpen": 69500,
                        "regularMarketDayHigh": 70500,
                        "regularMarketDayLow": 69000,
                        "currentTradingPeriod": {
                            "regular": {
                                "start": _unix(
                                    datetime(2026, 7, 30, 9, 0, tzinfo=SEOUL)
                                ),
                                "end": _unix(regular_at),
                            }
                        },
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [68000, 69500],
                                "high": [69000, 70500],
                                "low": [67500, 69000],
                                "close": [68500, 70000],
                                "volume": [100000, 123456],
                            }
                        ],
                        "adjclose": [{"adjclose": [68500, 70000]}],
                    },
                }
            ],
            "error": None,
        }
    }
    as_of = datetime(2026, 7, 30, 16, 0, tzinfo=SEOUL)
    transport = RecordingTransport(body=json.dumps(payload).encode())
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(as_of))

    result = await adapter.get_quote(_kr_instrument(), as_of)

    assert transport.requests[0].url.endswith("/005930.KS")
    assert result.value.quote_at == regular_at
    assert result.value.previous_close == Decimal("68500")
    assert result.value.last == Decimal("70000")
    assert "YAHOO_KR_DELAYED_QUOTE" in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered_price", [125.0, 115.0])
async def test_regular_intraday_recovery_does_not_mix_old_session_range(
    recovered_price: float,
) -> None:
    as_of = datetime(2026, 7, 24, 10, 35, tzinfo=NY)
    daily = _chart_payload(
        days=[date(2026, 7, 23), date(2026, 7, 24)],
        opens=[120.0, 121.0], highs=[123.0, 123.0], lows=[119.0, 120.0],
        closes=[122.0, 122.5], volumes=[1000, 500],
        regular_market_time=datetime(2026, 7, 24, 10, 0, tzinfo=NY),
        regular_market_price=122.5, trading_period_at=as_of,
    )
    recovered_at = datetime(2026, 7, 24, 10, 34, tzinfo=NY)
    intraday = _intraday_body(
        timestamps=[recovered_at], closes=[recovered_price],
        regular_market_time=recovered_at,
        regular_market_price=recovered_price, trading_period_at=as_of,
    )
    transport = SequenceTransport([json.dumps(daily).encode(), intraday])
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(as_of))
    result = await adapter.get_quote(_instrument(), as_of)
    assert result.value.last == Decimal(str(recovered_price))
    assert result.value.quote_at == recovered_at
    assert result.value.session is TradingSession.REGULAR
    assert result.value.previous_close == Decimal("122.0")
    assert result.value.open is None
    assert result.value.high is None
    assert result.value.low is None
    assert result.value.volume is None
    assert "INTRADAY_QUOTE_SESSION_RANGE_UNAVAILABLE" in result.meta.warnings
    assert len(transport.requests) == 2


def _early_close_body(*, symbol: str = "NVDA") -> bytes:
    payload = _chart_payload(
        days=[date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27)],
        opens=[120.0, 121.0, 122.0],
        highs=[121.0, 122.0, 123.0],
        lows=[119.0, 120.0, 121.0],
        closes=[120.5, 121.5, 122.5],
        volumes=[1000, 1100, 1200],
        regular_market_time=datetime(2026, 11, 30, 16, 0, tzinfo=NY),
        regular_market_price=999.0,
        symbol=symbol,
    )
    payload["chart"]["result"][0]["meta"]["regularMarketVolume"] = 999999
    return json.dumps(payload).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_type", [AssetType.EQUITY, AssetType.ETF, AssetType.INDEX])
@pytest.mark.parametrize("cutoff", ["12:59:59", "13:00:00", "13:30:00"])
@pytest.mark.parametrize("operation", ["quote", "bars"])
async def test_us_early_close_daily_cutoff_uses_completed_regular_session(
    asset_type: AssetType, cutoff: str, operation: str
) -> None:
    symbol = "SPX" if asset_type is AssetType.INDEX else "NVDA"
    wire_symbol = "^GSPC" if asset_type is AssetType.INDEX else symbol
    instrument = replace(
        _instrument(), asset_type=asset_type, symbol=symbol,
        instrument_id=f"{asset_type.value}:US:{symbol}",
    )
    as_of = datetime.fromisoformat(f"2026-11-27T{cutoff}").replace(tzinfo=NY)
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=_early_close_body(symbol=wire_symbol)),
        clock=FixedClock(datetime(2026, 11, 30, 16, 5, tzinfo=NY)),
    )
    completed = cutoff != "12:59:59"
    expected_at = (
        datetime(2026, 11, 27, 13, 0, tzinfo=NY)
        if completed else datetime(2026, 11, 25, 16, 0, tzinfo=NY)
    )
    expected_close = Decimal("122.5") if completed else Decimal("121.5")

    if operation == "quote":
        result = await adapter.get_quote(instrument, as_of)
        assert result.value.quote_at == expected_at
        assert result.value.last == expected_close
        assert result.value.previous_close == (Decimal("121.5") if completed else Decimal("120.5"))
        assert result.value.volume != Decimal("999999")
        assert result.value.week_52_high is None
        assert result.value.week_52_low is None
    else:
        bars = await adapter.get_bars(
            instrument, start=date(2026, 11, 24), end=date(2026, 11, 27),
            interval=USBarInterval.ONE_DAY, adjustment=AdjustmentMethod.NONE, as_of=as_of,
        )
        assert bars.value.bars[-1].timestamp == expected_at
        assert bars.value.bars[-1].close == expected_close
        assert all(bar.timestamp <= as_of for bar in bars.value.bars)


@pytest.mark.asyncio
@pytest.mark.parametrize(("day", "utc_hour"), [(date(2026, 3, 6), 21), (date(2026, 3, 9), 20)])
async def test_us_normal_daily_close_retains_dst_aware_timestamp(day: date, utc_hour: int) -> None:
    as_of = datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY)
    payload = _chart_payload(
        days=[day], opens=[120.0], highs=[121.0], lows=[119.0], closes=[120.5], volumes=[1000],
    )
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of),
    )

    result = await adapter.get_bars(
        _instrument(), start=day, end=day, interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert result.value.bars[-1].timestamp == as_of
    assert result.value.bars[-1].timestamp.astimezone(ZoneInfo("UTC")).hour == utc_hour
    assert "US_NON_SESSION_DAILY_BAR" not in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("cutoff", ["15:59:59", "16:00:00"])
async def test_us_non_session_daily_row_has_conservative_cutoff_and_warning(cutoff: str) -> None:
    payload = _chart_payload(
        days=[date(2026, 11, 25), date(2026, 11, 26)], opens=[121.0, 122.0],
        highs=[122.0, 123.0], lows=[120.0, 121.0], closes=[121.5, 122.5], volumes=[1000, 1100],
    )
    as_of = datetime.fromisoformat(f"2026-11-26T{cutoff}").replace(tzinfo=NY)
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of),
    )

    result = await adapter.get_bars(
        _instrument(), start=date(2026, 11, 25), end=date(2026, 11, 26),
        interval=USBarInterval.ONE_DAY, adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    included = cutoff == "16:00:00"
    assert result.value.bars[-1].timestamp == (
        datetime(2026, 11, 26, 16, 0, tzinfo=NY)
        if included else datetime(2026, 11, 25, 16, 0, tzinfo=NY)
    )
    assert ("US_NON_SESSION_DAILY_BAR" in result.meta.warnings) is included


class _FailingSessionCalendar:
    def session_at(self, moment: datetime) -> MarketSession | None:
        raise RuntimeError("private calendar failure details")

    def session_on_or_before(self, moment: datetime) -> MarketSession | None:
        raise AssertionError("unexpected calendar operation")

    def previous_session(self, session_date: date) -> MarketSession | None:
        raise AssertionError("unexpected calendar operation")

    def next_session(self, session_date: date) -> MarketSession | None:
        raise AssertionError("unexpected calendar operation")


@pytest.mark.asyncio
@pytest.mark.parametrize("use_broken_calendar", [True, False])
async def test_us_daily_calendar_failure_rejects_instead_of_inventing_close(
    use_broken_calendar: bool,
) -> None:
    day = date(2026, 11, 27) if use_broken_calendar else date(1900, 1, 2)
    payload = _chart_payload(
        days=[day], opens=[120.0], highs=[121.0], lows=[119.0], closes=[120.5], volumes=[1000],
    )
    as_of = datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY)
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of),
        us_session_calendar=_FailingSessionCalendar() if use_broken_calendar else None,
    )

    with pytest.raises(DataContractError) as captured:
        await adapter.get_bars(
            _instrument(), start=day, end=day, interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE, as_of=as_of,
        )

    assert captured.value.details["rule"] == "session_calendar_unavailable"
    assert "private" not in str(captured.value)
    assert captured.value.__suppress_context__ is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["future_us", "future_cme", "kr", "intraday_us"])
async def test_us_regular_calendar_does_not_change_other_market_or_interval(kind: str) -> None:
    day = date(2026, 11, 27)
    instrument = _instrument()
    wire_symbol = instrument.symbol
    expected_time = "00:00:00" if kind == "intraday_us" else "17:00:00"
    if kind == "future_us":
        instrument = _future_instrument()
        wire_symbol = "GC=F"
    elif kind == "future_cme":
        instrument = replace(
            _future_instrument(), market=Market.CME,
            symbol="GCZ26", instrument_id="future:CME:GCZ26",
        )
        wire_symbol = "GCZ26.CMX"
    elif kind == "kr":
        instrument = _kr_instrument()
        wire_symbol = "005930.KS"
        expected_time = "15:30:00"
    payload = _chart_payload(
        days=[day], opens=[120.0], highs=[121.0], lows=[119.0], closes=[120.5], volumes=[1000],
        symbol=wire_symbol,
    )
    as_of = datetime(2026, 11, 28, 0, 0, tzinfo=NY)
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of),
        us_session_calendar=_FailingSessionCalendar(),
    )

    result = await adapter.get_bars(
        instrument, start=day, end=day,
        interval=USBarInterval.ONE_MINUTE if kind == "intraday_us" else USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert result.value.bars[-1].timestamp.strftime("%H:%M:%S") == expected_time
    assert "US_NON_SESSION_DAILY_BAR" not in result.meta.warnings


_CALENDAR_PERIOD_WARNING = "CALENDAR_PERIOD_AGGREGATED_FROM_DAILY_BARS"


def _calendar_period_adapter(payload: dict[str, Any], as_of: datetime) -> YahooFinanceAdapter:
    return YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()),
        clock=FixedClock(as_of + timedelta(days=30)),
    )


def _july_daily_payload() -> dict[str, Any]:
    days = [
        date(2026, 7, day) for day in range(1, 32)
        if date(2026, 7, day).weekday() < 5 and day != 3
    ]
    return _chart_payload(
        days=days,
        opens=[99.0 + idx for idx in range(len(days))],
        highs=[101.0 + idx for idx in range(len(days))],
        lows=[98.0 + idx for idx in range(len(days))],
        closes=[100.0 + idx for idx in range(len(days))],
        volumes=[100 * (idx + 1) for idx in range(len(days))],
    )


@pytest.mark.asyncio
async def test_yahoo_weekly_bars_aggregate_completed_daily_ohlcv() -> None:
    transport = RecordingTransport(body=_success_body())
    result = await _adapter(transport).get_bars(
        _instrument(), start=date(2026, 7, 13), end=date(2026, 7, 17),
        interval=USBarInterval.ONE_WEEK, adjustment=AdjustmentMethod.NONE, as_of=AS_OF,
    )

    assert transport.requests[0].params["interval"] == "1d"
    assert result.value.interval is USBarInterval.ONE_WEEK
    assert result.value.adjustment is AdjustmentMethod.NONE
    assert len(result.value.bars) == 1
    bar = result.value.bars[0]
    assert bar.timestamp == datetime(2026, 7, 17, 16, 0, tzinfo=NY)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        Decimal("118.0"), Decimal("123.0"), Decimal("117.0"),
        Decimal("122.5"), Decimal("6000000"),
    )
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
async def test_yahoo_monthly_bars_aggregate_completed_daily_ohlcv() -> None:
    as_of = datetime(2026, 7, 31, 16, 0, tzinfo=NY)
    result = await _calendar_period_adapter(_july_daily_payload(), as_of).get_bars(
        _instrument(), start=date(2026, 7, 1), end=date(2026, 7, 31),
        interval=USBarInterval.ONE_MONTH, adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert result.value.interval is USBarInterval.ONE_MONTH
    assert len(result.value.bars) == 1
    bar = result.value.bars[0]
    assert bar.timestamp == as_of
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        Decimal("99.0"), Decimal("122.0"), Decimal("98.0"),
        Decimal("121.0"), Decimal("25300"),
    )
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize(("hour", "last_day", "close"), [(12, 14, "119.5"), (16, 15, "120.5")])
async def test_yahoo_historical_week_omits_future_daily_facts(
    hour: int, last_day: int, close: str
) -> None:
    as_of = datetime(2026, 7, 15, hour, 0, tzinfo=NY)
    payload = json.loads(_success_body())
    result = await _calendar_period_adapter(payload, as_of).get_bars(
        _instrument(), start=date(2026, 7, 13), end=date(2026, 7, 17),
        interval=USBarInterval.ONE_WEEK, adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    bar = result.value.bars[0]
    assert bar.timestamp == datetime(2026, 7, last_day, 16, 0, tzinfo=NY)
    assert bar.close == Decimal(close)
    assert bar.high <= Decimal("121.0")
    assert bar.volume == (Decimal("2100000") if hour == 12 else Decimal("3300000"))
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
async def test_yahoo_historical_month_omits_future_daily_facts() -> None:
    as_of = datetime(2026, 7, 10, 12, 0, tzinfo=NY)
    result = await _calendar_period_adapter(_july_daily_payload(), as_of).get_bars(
        _instrument(), start=date(2026, 7, 1), end=date(2026, 7, 31),
        interval=USBarInterval.ONE_MONTH, adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    bar = result.value.bars[0]
    assert bar.timestamp == datetime(2026, 7, 9, 16, 0, tzinfo=NY)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        Decimal("99.0"), Decimal("106.0"), Decimal("98.0"),
        Decimal("105.0"), Decimal("2100"),
    )
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
async def test_yahoo_weekly_grouping_uses_iso_week_year_across_civil_years() -> None:
    days = [
        date(2024, 12, 30), date(2024, 12, 31), date(2025, 1, 2), date(2025, 1, 3),
        date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31),
    ]
    payload = _chart_payload(
        days=days, opens=[100.0 + idx for idx in range(7)],
        highs=[102.0 + idx for idx in range(7)], lows=[99.0 + idx for idx in range(7)],
        closes=[101.0 + idx for idx in range(7)], volumes=[10] * 7,
    )
    as_of = datetime(2025, 12, 31, 16, 0, tzinfo=NY)
    result = await _calendar_period_adapter(payload, as_of).get_bars(
        _instrument(), start=days[0], end=days[-1], interval=USBarInterval.ONE_WEEK,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert [bar.timestamp for bar in result.value.bars] == [
        datetime(2025, 1, 3, 16, 0, tzinfo=NY), datetime(2025, 12, 31, 16, 0, tzinfo=NY),
    ]
    assert [bar.close for bar in result.value.bars] == [Decimal("104.0"), Decimal("107.0")]
    assert [bar.volume for bar in result.value.bars] == [Decimal("40"), Decimal("30")]


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH])
async def test_yahoo_period_uses_only_the_inclusive_requested_daily_range(
    interval: USBarInterval,
) -> None:
    result = await _adapter(RecordingTransport(body=_success_body())).get_bars(
        _instrument(), start=date(2026, 7, 14), end=date(2026, 7, 16),
        interval=interval, adjustment=AdjustmentMethod.NONE, as_of=AS_OF,
    )

    bar = result.value.bars[0]
    assert bar.timestamp == datetime(2026, 7, 16, 16, 0, tzinfo=NY)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        Decimal("119.0"), Decimal("122.0"), Decimal("118.0"),
        Decimal("121.5"), Decimal("3600000"),
    )
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH])
@pytest.mark.parametrize("empty", [True, False])
async def test_yahoo_calendar_period_without_completed_daily_data_fails(
    interval: USBarInterval, empty: bool,
) -> None:
    payload = _chart_payload(
        days=[date(2026, 7, 15)], opens=[120.0], highs=[121.0], lows=[119.0],
        closes=[120.5], volumes=[1000],
    )
    if empty:
        row = payload["chart"]["result"][0]
        row["timestamp"] = []
        row["indicators"]["quote"][0] = {
            "open": [], "high": [], "low": [], "close": [], "volume": [],
        }
        row["indicators"]["adjclose"][0]["adjclose"] = []
    as_of = datetime(2026, 7, 15, 12, 0, tzinfo=NY)
    with pytest.raises(NoMarketData):
        await _calendar_period_adapter(payload, as_of).get_bars(
            _instrument(), start=date(2026, 7, 15), end=date(2026, 7, 15),
            interval=interval, adjustment=AdjustmentMethod.NONE, as_of=as_of,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH])
@pytest.mark.parametrize("kind", ["future_us", "future_cme", "kr"])
async def test_yahoo_calendar_period_preserves_market_local_daily_close_and_basis(
    interval: USBarInterval, kind: str,
) -> None:
    instrument = _future_instrument()
    wire_symbol = "GC=F"
    zone = NY
    close_hour, close_minute = 17, 0
    if kind == "future_cme":
        instrument = replace(
            instrument, market=Market.CME, symbol="GCZ26", instrument_id="future:CME:GCZ26",
        )
        wire_symbol = "GCZ26.CMX"
    elif kind == "kr":
        instrument = _kr_instrument()
        wire_symbol = "005930.KS"
        zone = SEOUL
        close_hour, close_minute = 15, 30
    days = [date(2026, 7, 16), date(2026, 7, 17)]
    payload = _chart_payload(
        days=days, opens=[100.0, 101.0], highs=[102.0, 103.0], lows=[99.0, 100.0],
        closes=[101.0, 102.0], volumes=[10, 20], symbol=wire_symbol,
    )
    payload["chart"]["result"][0]["timestamp"] = [
        _unix(datetime(day.year, day.month, day.day, 0, 0, tzinfo=zone)) for day in days
    ]
    transport = RecordingTransport(body=json.dumps(payload).encode())
    as_of = datetime(2026, 7, 17, close_hour, close_minute, tzinfo=zone)
    adapter = YahooFinanceAdapter(transport, clock=FixedClock(as_of))

    result = await adapter.get_bars(
        instrument, start=days[0], end=days[-1], interval=interval,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert transport.requests[0].params["interval"] == "1d"
    assert result.value.adjustment is AdjustmentMethod.NONE
    assert result.value.bars[0].timestamp == datetime(
        2026, 7, 17, close_hour, close_minute, tzinfo=zone,
    )
    assert result.value.bars[0].close == Decimal("102.0")
    assert result.value.bars[0].volume == Decimal("30")
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("latest_adjusted_missing", [False, True])
async def test_yahoo_period_aggregates_adjusted_daily_prices_without_inventing_adjustment(
    latest_adjusted_missing: bool,
) -> None:
    adjusted: list[float | None] = [59.25, 59.75, 60.25, 60.75, 61.25]
    if latest_adjusted_missing:
        adjusted[-1] = None
    transport = RecordingTransport(body=_success_body(adjcloses=adjusted))
    result = await _adapter(transport).get_bars(
        _instrument(), start=date(2026, 7, 13), end=date(2026, 7, 17),
        interval=USBarInterval.ONE_WEEK,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED, as_of=AS_OF,
    )

    assert result.value.adjustment is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
    bar = result.value.bars[0]
    assert bar.open == Decimal("59.0")
    assert bar.low == Decimal("58.5")
    assert bar.close == (Decimal("60.75") if latest_adjusted_missing else Decimal("61.25"))
    assert bar.high == (Decimal("61.0") if latest_adjusted_missing else Decimal("61.5"))
    assert bar.volume == (Decimal("4600000") if latest_adjusted_missing else Decimal("6000000"))
    assert ("LATEST_ADJUSTED_BAR_UNAVAILABLE" in result.meta.warnings) is latest_adjusted_missing
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
async def test_yahoo_period_rejects_duplicate_daily_timestamps_before_volume_aggregation() -> None:
    day = date(2026, 7, 13)
    payload = _chart_payload(
        days=[day, day], opens=[100.0, 100.0], highs=[101.0, 101.0], lows=[99.0, 99.0],
        closes=[100.5, 100.5], volumes=[10, 10],
    )
    as_of = datetime(2026, 7, 13, 16, 0, tzinfo=NY)
    with pytest.raises(DataContractError) as captured:
        await _calendar_period_adapter(payload, as_of).get_bars(
            _instrument(), start=day, end=day, interval=USBarInterval.ONE_WEEK,
            adjustment=AdjustmentMethod.NONE, as_of=as_of,
        )

    assert captured.value.details["rule"] == "unique_daily_timestamps"


@pytest.mark.asyncio
async def test_yahoo_monthly_grouping_preserves_year_and_month_boundaries() -> None:
    days = [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 2), date(2026, 1, 5)]
    payload = _chart_payload(
        days=days, opens=[100.0, 101.0, 102.0, 103.0], highs=[102.0, 103.0, 104.0, 105.0],
        lows=[99.0, 100.0, 101.0, 102.0], closes=[101.0, 102.0, 103.0, 104.0],
        volumes=[10, 20, 30, 40],
    )
    as_of = datetime(2026, 1, 5, 16, 0, tzinfo=NY)
    result = await _calendar_period_adapter(payload, as_of).get_bars(
        _instrument(), start=days[0], end=days[-1], interval=USBarInterval.ONE_MONTH,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert [bar.timestamp for bar in result.value.bars] == [
        datetime(2025, 12, 31, 16, 0, tzinfo=NY), datetime(2026, 1, 5, 16, 0, tzinfo=NY),
    ]
    assert [bar.close for bar in result.value.bars] == [Decimal("102.0"), Decimal("104.0")]
    assert [bar.volume for bar in result.value.bars] == [Decimal("30"), Decimal("70")]


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH])
async def test_yahoo_period_includes_completed_early_close_at_its_real_timestamp(
    interval: USBarInterval,
) -> None:
    as_of = datetime(2026, 11, 27, 13, 0, tzinfo=NY)
    result = await _calendar_period_adapter(json.loads(_early_close_body()), as_of).get_bars(
        _instrument(), start=date(2026, 11, 24), end=date(2026, 11, 27), interval=interval,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert result.value.bars[0].timestamp == as_of
    assert result.value.bars[0].close == Decimal("122.5")
    assert result.value.bars[0].volume == Decimal("3300")
    assert _CALENDAR_PERIOD_WARNING in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH])
async def test_yahoo_period_retains_underlying_daily_staleness_guard(
    interval: USBarInterval,
) -> None:
    payload = _chart_payload(
        days=[date(2026, 7, 1)], opens=[120.0], highs=[121.0], lows=[119.0],
        closes=[120.5], volumes=[1000],
    )
    with pytest.raises(StaleMarketData):
        await _calendar_period_adapter(payload, AS_OF).get_bars(
            _instrument(), start=date(2026, 7, 1), end=date(2026, 7, 17), interval=interval,
            adjustment=AdjustmentMethod.NONE, as_of=AS_OF,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interval", [USBarInterval.ONE_DAY, USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH]
)
async def test_yahoo_daily_range_filters_old_rows_before_calendar_lookup(
    interval: USBarInterval,
) -> None:
    # The irrelevant old row is outside the declared XNYS history window,
    # even after its supported history has been expanded to 1990.
    # It must not prevent the requested completed early-close bar from returning.
    day = date(2026, 11, 27)
    as_of = datetime(2026, 11, 27, 13, 0, tzinfo=NY)
    payload = _chart_payload(
        days=[date(1900, 1, 3), day], opens=[100.0, 120.0],
        highs=[101.0, 121.0], lows=[99.0, 119.0], closes=[100.5, 120.5],
        volumes=[1000, 2000],
    )
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of),
    )

    result = await adapter.get_bars(
        _instrument(), start=day, end=day, interval=interval,
        adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )

    assert len(result.value.bars) == 1
    bar = result.value.bars[0]
    assert bar.timestamp == as_of
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        Decimal("120.0"), Decimal("121.0"), Decimal("119.0"),
        Decimal("120.5"), Decimal("2000"),
    )
    assert "US_NON_SESSION_DAILY_BAR" not in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [USBarInterval.ONE_WEEK, USBarInterval.ONE_MONTH])
@pytest.mark.parametrize(
    "adjustment", [AdjustmentMethod.NONE, AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED]
)
@pytest.mark.parametrize("invalid_bound", ["high", "low"])
async def test_yahoo_period_rejects_invalid_middle_daily_ohlc(
    interval: USBarInterval, adjustment: AdjustmentMethod, invalid_bound: str,
) -> None:
    # The first/last rows make the aggregate itself appear valid, so the
    # intermediate daily fact must be validated before aggregation hides it.
    days = [date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15)]
    middle_open = 110.0 if invalid_bound == "high" else 100.0
    middle_high = 105.0 if invalid_bound == "high" else 107.0
    payload = _chart_payload(
        days=days, opens=[100.0, middle_open, 100.0],
        highs=[101.0, middle_high, 111.0], lows=[99.0, 104.0, 99.0],
        closes=[100.0, 106.0, 100.0], volumes=[10, 1000, 10],
        adjcloses=[100.0, 106.0, 100.0],
    )
    as_of = datetime(2026, 7, 15, 16, 0, tzinfo=NY)
    adapter = YahooFinanceAdapter(
        RecordingTransport(body=json.dumps(payload).encode()), clock=FixedClock(as_of),
    )

    with pytest.raises(DataContractError) as captured:
        await adapter.get_bars(
            _instrument(), start=days[0], end=days[-1], interval=interval,
            adjustment=adjustment, as_of=as_of,
        )

    assert captured.value.details["field"] == f"bars[1].{invalid_bound}"
    assert captured.value.details["rule"] == f"ohlc_{invalid_bound}"


@pytest.mark.asyncio
@pytest.mark.parametrize("irrelevant_day", [date(1900, 6, 15), date(1989, 6, 15)])
async def test_yahoo_quote_ignores_daily_rows_outside_its_requested_chart_window(
    irrelevant_day: date,
) -> None:
    as_of = datetime(2026, 11, 27, 13, 0, tzinfo=NY)
    payload = _chart_payload(
        days=[irrelevant_day, date(2026, 11, 25), date(2026, 11, 27)],
        opens=[1.0, 121.0, 122.0], highs=[2.0, 122.0, 123.0],
        lows=[0.5, 120.0, 121.0], closes=[1.0, 121.5, 122.5],
        volumes=[10, 1100, 1200],
        regular_market_time=datetime(2026, 11, 30, 16, 0, tzinfo=NY),
    )
    transport = RecordingTransport(body=json.dumps(payload).encode())
    adapter = YahooFinanceAdapter(
        transport, clock=FixedClock(datetime(2026, 11, 30, 16, 5, tzinfo=NY)),
    )

    result = await adapter.get_quote(_instrument(), as_of)

    assert len(transport.requests) == 1
    assert result.value.quote_at == as_of
    assert result.value.last == Decimal("122.5")
    assert result.value.previous_close == Decimal("121.5")
    assert result.value.volume == Decimal("1200")
    assert result.value.week_52_high is None
    assert result.value.week_52_low is None
    assert "US_NON_SESSION_DAILY_BAR" not in result.meta.warnings
