"""Phase 1F F2b: Alpha Vantage fallback adapter focused contracts."""

from __future__ import annotations

import json
from datetime import date, datetime
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
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import (
    DataContractError,
    NoMarketData,
    ProviderNotConfigured,
    ProviderRateLimitError,
)
from domain.instruments.models import Instrument
from domain.us_market.enums import USBarInterval
from infrastructure.providers.us.alpha_vantage import AlphaVantageAdapter

NY = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 7, 17, 16, 5, tzinfo=NY)
CLOCK = FixedClock(AS_OF)
API_KEY = "TEST_AV_SECRET_KEY_NEVER_LEAK"
SECOND_API_KEY = "SECOND_TEST_AV_SECRET_KEY_NEVER_LEAK"


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


def _global_quote(
    *,
    latest_day: str = "2026-07-17",
    price: str = "122.5000",
    open_: str = "122.0000",
    high: str = "123.0000",
    low: str = "121.0000",
    volume: str = "1400000",
    previous_close: str = "119.0000",
) -> dict[str, Any]:
    return {
        "Global Quote": {
            "01. symbol": "NVDA",
            "02. open": open_,
            "03. high": high,
            "04. low": low,
            "05. price": price,
            "06. volume": volume,
            "07. latest trading day": latest_day,
            "08. previous close": previous_close,
            "09. change": "3.5000",
            "10. change percent": "2.9412%",
        }
    }


def _daily_series(
    rows: dict[str, dict[str, str]],
) -> dict[str, Any]:
    return {
        "Meta Data": {
            "1. Information": "Daily Time Series with Splits and Dividend Events",
            "2. Symbol": "NVDA",
            "3. Last Refreshed": "2026-07-17",
            "4. Output Size": "Compact",
            "5. Time Zone": "US/Eastern",
        },
        "Time Series (Daily)": rows,
    }


def _success_rows(*, include_future: bool = False) -> dict[str, dict[str, str]]:
    rows = {
        "2026-07-13": {
            "1. open": "118.0000",
            "2. high": "119.0000",
            "3. low": "117.0000",
            "4. close": "118.5000",
            "5. adjusted close": "59.2500",
            "6. volume": "1000000",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0000",
        },
        "2026-07-14": {
            "1. open": "119.0000",
            "2. high": "120.0000",
            "3. low": "118.0000",
            "4. close": "119.5000",
            "5. adjusted close": "59.7500",
            "6. volume": "1100000",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0000",
        },
        "2026-07-15": {
            "1. open": "120.0000",
            "2. high": "121.0000",
            "3. low": "119.0000",
            "4. close": "120.5000",
            "5. adjusted close": "60.2500",
            "6. volume": "1200000",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0000",
        },
        "2026-07-16": {
            "1. open": "121.0000",
            "2. high": "122.0000",
            "3. low": "120.0000",
            "4. close": "121.5000",
            "5. adjusted close": "60.7500",
            "6. volume": "1300000",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0000",
        },
        "2026-07-17": {
            "1. open": "122.0000",
            "2. high": "123.0000",
            "3. low": "121.0000",
            "4. close": "122.5000",
            "5. adjusted close": "61.2500",
            "6. volume": "1400000",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0000",
        },
    }
    if include_future:
        rows["2026-07-20"] = {
            "1. open": "999.0000",
            "2. high": "999.0000",
            "3. low": "998.0000",
            "4. close": "999.0000",
            "5. adjusted close": "999.0000",
            "6. volume": "1",
            "7. dividend amount": "0.0000",
            "8. split coefficient": "1.0000",
        }
    return rows


