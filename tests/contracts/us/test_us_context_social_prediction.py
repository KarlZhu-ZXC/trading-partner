"""Lean Phase 1H social and prediction-provider contracts."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from application.ports.http_transport import HttpRequest, HttpResponse
from conftest import FixedClock
from domain.common.enums import AssetType, Freshness, Market
from domain.common.errors import NoMarketData, ProviderRateLimitError
from domain.instruments.models import Instrument
from domain.us_context.enums import (
    USSentimentDirection,
    USSentimentLabelOrigin,
)
from infrastructure.persistence.reddit_state_store import InMemoryRedditStateStore
from infrastructure.providers.us.moomoo_sentiment import MoomooSentimentAdapter
from infrastructure.providers.us.polymarket import PolymarketPredictionAdapter
from infrastructure.providers.us.reddit import RedditSentimentAdapter
from infrastructure.providers.us.reddit_apify import ApifyRedditClient, ApifyRedditPost
from infrastructure.providers.us.stocktwits import StockTwitsSentimentAdapter

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


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


def _amd_instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:US:AMD",
        symbol="AMD",
        name="Advanced Micro Devices",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


def _ai_instrument() -> Instrument:
    return Instrument(
        instrument_id="equity:US:AI",
        symbol="AI",
        name="C3.ai",
        market=Market.US,
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )


class PayloadTransport:
    def __init__(self, payload: bytes, content_type: str) -> None:
        self.payload = payload
        self.content_type = content_type
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(200, {"content-type": self.content_type}, self.payload)


class RateLimitedAfterOneTransport(PayloadTransport):
    async def send(self, request: HttpRequest) -> HttpResponse:
        if self.requests:
            raise ProviderRateLimitError("limited")
        return await super().send(request)


class SequenceTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class BlockingTransport(PayloadTransport):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload, "application/atom+xml")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return HttpResponse(200, {"content-type": self.content_type}, self.payload)


@pytest.mark.asyncio
async def test_stocktwits_keeps_only_explicit_user_labels() -> None:
    transport = PayloadTransport(
        json.dumps(
            {
                "messages": [
                    {
                        "id": 1,
                        "body": "Demand remains strong",
                        "created_at": "2026-07-18T11:00:00Z",
                        "entities": {"sentiment": {"basic": "Bullish"}},
                        "likes": {"total": 7},
                    },
                    {
                        "id": 2,
                        "body": "No label",
                        "created_at": "2026-07-18T11:30:00Z",
                        "entities": {"sentiment": None},
                    },
                ]
            }
        ).encode(),
        "application/json",
    )
    success = await StockTwitsSentimentAdapter(
        transport, clock=FixedClock(NOW)
    ).get_sentiment_samples(_instrument(), start=None, end=None, limit=20, as_of=NOW)

    assert len(success.value) == 1
    assert success.value[0].label_origin is USSentimentLabelOrigin.USER_LABEL
    assert success.value[0].direction is USSentimentDirection.BULLISH
    assert success.value[0].likes == 7


@pytest.mark.asyncio
async def test_reddit_rss_inference_keeps_engagement_unknown_and_versioned() -> None:
    rss = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>NVDA upside looks bullish</title>
      <published>2026-07-18T10:00:00Z</published>
      <link href="https://reddit.test/post/1" />
      <content type="html">Strong growth, long.</content>
    </entry></feed>"""
    transport = PayloadTransport(rss, "application/atom+xml")
    success = await RedditSentimentAdapter(
        transport,
        user_agent="TradingPartner/1.0",
        subreddits=("wallstreetbets", "stocks", "investing"),
        clock=FixedClock(NOW),
        min_interval_seconds=0,
    ).get_sentiment_samples(_instrument(), start=None, end=None, limit=20, as_of=NOW)

    assert len(success.value) == 1
    sample = success.value[0]
    assert sample.label_origin is USSentimentLabelOrigin.DETERMINISTIC_INFERENCE
    assert sample.classifier_version == "reddit_lexicon_v1"
    assert sample.likes is None and sample.comments is None
    assert len(transport.requests) == 3


