"""Lean contracts for Yahoo current fundamentals and corporate actions."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import AssetType, DataCategory, Market
from domain.common.errors import NoMarketData
from domain.instruments.models import Instrument
from domain.us_research.enums import (
    USCorporateActionType,
    USFundamentalBasis,
    USStatementFrequency,
    USStatementView,
)
from infrastructure.providers.us.research_codecs import us_corporate_actions_codec
from infrastructure.providers.us.yahoo_finance_research import (
    YahooFinanceResearchAdapter,
)

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 17, 16, 5, tzinfo=NY)


class StubTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(self.payload).encode(),
        )


class StubFundamentalsClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, float]] = []

    async def get_info(self, symbol: str, *, timeout_seconds: float) -> dict[str, object]:
        self.calls.append((symbol, timeout_seconds))
        return self.payload


class StubStatementsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    async def get_tables(self, symbol: str, *, frequency: str, timeout_seconds: float):
        self.calls.append((symbol, frequency, timeout_seconds))
        period = date(2026, 3, 31)
        return {
            "income": ((period, {"TotalRevenue": 100, "NetIncome": 20}),),
            "balance_sheet": (
                (period, {"CashAndCashEquivalents": 50, "CurrentAssets": 80,
                          "CurrentLiabilities": 40, "LongTermDebt": 10}),
            ),
            "cash_flow": (
                (period, {"OperatingCashFlow": 30, "CapitalExpenditure": -8}),
            ),
        }


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


@pytest.mark.asyncio
async def test_current_fundamentals_normalize_profile_metrics_and_request() -> None:
    transport = StubTransport({})
    client = StubFundamentalsClient(
        {
            "longName": "NVIDIA Corporation",
            "sector": "Technology",
            "industry": "Semiconductors",
            "country": "United States",
            "fullTimeEmployees": 36000,
            "marketCap": 4100000000000,
            "trailingPE": 41.25,
            "forwardPE": 32.5,
            "sharesOutstanding": 24500000000,
            "netIncomeToCommon": 72000000000,
            "totalRevenue": 130000000000,
            "freeCashflow": 52000000000,
            "totalCash": 60000000000,
            "totalDebt": 10000000000,
        }
    )
    adapter = YahooFinanceResearchAdapter(
        transport, clock=FixedClock(NOW), fundamentals_client=client
    )

    success = await adapter.get_fundamental_snapshot(_instrument(), NOW)

    assert success.value.profile is not None
    assert success.value.profile.legal_name == "NVIDIA Corporation"
    assert success.value.metrics is not None
    assert success.value.metrics.trailing_pe == Decimal("41.25")
    assert success.value.metrics.net_income == Decimal("72000000000")
    assert success.value.metrics.net_cash_or_debt == Decimal("50000000000")
    assert success.value.metrics.estimate_revision is None
    assert success.value.metrics.basis is USFundamentalBasis.CURRENT
    assert not success.value.degraded
    assert success.meta.category is DataCategory.FUNDAMENTALS
    assert client.calls == [("NVDA", 15.0)]
    assert transport.requests == []


@pytest.mark.asyncio
async def test_historical_fundamentals_are_rejected_before_network() -> None:
    transport = StubTransport({})
    client = StubFundamentalsClient({})
    adapter = YahooFinanceResearchAdapter(
        transport, clock=FixedClock(NOW), fundamentals_client=client
    )

    with pytest.raises(NoMarketData):
        await adapter.get_fundamental_snapshot(
            _instrument(), datetime(2026, 7, 16, 16, 5, tzinfo=NY)
        )

    assert transport.requests == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_current_statements_normalize_capex_and_financial_quality_inputs() -> None:
    client = StubStatementsClient()
    adapter = YahooFinanceResearchAdapter(
        StubTransport({}), clock=FixedClock(NOW), statements_client=client
    )

    success = await adapter.get_financial_statements(
        _instrument(),
        frequency=USStatementFrequency.QUARTERLY,
        limit=4,
        as_of=NOW,
        view=USStatementView.LATEST,
    )

    assert success.value.view is USStatementView.LATEST
    assert success.value.cash_flow[0].line_items[:2] == (
        ("operating_cash_flow", Decimal("30")),
        ("capital_expenditure", Decimal("8")),
    )
    assert success.meta.warnings == ("YAHOO_STATEMENTS_CURRENT_ONLY",)
    assert client.calls == [("NVDA", "quarterly", 15.0)]


@pytest.mark.asyncio
async def test_actions_apply_inclusive_cutoff_normalize_and_dedupe() -> None:
    dividend_ts = int(datetime(2026, 7, 15, 12, tzinfo=NY).timestamp())
    split_ts = int(datetime(2026, 7, 17, 12, tzinfo=NY).timestamp())
    future_ts = int(datetime(2026, 7, 18, 12, tzinfo=NY).timestamp())
    transport = StubTransport(
        {
            "chart": {
                "error": None,
                "result": [
                    {
                        "events": {
                            "dividends": {
                                "a": {"date": dividend_ts, "amount": 0.01},
                                "duplicate": {"date": dividend_ts, "amount": 0.01},
                                "future": {"date": future_ts, "amount": 0.02},
                            },
                            "splits": {
                                "b": {
                                    "date": split_ts,
                                    "splitRatio": "10:1",
                                }
                            },
                        }
                    }
                ],
            }
        }
    )
    adapter = YahooFinanceResearchAdapter(transport, clock=FixedClock(NOW))

    success = await adapter.get_corporate_actions(
        _instrument(), start=date(2026, 7, 15), end=None, as_of=NOW
    )

    assert [item.action_type for item in success.value] == [
        USCorporateActionType.SPLIT,
        USCorporateActionType.DIVIDEND,
    ]
    assert success.value[0].ratio == Decimal("10")
    assert success.value[1].amount == Decimal("0.01")
    assert us_corporate_actions_codec().codec_id == "us.corporate_actions.v1"


@pytest.mark.asyncio
async def test_news_excludes_undated_and_after_as_of_rows() -> None:
    transport = StubTransport(
        {
            "news": [
                {
                    "uuid": "kept",
                    "title": "Nvidia update",
                    "providerPublishTime": int(datetime(2026, 7, 17, 15, tzinfo=NY).timestamp()),
                    "publisher": "Wire",
                    "link": "https://example.test/kept",
                },
                {"uuid": "undated", "title": "Cannot prove publication time"},
                {
                    "uuid": "future",
                    "title": "Future row",
                    "providerPublishTime": int(datetime(2026, 7, 17, 17, tzinfo=NY).timestamp()),
                },
            ]
        }
    )
    success = await YahooFinanceResearchAdapter(transport, clock=FixedClock(NOW)).get_news(
        _instrument(), query=None, start=date(2026, 7, 17), end=None, limit=20, as_of=NOW
    )

    assert [item.article_id for item in success.value.articles] == ["yahoo:kept"]
    assert transport.requests[0].url.endswith("/v1/finance/search")


@pytest.mark.asyncio
async def test_news_accepts_us_etf_and_uses_exact_symbol_query() -> None:
    transport = StubTransport({"news": []})

    success = await YahooFinanceResearchAdapter(transport, clock=FixedClock(NOW)).get_news(
        _etf(), query=None, start=date(2026, 7, 17), end=None, limit=20, as_of=NOW
    )

    assert success.value.instrument_id == "etf:US:UGL"
    assert transport.requests[0].params["q"] == "UGL"