class ScriptedTransport:
    """Returns bodies in order; records requests for inspection."""

    def __init__(
        self,
        bodies: list[bytes],
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._bodies = list(bodies)
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json; charset=utf-8"}
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        body = self._bodies.pop(0) if self._bodies else b"{}"
        return HttpResponse(
            status_code=self.status_code,
            headers=self.headers,
            body=body,
        )


def _adapter(
    transport: ScriptedTransport,
    *,
    api_keys: tuple[str, ...] = (API_KEY,),
    enabled: bool = True,
) -> AlphaVantageAdapter:
    return AlphaVantageAdapter(
        transport,
        api_keys=api_keys,
        clock=CLOCK,
        enabled=enabled,
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


def _assert_no_secret(exc: BaseException) -> None:
    blob = f"{exc}{getattr(exc, 'message', '')}{getattr(exc, 'details', '')}"
    assert API_KEY not in blob
    assert "TEST_AV_SECRET" not in blob
    assert SECOND_API_KEY not in blob


@pytest.mark.asyncio
async def test_alpha_vantage_quote_and_bars_success() -> None:
    transport = ScriptedTransport(
        [
            json.dumps(_global_quote()).encode("utf-8"),
            json.dumps(_daily_series(_success_rows())).encode("utf-8"),
        ]
    )
    adapter = _adapter(transport)

    quote = await adapter.get_quote(_instrument(), AS_OF)
    assert quote.meta.vendor is VendorId.ALPHA_VANTAGE
    assert quote.meta.role is SourceRole.FALLBACK
    assert quote.meta.category is DataCategory.MARKET_QUOTE
    assert quote.value.instrument_id == "equity:US:NVDA"
    assert quote.value.last == Decimal("122.5000")
    assert type(quote.value.last) is Decimal
    assert quote.value.quote_at == datetime(2026, 7, 17, 16, 0, tzinfo=NY)
    assert quote.value.quote_at <= AS_OF
    assert quote.value.previous_close == Decimal("119.0000")
    assert quote.value.session is TradingSession.REGULAR
    assert quote.meta.session is TradingSession.REGULAR

    bars = await adapter.get_bars(
        _instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    assert bars.meta.vendor is VendorId.ALPHA_VANTAGE
    assert bars.meta.role is SourceRole.FALLBACK
    assert bars.meta.category is DataCategory.MARKET_OHLCV
    assert bars.meta.adjustment is AdjustmentMethod.NONE
    assert bars.meta.session is TradingSession.REGULAR
    assert bars.value.end == date(2026, 7, 17)
    assert len(bars.value.bars) == 5
    assert bars.value.bars[-1].timestamp == datetime(2026, 7, 17, 16, 0, tzinfo=NY)
    assert bars.value.bars[-1].close == Decimal("122.5000")
    assert all(b.timestamp <= AS_OF for b in bars.value.bars)

    assert len(transport.requests) == 2
    for req in transport.requests:
        assert req.method == "GET"
        parts = urlsplit(req.url)
        assert parts.scheme == "https"
        assert parts.hostname == "www.alphavantage.co"
        assert parts.path == "/query"
        assert req.params.get("apikey") == API_KEY
        assert "apikey" in req.params


@pytest.mark.asyncio
async def test_end_inclusive_as_of_cutoff_and_adjustment() -> None:
    transport = ScriptedTransport(
        [
            json.dumps(_daily_series(_success_rows(include_future=True))).encode("utf-8"),
            json.dumps(_daily_series(_success_rows())).encode("utf-8"),
            json.dumps(_global_quote(latest_day="2026-07-20", price="999.0000")).encode("utf-8"),
        ]
    )
    adapter = _adapter(transport)

    raw = await adapter.get_bars(
        _instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.NONE,
        as_of=AS_OF,
    )
    assert raw.value.bars[-1].timestamp.astimezone(NY).date() == date(2026, 7, 17)
    assert all(b.close != Decimal("999.0000") for b in raw.value.bars)
    assert len(raw.value.bars) == 5

    adjusted = await adapter.get_bars(
        _instrument(),
        start=date(2026, 7, 13),
        end=date(2026, 7, 17),
        interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED,
        as_of=AS_OF,
    )
    assert adjusted.value.adjustment is AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED
    assert adjusted.value.bars[-1].close == Decimal("61.2500")
    # 122 * (61.25/122.5) = 61.0
    assert adjusted.value.bars[-1].open == Decimal("61.0000")

    with pytest.raises(NoMarketData):
        await adapter.get_quote(_instrument(), AS_OF)


@pytest.mark.asyncio
async def test_rate_limit_error_message_secret_redaction() -> None:
    rate_body = json.dumps(
        {
            "Note": (
                f"Thank you for using Alpha Vantage! API key {API_KEY} "
                "call frequency is 25 requests per day."
            )
        }
    ).encode("utf-8")
    transport = ScriptedTransport([rate_body])
    adapter = _adapter(transport)
    with pytest.raises(ProviderRateLimitError) as rate_exc:
        await adapter.get_quote(_instrument(), AS_OF)
    _assert_no_secret(rate_exc.value)
    blob = f"{rate_exc.value.message}{rate_exc.value.details}"
    assert "25 requests" not in blob
    assert API_KEY not in blob

    err_body = json.dumps(
        {"Error Message": f"Invalid API call with key {API_KEY} SECRET_PAYLOAD"}
    ).encode("utf-8")
    transport2 = ScriptedTransport([err_body])
    adapter2 = _adapter(transport2)
    with pytest.raises(NoMarketData) as err_exc:
        await adapter2.get_quote(_instrument(), AS_OF)
    _assert_no_secret(err_exc.value)
    err_blob = f"{err_exc.value.message}{err_exc.value.details}"
    assert "SECRET_PAYLOAD" not in err_blob
    assert API_KEY not in err_blob


@pytest.mark.asyncio
async def test_rate_limited_key_fails_over_and_successful_key_stays_active() -> None:
    rate_body = json.dumps({"Note": "API call frequency rate limit reached"}).encode()
    success_body = json.dumps(_global_quote()).encode()
    transport = ScriptedTransport([rate_body, success_body, success_body])
    adapter = _adapter(transport, api_keys=(API_KEY, SECOND_API_KEY))

    await adapter.get_quote(_instrument(), AS_OF)
    await adapter.get_quote(_instrument(), AS_OF)

    used_keys = [request.params["apikey"] for request in transport.requests]
    assert used_keys == [API_KEY, SECOND_API_KEY, SECOND_API_KEY]
    assert API_KEY not in repr(adapter._key_pool)  # noqa: SLF001
    assert SECOND_API_KEY not in repr(adapter._key_pool)  # noqa: SLF001


@pytest.mark.asyncio
async def test_missing_key_and_unsupported_interval() -> None:
    transport = ScriptedTransport([b"{}"])
    missing = _adapter(transport, api_keys=())
    assert missing.is_configured() is False
    with pytest.raises(ProviderNotConfigured):
        await missing.get_quote(_instrument(), AS_OF)

    blank = _adapter(transport, api_keys=("   ",))
    assert blank.is_configured() is False

    disabled = _adapter(transport, enabled=False)
    assert disabled.is_configured() is False

    ok = _adapter(transport)
    assert ok.is_configured() is True
    assert ok.vendor_id is VendorId.ALPHA_VANTAGE
    assert ok.supports(Market.US, DataCategory.MARKET_QUOTE)
    assert ok.supports(Market.US, DataCategory.MARKET_OHLCV)
    assert not ok.supports(Market.A_SHARE, DataCategory.MARKET_QUOTE)

    with pytest.raises(DataContractError) as interval_exc:
        await ok.get_bars(
            _instrument(),
            start=date(2026, 7, 13),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_MINUTE,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert interval_exc.value.details.get("rule") == "unsupported_interval"

    with pytest.raises(DataContractError) as split_exc:
        await ok.get_bars(
            _instrument(),
            start=date(2026, 7, 13),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.SPLIT_ADJUSTED,
            as_of=AS_OF,
        )
    assert split_exc.value.details.get("rule") == "unsupported_adjustment"
    # No outbound request when interval/adjustment rejected before fetch.
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "bars"])
@pytest.mark.parametrize("response_symbol", [None, "", "AAPL", 123, API_KEY])
async def test_response_symbol_identity_rejected_without_echo(
    operation: str, response_symbol: object
) -> None:
    payload = _global_quote() if operation == "quote" else _daily_series(_success_rows())
    identity = payload["Global Quote"] if operation == "quote" else payload["Meta Data"]
    field = "01. symbol" if operation == "quote" else "2. Symbol"
    if response_symbol is None:
        identity.pop(field)
    else:
        identity[field] = response_symbol
    adapter = _adapter(ScriptedTransport([json.dumps(payload).encode()]))
    with pytest.raises(DataContractError) as exc:
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
    assert exc.value.details["rule"] == "symbol_identity"
    assert exc.value.details["operation"] == operation
    _assert_no_secret(exc.value)
    assert "AAPL" not in str(exc.value.details)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "bars"])