@pytest.mark.asyncio
async def test_moomoo_feed_is_exact_filtered_deterministic_and_cached() -> None:
    recent = int((NOW - timedelta(minutes=15)).timestamp())
    newer = int((NOW - timedelta(minutes=10)).timestamp())
    payload = {
        "code": 0,
        "data": [
            {
                "id": "bull-old",
                "title": '<nnstock stockcode="NVDA" stocksymbol="NVDA.US">NVDA</nnstock>',
                "desc": "Strong demand supports a breakout with more upside ahead.",
                "publish_time": str(recent),
                "url": "https://example.test/bull-old",
            },
            {
                "id": "bull-new",
                "title": '<nnstock stockcode="NVDA" stocksymbol="NVDA.US">NVDA</nnstock>',
                "desc": "Strong demand supports a breakout with more upside ahead.",
                "publish_time": str(newer),
                "url": "https://example.test/bull-new",
            },
            {
                "id": "bear",
                "title": '<nnstock stockcode="NVDA" stocksymbol="NVDA.US">NVDA</nnstock>',
                "desc": "估值高估且需求疲软，我选择减仓并防范下跌风险。",
                "publish_time": str(recent),
            },
            {
                "id": "ticker-only",
                "title": '<nnstock stockcode="NVDA" stocksymbol="NVDA.US">$NVDA</nnstock>',
                "desc": "",
                "publish_time": str(recent),
            },
            {
                "id": "irrelevant",
                "title": '<nnstock stockcode="INTC" stocksymbol="INTC.US">INTC</nnstock>',
                "desc": "Bullish earnings beat and strong demand support a breakout.",
                "publish_time": str(recent),
            },
        ],
    }
    transport = PayloadTransport(json.dumps(payload).encode(), "application/json")
    adapter = MoomooSentimentAdapter(transport, clock=FixedClock(NOW))

    first = await adapter.get_sentiment_samples(
        _instrument(), start=None, end=None, limit=20, as_of=NOW
    )
    second = await adapter.get_sentiment_samples(
        _instrument(), start=None, end=None, limit=20, as_of=NOW
    )

    assert first.value == second.value
    assert len(first.value) == 2
    assert [sample.direction for sample in first.value] == [
        USSentimentDirection.BULLISH,
        USSentimentDirection.BEARISH,
    ]
    assert all(
        sample.label_origin is USSentimentLabelOrigin.DETERMINISTIC_INFERENCE
        and sample.classifier_version == "moomoo_rules_v1"
        and sample.likes is None
        and sample.comments is None
        for sample in first.value
    )
    assert first.value[0].url == "https://example.test/bull-new"
    assert second.meta.freshness is Freshness.FRESH
    assert "MOOMOO_SENTIMENT_DETERMINISTIC_RULES" in first.meta.warnings
    assert len(transport.requests) == 1
    assert transport.requests[0].url.endswith("/stock_feed")
    assert transport.requests[0].params == {"keyword": "NVDA", "size": "30"}


