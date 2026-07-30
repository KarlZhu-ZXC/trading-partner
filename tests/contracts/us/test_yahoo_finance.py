"""Phase 1F F2a: Yahoo Finance chart adapter focused contracts."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
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
    ProviderRateLimitError,
    ProviderUnavailableError,
    StaleMarketData,
)
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from infrastructure.providers.us.yahoo_finance import YahooFinanceAdapter

NY = ZoneInfo("America/New_York")
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
                        "symbol": "NVDA",
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
        closes=[230.33, 232.0],
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
    assert result.value.last == Decimal("232.0")
    assert result.value.quote_at == datetime(2026, 7, 24, 5, 14, tzinfo=NY)
    assert result.value.session.value == "pre_market"
    assert result.value.previous_close == Decimal("230.25")
    assert "EXTENDED_HOURS_PRICE" in result.meta.warnings


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
        closes=[231.5],
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

    assert result.value.last == Decimal("231.5")
    assert result.value.session.value == "post_market"
    assert result.value.previous_close == Decimal("230.25")
    assert "EXTENDED_HOURS_PRICE" in result.meta.warnings


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
    transport = RecordingTransport(body=_success_body())
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