async def test_response_symbol_identity_accepts_case_and_space(operation: str) -> None:
    payload = _global_quote() if operation == "quote" else _daily_series(_success_rows())
    identity = payload["Global Quote"] if operation == "quote" else payload["Meta Data"]
    identity["01. symbol" if operation == "quote" else "2. Symbol"] = " nvda "
    adapter = _adapter(ScriptedTransport([json.dumps(payload).encode()]))
    if operation == "quote":
        success = await adapter.get_quote(_instrument(), AS_OF)
        assert success.value.instrument_id == "equity:US:NVDA"
    else:
        bars = await adapter.get_bars(
            _instrument(),
            start=date(2026, 7, 13),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
        assert bars.value.instrument_id == "equity:US:NVDA"


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, [], "NVDA"])
async def test_daily_series_metadata_identity_required(metadata: object) -> None:
    payload = _daily_series(_success_rows())
    payload["Meta Data"] = metadata
    adapter = _adapter(ScriptedTransport([json.dumps(payload).encode()]))
    with pytest.raises(DataContractError) as exc:
        await adapter.get_bars(
            _instrument(),
            start=date(2026, 7, 13),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )
    assert exc.value.details["rule"] == "symbol_identity"


@pytest.mark.asyncio
async def test_absent_daily_series_preserves_no_market_data() -> None:
    adapter = _adapter(ScriptedTransport([b"{}"]))
    with pytest.raises(NoMarketData):
        await adapter.get_bars(
            _instrument(),
            start=date(2026, 7, 13),
            end=date(2026, 7, 17),
            interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE,
            as_of=AS_OF,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "bars"])
