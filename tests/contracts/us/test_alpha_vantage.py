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
from conftest import FixedClock
from domain.common.enums import (
    AdjustmentMethod,
    AssetType,
    DataCategory,
    Market,
    SourceRole,
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
    transport: ScriptedTransport, *, api_key: str | None = API_KEY, enabled: bool = True
) -> AlphaVantageAdapter:
    return AlphaVantageAdapter(
        transport,
        api_key=api_key,
        clock=CLOCK,
        enabled=enabled,
        max_fresh_seconds=60,
        max_delayed_seconds=3600,
    )


def _assert_no_secret(exc: BaseException) -> None:
    blob = f"{exc}{getattr(exc, 'message', '')}{getattr(exc, 'details', '')}"
    assert API_KEY not in blob
    assert "TEST_AV_SECRET" not in blob


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
async def test_missing_key_and_unsupported_interval() -> None:
    transport = ScriptedTransport([b"{}"])
    missing = _adapter(transport, api_key=None)
    assert missing.is_configured() is False
    with pytest.raises(ProviderNotConfigured):
        await missing.get_quote(_instrument(), AS_OF)

    blank = _adapter(transport, api_key="   ")
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
