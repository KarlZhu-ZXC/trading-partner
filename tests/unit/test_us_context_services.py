"""Lean H4 application aggregation and news-event acceptance."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.provider_routing import (
    ProviderResultMeta,
    RouterExecutionResult,
)
from application.services.us_company_update_service import USCompanyUpdateService
from application.services.us_context_services import USSentimentService
from conftest import FixedClock
from domain.common.enums import (
    CacheDisposition,
    DataCategory,
    DataCriticality,
    Freshness,
    SourceRole,
    TradingSession,
    VendorId,
)
from domain.us_context.enums import (
    USNewsScope,
    USSentimentDirection,
    USSentimentLabelOrigin,
    USSentimentSource,
)
from domain.us_context.models import USNewsArticle, USSentimentSample
from domain.us_research.enums import USExternalEventType

AS_OF = datetime(2026, 7, 18, 12, tzinfo=UTC)
IID = "equity:US:NVDA"


def _result(
    vendor: VendorId, samples: tuple[USSentimentSample, ...]
) -> RouterExecutionResult[tuple[USSentimentSample, ...]]:
    return RouterExecutionResult(
        value=samples,
        ok=True,
        criticality=DataCriticality.OPTIONAL,
        meta=ProviderResultMeta(
            vendor,
            DataCategory.SENTIMENT,
            SourceRole.SUPPLEMENTAL,
            AS_OF,
            AS_OF,
            Freshness.FRESH,
            TradingSession.UNKNOWN,
            None,
            CacheDisposition.MISS,
            None,
            None,
            (),
        ),
        attempts=(),
        warnings=(),
        error=None,
    )


@pytest.mark.asyncio
async def test_sentiment_service_keeps_sources_separate_and_shows_disagreement() -> None:
    reddit = USSentimentSample(
        IID,
        USSentimentSource.REDDIT,
        AS_OF,
        "Bearish inference",
        USSentimentDirection.BEARISH,
        USSentimentLabelOrigin.DETERMINISTIC_INFERENCE,
        Decimal(-1),
        None,
        None,
        None,
        "reddit_lexicon_v1",
    )
    moomoo = USSentimentSample(
        IID,
        USSentimentSource.MOOMOO,
        AS_OF,
        "Neutral deterministic mining",
        USSentimentDirection.NEUTRAL,
        USSentimentLabelOrigin.DETERMINISTIC_INFERENCE,
        Decimal(0),
        None,
        None,
        None,
        "moomoo_rules_v1",
    )
    router = MagicMock()
    router.execute = AsyncMock(
        side_effect=[
            _result(VendorId.REDDIT, (reddit,)),
            _result(VendorId.MOOMOO_FEED, (moomoo,)),
        ]
    )
    service = USSentimentService(router, FixedClock(AS_OF), MagicMock())

    result = await service.get_snapshot(
        MagicMock(instrument_id=IID), start=None, end=None, limit=20, as_of=AS_OF
    )

    assert [item.source for item in result.snapshot.summaries] == [
        USSentimentSource.REDDIT,
        USSentimentSource.MOOMOO,
    ]
    assert result.snapshot.disagreement == Decimal(1)
    assert not result.snapshot.degraded


def test_company_update_news_event_retains_typed_article() -> None:
    article = USNewsArticle(
        article_id="yahoo:one",
        instrument_id=IID,
        scope=USNewsScope.COMPANY,
        title="Nvidia update",
        summary=None,
        publisher="Wire",
        url="https://example.test/news",
        published_at=AS_OF,
        vendor=VendorId.YFINANCE,
        source_sentiment=None,
        relevance=None,
        dedupe_key="one",
    )

    event = USCompanyUpdateService._news_events((article,))[0]

    assert event.event_type is USExternalEventType.NEWS
    assert event.news_article is article
    assert event.visible_time == article.published_at