@pytest.mark.parametrize("cutoff", ["12:59:59", "13:00:00", "13:30:00"])
async def test_early_close_daily_cutoff(operation: str, cutoff: str) -> None:
    day = date(2026, 11, 27)
    as_of = datetime.fromisoformat(f"2026-11-27T{cutoff}").replace(tzinfo=NY)
    payload = (
        _global_quote(latest_day=day.isoformat()) if operation == "quote"
        else _daily_series({day.isoformat(): _success_rows()["2026-07-17"]})
    )
    adapter = AlphaVantageAdapter(
        ScriptedTransport([json.dumps(payload).encode()]), api_keys=(API_KEY,),
        clock=FixedClock(as_of),
    )
    if cutoff == "12:59:59":
        with pytest.raises(NoMarketData):
            if operation == "quote":
                await adapter.get_quote(_instrument(), as_of)
            else:
                await adapter.get_bars(
                    _instrument(), start=day, end=day, interval=USBarInterval.ONE_DAY,
                    adjustment=AdjustmentMethod.NONE, as_of=as_of,
                )
        return
    if operation == "quote":
        result = await adapter.get_quote(_instrument(), as_of)
        assert result.value.quote_at == datetime(2026, 11, 27, 13, tzinfo=NY)
        assert result.value.last == Decimal("122.5")
        assert result.value.session is TradingSession.REGULAR
        assert result.value.previous_close == Decimal("119.0")
    else:
        bars = await adapter.get_bars(
            _instrument(), start=day, end=day, interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE, as_of=as_of,
        )
        assert bars.value.bars[-1].timestamp == datetime(2026, 11, 27, 13, tzinfo=NY)
        assert bars.value.bars[-1].close == Decimal("122.5")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "bars"])
