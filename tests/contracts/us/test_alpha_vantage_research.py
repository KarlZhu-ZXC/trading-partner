"""Lean contracts for Alpha Vantage US research fallback."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import AssetType, Market
from domain.common.errors import NoMarketData
from domain.instruments.models import Instrument
from domain.us_research.enums import (
    USInsiderAcquiredDisposed,
    USStatementFrequency,
)
from infrastructure.providers.us.alpha_vantage_research import (
    AlphaVantageResearchAdapter,
)

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 17, 16, 5, tzinfo=NY)
API_KEY = "test-secret-key"


class FunctionTransport:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        function = request.params["function"]
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(self.payloads[function]).encode(),
        )


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:US:IBM",
        symbol="IBM",
        name="IBM",
        market=Market.US,
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _etf() -> Instrument:
    return Instrument(
        instrument_id="etf:US:UGL",
        symbol="UGL",
        name="ProShares Ultra Gold",
        market=Market.US,
        exchange="NYSEARCA",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.ETF,
    )


def _adapter(transport: FunctionTransport) -> AlphaVantageResearchAdapter:
    return AlphaVantageResearchAdapter(transport, api_keys=(API_KEY,), clock=FixedClock(NOW))


@pytest.mark.asyncio
async def test_overview_maps_current_facts_and_never_exposes_key() -> None:
    transport = FunctionTransport(
        {
            "OVERVIEW": {
                "Symbol": "IBM",
                "Name": "International Business Machines",
                "Sector": "Technology",
                "MarketCapitalization": "260000000000",
                "PERatio": "22.5",
                "RevenueTTM": "64000000000",
                "SharesOutstanding": "900000000",
            }
        }
    )

    success = await _adapter(transport).get_fundamental_snapshot(_instrument(), NOW)

    assert success.value.profile is not None
    assert success.value.profile.market_cap == Decimal("260000000000")
    assert success.value.metrics is not None
    assert success.value.metrics.trailing_pe == Decimal("22.5")
    assert success.value.degraded
    assert API_KEY not in repr(transport.requests[0])


@pytest.mark.asyncio
async def test_statements_map_three_functions_to_stable_line_keys() -> None:
    transport = FunctionTransport(
        {
            "INCOME_STATEMENT": {
                "quarterlyReports": [
                    {
                        "fiscalDateEnding": "2026-06-30",
                        "reportedCurrency": "USD",
                        "totalRevenue": "17000000000",
                        "netIncome": "2100000000",
                    }
                ]
            },
            "BALANCE_SHEET": {
                "quarterlyReports": [
                    {
                        "fiscalDateEnding": "2026-06-30",
                        "reportedCurrency": "USD",
                        "totalAssets": "140000000000",
                    }
                ]
            },
            "CASH_FLOW": {
                "quarterlyReports": [
                    {
                        "fiscalDateEnding": "2026-06-30",
                        "reportedCurrency": "USD",
                        "operatingCashflow": "4500000000",
                    }
                ]
            },
        }
    )

    success = await _adapter(transport).get_financial_statements(
        _instrument(), frequency=USStatementFrequency.QUARTERLY, limit=8, as_of=NOW
    )

    assert dict(success.value.income[0].line_items)["revenue"] == Decimal("17000000000")
    assert dict(success.value.balance_sheet[0].line_items)["total_assets"] == Decimal(
        "140000000000"
    )
    assert dict(success.value.cash_flow[0].line_items)["operating_cash_flow"] == Decimal(
        "4500000000"
    )


@pytest.mark.asyncio
async def test_insider_filters_dates_and_historical_as_of_fails_before_network() -> None:
    transport = FunctionTransport(
        {
            "INSIDER_TRANSACTIONS": {
                "data": [
                    {
                        "transaction_date": "2026-07-16",
                        "executive": "Jane Doe",
                        "executive_title": "Director",
                        "acquisition_or_disposal": "D",
                        "shares": "100",
                        "share_price": "285.50",
                    },
                    {
                        "transaction_date": "2026-07-10",
                        "executive": "Old Row",
                        "shares": "5",
                    },
                ]
            }
        }
    )
    adapter = _adapter(transport)

    success = await adapter.get_insider_activity(
        _instrument(), start=date(2026, 7, 15), end=None, limit=50, as_of=NOW
    )

    assert len(success.value) == 1
    assert success.value[0].acquired_disposed is USInsiderAcquiredDisposed.DISPOSED
    assert success.value[0].rule_10b5_1 is None
    request_count = len(transport.requests)
    with pytest.raises(NoMarketData):
        await adapter.get_insider_activity(
            _instrument(),
            start=None,
            end=None,
            limit=50,
            as_of=datetime(2026, 7, 16, 16, tzinfo=NY),
        )
    assert len(transport.requests) == request_count


@pytest.mark.asyncio
async def test_news_maps_sentiment_and_keeps_api_key_request_only() -> None:
    transport = FunctionTransport(
        {
            "NEWS_SENTIMENT": {
                "feed": [
                    {
                        "title": "IBM launches product",
                        "time_published": "20260717T190000",
                        "source": "Example",
                        "url": "https://example.test/ibm",
                        "overall_sentiment_score": "0.35",
                        "ticker_sentiment": [{"ticker": "IBM", "relevance_score": "0.91"}],
                    }
                ]
            }
        }
    )
    success = await _adapter(transport).get_news(
        _instrument(), query=None, start=date(2026, 7, 17), end=None, limit=10, as_of=NOW
    )

    article = success.value.articles[0]
    assert article.source_sentiment == Decimal("0.35")
    assert article.relevance == Decimal("0.91")
    assert API_KEY not in repr(transport.requests[0])
    assert transport.requests[0].params["time_to"] == "20260717T2359"


@pytest.mark.asyncio
async def test_news_accepts_us_etf_without_opening_equity_fundamentals() -> None:
    transport = FunctionTransport({"NEWS_SENTIMENT": {"feed": []}})

    success = await _adapter(transport).get_news(
        _etf(), query=None, start=date(2026, 7, 17), end=None, limit=10, as_of=NOW
    )

    assert success.value.instrument_id == "etf:US:UGL"
    assert transport.requests[0].params["tickers"] == "UGL"