@pytest.mark.asyncio
async def test_reddit_is_serial_and_stops_after_rate_limit() -> None:
    rss = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>NVDA bullish</title><published>2026-07-18T10:00:00Z</published>
      <content type="html">growth</content>
    </entry></feed>"""
    transport = RateLimitedAfterOneTransport(rss, "application/atom+xml")
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = await RedditSentimentAdapter(
        transport,
        user_agent="TradingPartner/1.0",
        subreddits=("wallstreetbets", "stocks", "investing"),
        clock=FixedClock(NOW),
        min_interval_seconds=6,
        sleep=record_sleep,
    ).get_sentiment_samples(_instrument(), start=None, end=None, limit=20, as_of=NOW)

    assert len(result.value) == 1
    assert len(transport.requests) == 1
    assert sleeps == [6]
    assert result.meta.warnings == ("REDDIT_PARTIAL", "REDDIT_RATE_LIMITED")


@pytest.mark.asyncio
async def test_reddit_uses_retry_headers_shared_cooldown_and_stale_success() -> None:
    rss = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>NVDA bullish</title><published>2026-07-18T10:00:00Z</published>
      <content type="html">growth</content>
    </entry></feed>"""
    clock = FixedClock(NOW)
    store = InMemoryRedditStateStore()
    transport = SequenceTransport(
        [
            HttpResponse(200, {"content-type": "application/atom+xml"}, rss),
            HttpResponse(
                429,
                {"Retry-After": "120", "X-Ratelimit-Reset": "60"},
                b"",
            ),
        ]
    )
    adapter = RedditSentimentAdapter(
        transport,
        user_agent="TradingPartner/1.0",
        subreddits=("stocks",),
        clock=clock,
        min_interval_seconds=0,
        cache_ttl_seconds=60,
        state_store=store,
    )

    fresh = await adapter.get_sentiment_samples(
        _instrument(), start=None, end=None, limit=20, as_of=NOW
    )
    assert fresh.meta.freshness is Freshness.FRESH

    clock.advance(61)
    stale = await adapter.get_sentiment_samples(
        _instrument(), start=None, end=None, limit=20, as_of=clock.now()
    )
    assert stale.meta.freshness is Freshness.STALE
    assert stale.meta.warnings == ("REDDIT_RATE_LIMITED",)
    assert len(stale.value) == 1
    assert len(transport.requests) == 2

    # The shared cooldown prevents another probe and serves the same durable data.
    again = await adapter.get_sentiment_samples(
        _instrument(), start=None, end=None, limit=20, as_of=clock.now()
    )
    assert again.meta.freshness is Freshness.STALE
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_reddit_coalesces_concurrent_instrument_refreshes() -> None:
    rss = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>NVDA upside</title><published>2026-07-18T10:00:00Z</published>
      <content type="html">growth</content>
    </entry></feed>"""
    transport = BlockingTransport(rss)
    adapter = RedditSentimentAdapter(
        transport,
        user_agent="TradingPartner/1.0",
        subreddits=("stocks",),
        clock=FixedClock(NOW),
        min_interval_seconds=0,
    )

    first = asyncio.create_task(
        adapter.get_sentiment_samples(_instrument(), start=None, end=None, limit=20, as_of=NOW)
    )
    await transport.started.wait()
    second = asyncio.create_task(
        adapter.get_sentiment_samples(_instrument(), start=None, end=None, limit=5, as_of=NOW)
    )
    await asyncio.sleep(0)
    assert len(transport.requests) == 1
    transport.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert len(first_result.value) == len(second_result.value) == 1
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_reddit_requests_custom_subreddits_in_order() -> None:
    rss = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>NVDA upside looks bullish</title>
      <published>2026-07-18T10:00:00Z</published>
      <content type="html">Strong growth, long.</content>
    </entry></feed>"""
    transport = PayloadTransport(rss, "application/atom+xml")
    subreddits = ("moon_shot", "growth_stocks")

    await RedditSentimentAdapter(
        transport,
        user_agent="TradingPartner/1.0",
        subreddits=subreddits,
        clock=FixedClock(NOW),
        min_interval_seconds=0,
    ).get_sentiment_samples(_instrument(), start=None, end=None, limit=20, as_of=NOW)

    assert [request.url for request in transport.requests] == [
        f"https://www.reddit.com/r/{name}/search.rss" for name in subreddits
    ]


@pytest.mark.asyncio
async def test_reddit_rate_limit_uses_bounded_apify_fallback_and_caches_result() -> None:
    actor_post = {
        "dataType": "post",
        "id": "t3_apify1",
        "parsedId": "apify1",
        "communityName": "r/stocks",
        "parsedCommunityName": "stocks",
        "title": "NVDA upside remains bullish",
        "body": "NVIDIA growth supports a long thesis",
        "createdAt": "2026-07-18T10:00:00Z",
        "postUrl": "https://www.reddit.com/r/stocks/comments/apify1/example/",
        "score": 42,
        "commentsCount": 9,
    }
    transport = SequenceTransport(
        [
            HttpResponse(429, {"Retry-After": "120"}, b""),
            HttpResponse(
                201,
                {},
                json.dumps(
                    {
                        "data": {
                            "id": "run1",
                            "status": "SUCCEEDED",
                            "defaultDatasetId": "dataset1",
                        }
                    }
                ).encode(),
            ),
            HttpResponse(200, {}, json.dumps([actor_post]).encode()),
        ]
    )
    adapter = RedditSentimentAdapter(
        transport,
        user_agent="TradingPartner/1.0",
        subreddits=("stocks",),
        clock=FixedClock(NOW),
        min_interval_seconds=0,
        apify_enabled=True,
        apify_api_token="test-apify-token",
        apify_subreddits=("stocks", "shortsqueeze"),
        apify_lookback_days={"stocks": 7, "shortsqueeze": 2},
        apify_max_charge_usd=Decimal("0.20"),
        apify_batch_window_seconds=0,
    )

    first = await adapter.get_sentiment_samples(
        _instrument(), start=None, end=None, limit=20, as_of=NOW
    )
    second = await adapter.get_sentiment_samples(
        _instrument(), start=None, end=None, limit=20, as_of=NOW
    )

    assert first.value == second.value
    assert len(first.value) == 1
    assert first.value[0].likes == 42
    assert first.value[0].comments == 9
    assert first.meta.warnings == ("REDDIT_RATE_LIMITED", "REDDIT_APIFY_FALLBACK")
    assert len(transport.requests) == 3
    start_request = transport.requests[1]
    assert start_request.url.endswith("/acts/harshmaur~reddit-scraper/runs")
    assert start_request.params == {"maxTotalChargeUsd": "0.20"}
    assert "test-apify-token" not in repr(start_request)
    payload = json.loads(start_request.body or b"{}")
    assert payload["maxPostsCount"] == 200
    assert len(payload["startUrls"]) == 2
    assert "t=week" in payload["startUrls"][0]["url"]
    assert "t=week" in payload["startUrls"][1]["url"]