@pytest.mark.parametrize(("day", "utc_hour"), [(date(2026, 3, 6), 21), (date(2026, 3, 9), 20)])
async def test_normal_daily_close_is_dst_aware(
    operation: str, day: date, utc_hour: int
) -> None:
    as_of = datetime(day.year, day.month, day.day, 16, tzinfo=NY)
    payload = (
        _global_quote(latest_day=day.isoformat()) if operation == "quote"
        else _daily_series({day.isoformat(): _success_rows()["2026-07-17"]})
    )
    adapter = AlphaVantageAdapter(
        ScriptedTransport([json.dumps(payload).encode()]), api_keys=(API_KEY,),
        clock=FixedClock(as_of),
    )
    if operation == "quote":
        quote = await adapter.get_quote(_instrument(), as_of)
        timestamp = quote.value.quote_at
        warnings = quote.meta.warnings
    else:
        bars = await adapter.get_bars(
            _instrument(), start=day, end=day, interval=USBarInterval.ONE_DAY,
            adjustment=AdjustmentMethod.NONE, as_of=as_of,
        )
        timestamp = bars.value.bars[-1].timestamp
        warnings = bars.meta.warnings
    assert timestamp == as_of
    assert timestamp.astimezone(ZoneInfo("UTC")).hour == utc_hour
    assert "US_NON_SESSION_DAILY_BAR" not in warnings


@pytest.mark.asyncio
async def test_historical_adjusted_early_close_and_future_row_are_cutoff_safe() -> None:
    day = date(2026, 11, 27)
    as_of = datetime(2026, 11, 27, 13, tzinfo=NY)
    payload = _daily_series({
        day.isoformat(): _success_rows()["2026-07-17"],
        "2099-01-02": None,
    })
    adapter = AlphaVantageAdapter(
        ScriptedTransport([json.dumps(payload).encode()]), api_keys=(API_KEY,),
        clock=FixedClock(datetime(2026, 11, 30, 16, tzinfo=NY)),
    )
    result = await adapter.get_bars(
        _instrument(), start=day, end=date(2099, 1, 2), interval=USBarInterval.ONE_DAY,
        adjustment=AdjustmentMethod.SPLIT_AND_DIVIDEND_ADJUSTED, as_of=as_of,
    )
    assert len(result.value.bars) == 1
    assert result.value.bars[0].timestamp == as_of
    assert result.value.bars[0].close == Decimal("61.25")
    assert result.value.bars[0].open == Decimal("61.0")
    assert result.meta.as_of == as_of
    assert result.meta.fetched_at > as_of
    assert "US_NON_SESSION_DAILY_BAR" not in result.meta.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("cutoff", ["15:59:59", "16:00:00"])
async def test_non_session_daily_row_warns_only_if_included(cutoff: str) -> None:
    as_of = datetime.fromisoformat(f"2026-11-26T{cutoff}").replace(tzinfo=NY)
    row = _success_rows()["2026-07-17"]
    payload = _daily_series({"2026-11-25": row, "2026-11-26": row})
    adapter = AlphaVantageAdapter(
        ScriptedTransport([json.dumps(payload).encode()]), api_keys=(API_KEY,),
        clock=FixedClock(as_of),
    )
    result = await adapter.get_bars(
        _instrument(), start=date(2026, 11, 25), end=date(2026, 11, 26),
        interval=USBarInterval.ONE_DAY, adjustment=AdjustmentMethod.NONE, as_of=as_of,
    )
    included = cutoff == "16:00:00"
    assert len(result.value.bars) == (2 if included else 1)
    assert ("US_NON_SESSION_DAILY_BAR" in result.meta.warnings) is included
    assert result.value.bars[-1].timestamp <= as_of


