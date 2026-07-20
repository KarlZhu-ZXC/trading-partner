"""Router-backed Phase 1H US context services."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from application.dto.provider_routing import (
    ProviderSuccess,
    RouterExecutionResult,
    ToolDataPolicy,
)
from application.ports.category_provider import CategoryProvider
from application.ports.clock import Clock
from application.ports.provider_cache_codec import ProviderCacheCodec
from application.ports.us_context_providers import (
    USMacroProvider,
    USNewsProvider,
    USPredictionMarketProvider,
    USSentimentProvider,
)
from application.services.provider_router import ProviderRouter
from application.services.us_market_data_service import build_us_fingerprint
from domain.common.enums import DataCategory, Market, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.instruments.models import Instrument
from domain.us_context.enums import USSentimentDirection, USSentimentSource
from domain.us_context.models import (
    USMacroContext,
    USNewsFeed,
    USPredictionMarketContext,
    USSentimentSample,
    USSentimentSnapshot,
    USSentimentSourceSummary,
)

OP_NEWS = "us.news_feed.v1"
OP_MACRO = "us.macro_context.v1"
OP_SENTIMENT_STOCKTWITS = "us.sentiment.stocktwits.v1"
OP_SENTIMENT_REDDIT = "us.sentiment.reddit.v1"
OP_PREDICTION = "us.prediction_market_context.v1"

_STOCKTWITS_POLICY = ToolDataPolicy(
    tool_name="us_get_sentiment_snapshot",
    required_categories=(),
    optional_categories=(DataCategory.SENTIMENT,),
    category_chain_overrides={DataCategory.SENTIMENT: (VendorId.STOCKTWITS,)},
)
_REDDIT_POLICY = ToolDataPolicy(
    tool_name="us_get_sentiment_snapshot",
    required_categories=(),
    optional_categories=(DataCategory.SENTIMENT,),
    category_chain_overrides={DataCategory.SENTIMENT: (VendorId.REDDIT,)},
)


def _request(clock: Clock, as_of: datetime) -> None:
    require_aware_datetime(as_of, field_name="as_of")
    if as_of > clock.now():
        raise DataContractError("as_of must not be in the future")


def _range(start: date | None, end: date | None) -> None:
    if start is not None and type(start) is not date:
        raise DataContractError("start must be a date")
    if end is not None and type(end) is not date:
        raise DataContractError("end must be a date")
    if start is not None and end is not None and start > end:
        raise DataContractError("start must be <= end")


class USNewsService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        codec: ProviderCacheCodec[USNewsFeed],
    ) -> None:
        self._router, self._clock, self._codec = router, clock, codec

    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        query: str | None,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> RouterExecutionResult[USNewsFeed]:
        _request(self._clock, as_of)
        _range(start, end)

        async def call(adapter: CategoryProvider) -> ProviderSuccess[USNewsFeed]:
            if not isinstance(adapter, USNewsProvider):
                raise DataContractError("adapter does not implement USNewsProvider")
            return await adapter.get_news(
                instrument, query=query, start=start, end=end, limit=limit, as_of=as_of
            )

        def validate(success: ProviderSuccess[USNewsFeed]) -> None:
            if success.meta.category is not DataCategory.NEWS or success.meta.as_of != as_of:
                raise DataContractError("news metadata does not match request")
            if not isinstance(success.value, USNewsFeed):
                raise DataContractError("news value has invalid type")
            expected_id = instrument.instrument_id if instrument else None
            if success.value.instrument_id != expected_id:
                raise DataContractError("news instrument does not match request")
            if any(item.published_at > as_of for item in success.value.articles):
                raise DataContractError("news article is after as_of")

        params = {
            "query": query or "",
            "start": start.isoformat() if start else "",
            "end": end.isoformat() if end else "",
            "limit": str(limit),
        }
        identity = instrument.instrument_id if instrument else "global"
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.NEWS,
            call=call,
            operation_name=OP_NEWS,
            request_fingerprint=build_us_fingerprint(OP_NEWS, identity, params, as_of),
            instrument=instrument,
            as_of=as_of,
            cache_codec=self._codec,
            result_validator=validate,
        )


class USMacroService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        codec: ProviderCacheCodec[USMacroContext],
    ) -> None:
        self._router, self._clock, self._codec = router, clock, codec

    async def get_context(
        self, *, series_ids: tuple[str, ...], lookback_days: int, as_of: datetime
    ) -> RouterExecutionResult[USMacroContext]:
        _request(self._clock, as_of)

        async def call(adapter: CategoryProvider) -> ProviderSuccess[USMacroContext]:
            if not isinstance(adapter, USMacroProvider):
                raise DataContractError("adapter does not implement USMacroProvider")
            return await adapter.get_macro_context(
                series_ids=series_ids, lookback_days=lookback_days, as_of=as_of
            )

        def validate(success: ProviderSuccess[USMacroContext]) -> None:
            if success.meta.category is not DataCategory.MACRO or success.meta.as_of != as_of:
                raise DataContractError("macro metadata does not match request")
            if not isinstance(success.value, USMacroContext) or success.value.as_of != as_of:
                raise DataContractError("macro value does not match request")
            if tuple(item.series_id for item in success.value.series) != series_ids:
                raise DataContractError("macro series do not match request")

        params = {"series": ",".join(series_ids), "lookback_days": str(lookback_days)}
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.MACRO,
            call=call,
            operation_name=OP_MACRO,
            request_fingerprint=build_us_fingerprint(OP_MACRO, "US", params, as_of),
            as_of=as_of,
            cache_codec=self._codec,
            result_validator=validate,
        )


@dataclass(frozen=True, slots=True)
class USSentimentServiceResult:
    snapshot: USSentimentSnapshot
    component_results: tuple[RouterExecutionResult[object], ...]


class USSentimentService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        codec: ProviderCacheCodec[tuple[USSentimentSample, ...]],
    ) -> None:
        self._router, self._clock, self._codec = router, clock, codec

    async def get_snapshot(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> USSentimentServiceResult:
        _request(self._clock, as_of)
        _range(start, end)
        results = await asyncio.gather(
            self._source(
                instrument,
                source=USSentimentSource.STOCKTWITS,
                policy=_STOCKTWITS_POLICY,
                operation=OP_SENTIMENT_STOCKTWITS,
                start=start,
                end=end,
                limit=limit,
                as_of=as_of,
            ),
            self._source(
                instrument,
                source=USSentimentSource.REDDIT,
                policy=_REDDIT_POLICY,
                operation=OP_SENTIMENT_REDDIT,
                start=start,
                end=end,
                limit=limit,
                as_of=as_of,
            ),
        )
        samples = tuple(
            sample
            for result in results
            if result.ok and isinstance(result.value, tuple)
            for sample in result.value
        )
        summaries = tuple(
            self._summary(source, tuple(item for item in samples if item.source is source))
            for source in USSentimentSource
            if source in {USSentimentSource.STOCKTWITS, USSentimentSource.REDDIT}
            and any(item.source is source for item in samples)
        )
        scores = [item.weighted_score for item in summaries if item.weighted_score is not None]
        disagreement = max(scores) - min(scores) if len(scores) > 1 else None
        failed = sum(not result.ok for result in results)
        snapshot = USSentimentSnapshot(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            summaries=summaries,
            samples=tuple(sorted(samples, key=lambda item: item.published_at, reverse=True)),
            disagreement=disagreement,
            degraded=bool(failed),
            warning_codes=("US_SENTIMENT_PARTIAL",) if failed else (),
        )
        return USSentimentServiceResult(snapshot, results)

    async def _source(
        self,
        instrument: Instrument,
        *,
        source: USSentimentSource,
        policy: ToolDataPolicy,
        operation: str,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> RouterExecutionResult[tuple[USSentimentSample, ...]]:
        async def call(
            adapter: CategoryProvider,
        ) -> ProviderSuccess[tuple[USSentimentSample, ...]]:
            if not isinstance(adapter, USSentimentProvider):
                raise DataContractError("adapter does not implement USSentimentProvider")
            return await adapter.get_sentiment_samples(
                instrument, start=start, end=end, limit=limit, as_of=as_of
            )

        def validate(success: ProviderSuccess[tuple[USSentimentSample, ...]]) -> None:
            if success.meta.category is not DataCategory.SENTIMENT or success.meta.as_of != as_of:
                raise DataContractError("sentiment metadata does not match request")
            if not isinstance(success.value, tuple) or any(
                not isinstance(item, USSentimentSample)
                or item.instrument_id != instrument.instrument_id
                or item.source is not source
                or item.published_at > as_of
                for item in success.value
            ):
                raise DataContractError("sentiment samples do not match request")

        params = {
            "source": source.value,
            "start": start.isoformat() if start else "",
            "end": end.isoformat() if end else "",
            "limit": str(limit),
        }
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.SENTIMENT,
            call=call,
            operation_name=operation,
            request_fingerprint=build_us_fingerprint(
                operation, instrument.instrument_id, params, as_of
            ),
            instrument=instrument,
            as_of=as_of,
            tool_policy=policy,
            cache_codec=self._codec,
            result_validator=validate,
        )

    @staticmethod
    def _summary(
        source: USSentimentSource, samples: tuple[USSentimentSample, ...]
    ) -> USSentimentSourceSummary:
        weights = tuple(
            Decimal(1) + Decimal(min(item.likes or 0, 100) + min(item.comments or 0, 100))
            for item in samples
        )
        total_weight = sum(weights, Decimal(0))
        weighted = (
            sum(
                (item.score * weight for item, weight in zip(samples, weights, strict=True)),
                Decimal(0),
            )
            / total_weight
            if total_weight
            else None
        )
        return USSentimentSourceSummary(
            source=source,
            label_origin=samples[0].label_origin,
            sample_count=len(samples),
            bullish_count=sum(item.direction is USSentimentDirection.BULLISH for item in samples),
            bearish_count=sum(item.direction is USSentimentDirection.BEARISH for item in samples),
            neutral_count=sum(item.direction is USSentimentDirection.NEUTRAL for item in samples),
            weighted_score=weighted,
            confidence=min(Decimal(1), Decimal(len(samples)) / Decimal(20)),
        )


class USPredictionMarketService:
    def __init__(
        self,
        router: ProviderRouter,
        clock: Clock,
        codec: ProviderCacheCodec[USPredictionMarketContext],
    ) -> None:
        self._router, self._clock, self._codec = router, clock, codec

    async def get_context(
        self, *, topic: str, limit: int, as_of: datetime
    ) -> RouterExecutionResult[USPredictionMarketContext]:
        _request(self._clock, as_of)

        async def call(adapter: CategoryProvider) -> ProviderSuccess[USPredictionMarketContext]:
            if not isinstance(adapter, USPredictionMarketProvider):
                raise DataContractError("adapter does not implement USPredictionMarketProvider")
            return await adapter.get_prediction_market_context(
                topic=topic, limit=limit, as_of=as_of
            )

        def validate(success: ProviderSuccess[USPredictionMarketContext]) -> None:
            if (
                success.meta.category is not DataCategory.PREDICTION_MARKET
                or success.meta.as_of != as_of
                or not isinstance(success.value, USPredictionMarketContext)
                or success.value.topic != topic
            ):
                raise DataContractError("prediction context does not match request")

        params = {"topic": topic, "limit": str(limit)}
        return await self._router.execute(
            market=Market.US,
            category=DataCategory.PREDICTION_MARKET,
            call=call,
            operation_name=OP_PREDICTION,
            request_fingerprint=build_us_fingerprint(OP_PREDICTION, "US", params, as_of),
            as_of=as_of,
            cache_codec=self._codec,
            result_validator=validate,
        )