@pytest.mark.asyncio
async def test_reddit_apify_coalesces_distinct_instruments_and_classifies_many_to_many() -> None:
    actor_post = {
        "dataType": "post",
        "id": "t3_pair",
        "parsedId": "pair",
        "communityName": "r/securityanalysis",
        "parsedCommunityName": "securityanalysis",
        "title": "NVDA and AMD valuation comparison",
        "body": "NVIDIA and Advanced Micro Devices both have upside",
        "createdAt": "2026-07-18T10:00:00Z",
        "postUrl": "https://www.reddit.com/r/securityanalysis/comments/pair/example/",
        "score": 18,
        "commentsCount": 4,
    }
    transport = SequenceTransport(
        [
            HttpResponse(
                201,
                {},
                json.dumps(
                    {
                        "data": {
                            "id": "run2",
                            "status": "SUCCEEDED",
                            "defaultDatasetId": "dataset2",
                        }
                    }
                ).encode(),
            ),
            HttpResponse(200, {}, json.dumps([actor_post]).encode()),
        ]
    )
    adapter = RedditSentimentAdapter(
        transport,
        user_agent="TradingPartner/1.0",
        subreddits=("stocks",),
        clock=FixedClock(NOW),
        enabled=False,
        min_interval_seconds=0,
        apify_enabled=True,
        apify_api_token="test-apify-token",
        apify_subreddits=("securityanalysis",),
        apify_lookback_days={"securityanalysis": 30},
        apify_max_charge_usd=Decimal("0.20"),
        apify_batch_window_seconds=0.01,
    )

    nvda_result, amd_result = await asyncio.gather(
        adapter.get_sentiment_samples(_instrument(), start=None, end=None, limit=20, as_of=NOW),
        adapter.get_sentiment_samples(_amd_instrument(), start=None, end=None, limit=20, as_of=NOW),
    )

    assert len(transport.requests) == 2
    assert [item.instrument_id for item in nvda_result.value] == ["equity:US:NVDA"]
    assert [item.instrument_id for item in amd_result.value] == ["equity:US:AMD"]
    payload = json.loads(transport.requests[0].body or b"{}")
    query_url = payload["startUrls"][0]["url"]
    assert "NVDA" in query_url and "AMD" in query_url
    assert "t=month" in query_url


def test_reddit_apify_requires_cashtag_or_company_name_for_ambiguous_symbol() -> None:
    generic = ApifyRedditPost(
        post_id="generic",
        subreddit="stocks",
        title="AI is changing every industry",
        body="A broad technology discussion",
        published_at=NOW,
        url=None,
        score=1,
        comments=1,
    )
    specific = ApifyRedditPost(
        post_id="specific",
        subreddit="stocks",
        title="$AI and C3.ai valuation",
        body="Company-specific discussion",
        published_at=NOW,
        url=None,
        score=1,
        comments=1,
    )

    assert ApifyRedditClient._matches(generic, _ai_instrument()) is False
    assert ApifyRedditClient._matches(specific, _ai_instrument()) is True


@pytest.mark.asyncio
async def test_polymarket_is_current_only_and_filters_closed_markets() -> None:
    payload = {
        "events": [
            {
                "slug": "fed-cut",
                "markets": [
                    {
                        "id": "open",
                        "question": "Will the Fed cut rates?",
                        "closed": False,
                        "endDate": "2026-09-01T00:00:00Z",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.65", "0.35"]',
                        "volumeNum": "1000000",
                    },
                    {
                        "id": "closed",
                        "question": "Old market",
                        "closed": True,
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["1", "0"]',
                    },
                ],
            }
        ]
    }
    transport = PayloadTransport(json.dumps(payload).encode(), "application/json")
    adapter = PolymarketPredictionAdapter(transport, clock=FixedClock(NOW))
    success = await adapter.get_prediction_market_context(topic="Fed cut", limit=6, as_of=NOW)
    assert [item.market_id for item in success.value.markets] == ["open"]
    assert success.value.markets[0].outcomes[0][1] == Decimal("0.65")

    with pytest.raises(NoMarketData):
        await adapter.get_prediction_market_context(
            topic="Fed cut", limit=6, as_of=NOW - timedelta(hours=1)
        )
    assert len(transport.requests) == 1