@pytest.mark.asyncio
async def test_non_session_quote_warns_and_retains_conservative_close() -> None:
    as_of = datetime(2026, 11, 26, 16, tzinfo=NY)
    adapter = AlphaVantageAdapter(
        ScriptedTransport([json.dumps(_global_quote(latest_day="2026-11-26")).encode()]),
        api_keys=(API_KEY,), clock=FixedClock(as_of),
    )
    result = await adapter.get_quote(_instrument(), as_of)
    assert result.value.quote_at == as_of
    assert result.meta.warnings == ("US_NON_SESSION_DAILY_BAR",)


class _BrokenDailyCalendar:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def session_at(self, moment: datetime) -> MarketSession | None:
        day = moment.astimezone(NY).date()
        if self.mode == "error":
            raise RuntimeError(f"private {API_KEY} calendar failure")
        if self.mode == "naive":
            return MarketSession(day, datetime(day.year, day.month, day.day, 13))
        if self.mode == "session_date":
            return MarketSession(date(2026, 11, 25), datetime(2026, 11, 27, 13, tzinfo=NY))
        return MarketSession(day, datetime(2026, 11, 30, 13, tzinfo=NY))

    def session_on_or_before(self, moment: datetime) -> MarketSession | None:
        raise AssertionError("unexpected operation")

    def previous_session(self, session_date: date) -> MarketSession | None:
        raise AssertionError("unexpected operation")

    def next_session(self, session_date: date) -> MarketSession | None:
        raise AssertionError("unexpected operation")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["quote", "bars"])
@pytest.mark.parametrize("mode", ["error", "naive", "session_date", "close_date", "bounds"])
async def test_calendar_failures_are_sanitized(operation: str, mode: str) -> None:
    day = date(1900, 1, 2) if mode == "bounds" else date(2026, 11, 27)
    as_of = datetime(day.year, day.month, day.day, 16, tzinfo=NY)
    payload = (
        _global_quote(latest_day=day.isoformat()) if operation == "quote"
        else _daily_series({day.isoformat(): _success_rows()["2026-07-17"]})
    )
    adapter = AlphaVantageAdapter(
        ScriptedTransport([json.dumps(payload).encode()]), api_keys=(API_KEY,),
        clock=FixedClock(as_of),
        us_session_calendar=None if mode == "bounds" else _BrokenDailyCalendar(mode),
    )
    with pytest.raises(DataContractError) as exc:
        if operation == "quote":
            await adapter.get_quote(_instrument(), as_of)
        else:
            await adapter.get_bars(
                _instrument(), start=day, end=day, interval=USBarInterval.ONE_DAY,
                adjustment=AdjustmentMethod.NONE, as_of=as_of,
            )
    assert exc.value.details["rule"] == "session_calendar_unavailable"
    assert exc.value.__suppress_context__ is True
    assert "private" not in str(exc.value)
    _assert_no_secret(exc.value)


@pytest.mark.asyncio
async def test_future_quote_date_is_rejected_before_calendar_lookup() -> None:
    as_of = datetime(2026, 11, 27, 13, tzinfo=NY)
    adapter = AlphaVantageAdapter(
        ScriptedTransport([json.dumps(_global_quote(latest_day="2099-01-02")).encode()]),
        api_keys=(API_KEY,), clock=FixedClock(as_of),
        us_session_calendar=_BrokenDailyCalendar("error"),
    )
    with pytest.raises(NoMarketData):
        await adapter.get_quote(_instrument(), as_of)
