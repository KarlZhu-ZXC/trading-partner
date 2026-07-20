"""Lean Phase 1H H1 contract acceptance."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from application.dto.provider_routing import ProviderResultMeta, ProviderSuccess
from application.dto.provider_state import CacheEntry
from application.dto.us_context import MarketGetLiveNewsInput, USGetMacroContextInput
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    Freshness,
    Market,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.common.errors import DataContractError
from domain.us_context.enums import USNewsScope, USSentimentLabelOrigin
from domain.us_context.models import USNewsArticle, USNewsFeed, USSentimentSample
from infrastructure.config.settings import AppSettings
from infrastructure.providers.us.context_codecs import (
    CODEC_US_MACRO_CONTEXT,
    CODEC_US_NEWS_FEED,
    CODEC_US_PREDICTION_MARKET_CONTEXT,
    CODEC_US_SENTIMENT_SAMPLES,
    us_news_feed_codec,
)

UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 7, 18, 12, tzinfo=UTC)
FETCHED_AT = datetime(2026, 7, 18, 12, 0, 1, tzinfo=UTC)
INSTRUMENT = "equity:US:NVDA"


def _feed() -> USNewsFeed:
    return USNewsFeed(
        instrument_id=INSTRUMENT,
        query=None,
        as_of=AS_OF,
        articles=(
            USNewsArticle(
                article_id="yahoo:one",
                instrument_id=INSTRUMENT,
                scope=USNewsScope.COMPANY,
                title="Nvidia reports results",
                summary=None,
                publisher="Example Wire",
                url="https://example.test/one",
                published_at=datetime(2026, 7, 18, 11, tzinfo=UTC),
                vendor=VendorId.YFINANCE,
                source_sentiment=Decimal("0.25"),
                relevance=Decimal("0.9"),
                dedupe_key="nvidia-reports-results",
            ),
        ),
        degraded=False,
        warning_codes=(),
    )


def test_h1_inputs_and_settings_keep_the_surface_bounded() -> None:
    assert MarketGetLiveNewsInput(instrument_id=INSTRUMENT).limit == 20
    assert len(USGetMacroContextInput().series_ids) == 12
    assert AppSettings.model_fields["fred_enabled"].default is True
    assert AppSettings.model_fields["reddit_user_agent"].default == "TradingPartner/1.0"
    with pytest.raises(ValidationError):
        MarketGetLiveNewsInput(query="macro", as_of=datetime(2026, 7, 18, 12))


def test_h1_domain_preserves_cutoffs_and_label_provenance() -> None:
    with pytest.raises(DataContractError):
        USNewsFeed(
            instrument_id=INSTRUMENT,
            query=None,
            as_of=datetime(2026, 7, 18, 10, tzinfo=UTC),
            articles=_feed().articles,
            degraded=False,
            warning_codes=(),
        )
    with pytest.raises(DataContractError):
        USSentimentSample(
            instrument_id=INSTRUMENT,
            source="reddit",  # type: ignore[arg-type]
            published_at=AS_OF,
            text="bullish",
            direction="bullish",  # type: ignore[arg-type]
            label_origin=USSentimentLabelOrigin.DETERMINISTIC_INFERENCE,
            score=Decimal("0.5"),
            likes=None,
            comments=None,
            url=None,
            classifier_version=None,
        )


def test_h1_news_codec_roundtrips_decimal_strings_and_inventory_is_versioned() -> None:
    codec = us_news_feed_codec()
    meta = ProviderResultMeta(
        vendor=VendorId.YFINANCE,
        category=DataCategory.NEWS,
        role=SourceRole.PRIMARY,
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
        freshness=Freshness.FRESH,
        session=TradingSession.UNKNOWN,
        latency_ms=1,
        cache_disposition=CacheDisposition.MISS,
        adjustment=None,
        data_delay_seconds=None,
        warnings=(),
    )
    payload = codec.encode(ProviderSuccess(value=_feed(), meta=meta))
    assert '"source_sentiment":"0.25"' in payload
    entry = CacheEntry(
        key="v1|US|news|equity:US:NVDA|2026-07-18T12:00:00+00:00|news|abcdef0123456789",
        market=Market.US,
        category=DataCategory.NEWS,
        instrument_id=INSTRUMENT,
        as_of=AS_OF,
        fetched_at=FETCHED_AT,
        expires_at=datetime(2026, 7, 18, 12, 5, tzinfo=UTC),
        freshness=Freshness.FRESH,
        vendor=VendorId.YFINANCE,
        payload_json=payload,
    )
    assert codec.decode(entry).value == _feed()
    assert {
        CODEC_US_NEWS_FEED,
        CODEC_US_MACRO_CONTEXT,
        CODEC_US_SENTIMENT_SAMPLES,
        CODEC_US_PREDICTION_MARKET_CONTEXT,
    } == {
        "us.news_feed.v1",
        "us.macro_context.v1",
        "us.sentiment_samples.v1",
        "us.prediction_market_context.v1",
    }
