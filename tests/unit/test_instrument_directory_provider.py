"""Contract-focused parsing tests for instrument directory adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import AssetType, Market, VendorId
from infrastructure.providers.instrument_directory import (
    AlphaVantageInstrumentDirectoryAdapter,
    TencentInstrumentDirectoryAdapter,
    YahooInstrumentDirectoryAdapter,
)

NOW = datetime(2026, 7, 18, 4, 0, tzinfo=UTC)


class _Transport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.request: HttpRequest | None = None

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.request = request
        return HttpResponse(200, {"content-type": "application/json"}, self.body)


class _SequenceTransport:
    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = list(bodies)
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            200,
            {"content-type": "application/json"},
            self.bodies.pop(0),
        )


@pytest.mark.asyncio
async def test_yahoo_discovers_us_equity() -> None:
    transport = _Transport(
        b'{"quotes":[{"symbol":"KO","longname":"The Coca-Cola Company",'
        b'"quoteType":"EQUITY","exchange":"NYQ","exchangeTimezoneName":'
        b'"America/New_York"}]}'
    )
    adapter = YahooInstrumentDirectoryAdapter(transport, clock=FixedClock(NOW))

    result = await adapter.lookup(market=Market.US, query="KO", asset_type_hint=None, as_of=NOW)

    assert result.meta.vendor is VendorId.YFINANCE
    assert result.value[0].instrument_id == "equity:US:KO"
    assert result.value[0].exchange == "NYSE"


@pytest.mark.asyncio
async def test_alpha_vantage_discovers_us_equity_without_exposing_key() -> None:
    transport = _Transport(
        b'{"bestMatches":[{"1. symbol":"KO","2. name":"Coca-Cola",'
        b'"3. type":"Equity","4. region":"United States","8. currency":"USD"}]}'
    )
    adapter = AlphaVantageInstrumentDirectoryAdapter(
        transport, api_keys=("secret",), clock=FixedClock(NOW)
    )

    result = await adapter.lookup(
        market=Market.US, query="KO", asset_type_hint=AssetType.EQUITY, as_of=NOW
    )

    assert result.meta.vendor is VendorId.ALPHA_VANTAGE
    assert result.value[0].instrument_id == "equity:US:KO"
    assert transport.request is not None
    assert "secret" not in repr(transport.request)


@pytest.mark.asyncio
async def test_alpha_vantage_directory_fails_over_only_after_rate_limit() -> None:
    transport = _SequenceTransport(
        [
            b'{"Note":"API call frequency rate limit reached"}',
            b'{"bestMatches":[{"1. symbol":"KO","2. name":"Coca-Cola",'
            b'"3. type":"Equity","4. region":"United States","8. currency":"USD"}]}',
        ]
    )
    adapter = AlphaVantageInstrumentDirectoryAdapter(
        transport,
        api_keys=("first-secret", "second-secret"),
        clock=FixedClock(NOW),
    )

    result = await adapter.lookup(
        market=Market.US,
        query="KO",
        asset_type_hint=AssetType.EQUITY,
        as_of=NOW,
    )

    assert result.value[0].instrument_id == "equity:US:KO"
    assert [request.params["apikey"] for request in transport.requests] == [
        "first-secret",
        "second-secret",
    ]


@pytest.mark.asyncio
async def test_tencent_validates_and_classifies_a_share_etf() -> None:
    transport = _Transport('v_sh510050="1~50ETF~510050~2.50";'.encode("gbk"))
    adapter = TencentInstrumentDirectoryAdapter(transport, clock=FixedClock(NOW))

    result = await adapter.lookup(
        market=Market.A_SHARE, query="510050", asset_type_hint=None, as_of=NOW
    )

    assert result.meta.vendor is VendorId.TENCENT
    assert result.value[0].instrument_id == "etf:A_SHARE:510050.SH"
