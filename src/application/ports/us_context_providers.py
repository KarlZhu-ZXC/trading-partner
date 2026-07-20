"""Phase 1H runtime-checkable provider protocols."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.instruments.models import Instrument
from domain.us_context.models import (
    USMacroContext,
    USNewsFeed,
    USPredictionMarketContext,
    USSentimentSample,
)


@runtime_checkable
class USNewsProvider(CategoryProvider, Protocol):
    async def get_news(
        self,
        instrument: Instrument | None,
        *,
        query: str | None,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[USNewsFeed]: ...


@runtime_checkable
class USMacroProvider(CategoryProvider, Protocol):
    async def get_macro_context(
        self,
        *,
        series_ids: tuple[str, ...],
        lookback_days: int,
        as_of: datetime,
    ) -> ProviderSuccess[USMacroContext]: ...


@runtime_checkable
class USSentimentProvider(CategoryProvider, Protocol):
    async def get_sentiment_samples(
        self,
        instrument: Instrument,
        *,
        start: date | None,
        end: date | None,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[tuple[USSentimentSample, ...]]: ...


@runtime_checkable
class USPredictionMarketProvider(CategoryProvider, Protocol):
    async def get_prediction_market_context(
        self,
        *,
        topic: str,
        limit: int,
        as_of: datetime,
    ) -> ProviderSuccess[USPredictionMarketContext]: ...


US_CONTEXT_RUNTIME_PROTOCOLS: tuple[type, ...] = (
    USNewsProvider,
    USMacroProvider,
    USSentimentProvider,
    USPredictionMarketProvider,
)
