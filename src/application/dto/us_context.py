"""Closed Phase 1H MCP inputs and JSON-safe output DTOs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.common.enums import AssetType, Market, VendorId
from domain.common.values import parse_instrument_id
from domain.us_context.enums import (
    USNewsScope,
    USSentimentDirection,
    USSentimentLabelOrigin,
    USSentimentSource,
)
from domain.us_context.models import (
    USMacroContext,
    USMacroObservation,
    USMacroSeriesSnapshot,
    USNewsArticle,
    USNewsFeed,
    USPredictionMarket,
    USPredictionMarketContext,
    USSentimentSample,
    USSentimentSnapshot,
    USSentimentSourceSummary,
)

DEFAULT_MACRO_SERIES = (
    "FEDFUNDS",
    "CPIAUCSL",
    "PCEPILFE",
    "UNRATE",
    "DGS2",
    "DGS10",
    "T10Y2Y",
    "GDP",
    "VIXCLS",
    "NFCI",
    "DTWEXBGS",
    "DCOILWTICO",
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must be timezone-aware")
    return value


def _validate_us_equity_instrument_id(value: str) -> str:
    asset_type, market, _ = parse_instrument_id(value)
    if asset_type is not AssetType.EQUITY or market is not Market.US:
        raise ValueError("instrument_id must be a US equity")
    return value


class MarketGetLiveNewsInput(_DTO):
    instrument_id: str | None = None
    query: str | None = Field(default=None, min_length=1, max_length=256)
    start: date | None = None
    end: date | None = None
    as_of: datetime | None = None
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("instrument_id")
    @classmethod
    def instrument(cls, value: str | None) -> str | None:
        return None if value is None else _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def range_and_scope(self) -> Self:
        if self.instrument_id is None and self.query is None:
            raise ValueError("instrument_id or query is required")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must be <= end")
        return self


class USGetMacroContextInput(_DTO):
    series_ids: tuple[str, ...] = DEFAULT_MACRO_SERIES
    lookback_days: int = Field(default=365, ge=7, le=3650)
    as_of: datetime | None = None

    @field_validator("series_ids")
    @classmethod
    def series(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) > 20 or len(set(value)) != len(value):
            raise ValueError("series_ids must contain 1..20 unique values")
        for item in value:
            if not item or len(item) > 32 or not item.replace("_", "").isalnum():
                raise ValueError("invalid FRED series id")
        return value

    @field_validator("as_of")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class USGetSentimentSnapshotInput(_DTO):
    instrument_id: str
    start: date | None = None
    end: date | None = None
    as_of: datetime | None = None
    limit_per_source: int = Field(default=20, ge=1, le=50)

    @field_validator("instrument_id")
    @classmethod
    def instrument(cls, value: str) -> str:
        return _validate_us_equity_instrument_id(value)

    @field_validator("as_of")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def range(self) -> Self:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must be <= end")
        return self


class USGetPredictionMarketContextInput(_DTO):
    topic: str = Field(min_length=1, max_length=256)
    as_of: datetime | None = None
    limit: int = Field(default=6, ge=1, le=20)

    @field_validator("as_of")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class USNewsArticleDTO(_DTO):
    article_id: str
    instrument_id: str | None
    scope: USNewsScope
    title: str
    summary: str | None
    publisher: str | None
    url: str | None
    published_at: datetime
    vendor: str
    source_sentiment: DecimalWire | None
    relevance: DecimalWire | None
    dedupe_key: str


class USNewsFeedDTO(_DTO):
    instrument_id: str | None
    query: str | None
    as_of: datetime
    articles: tuple[USNewsArticleDTO, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: USNewsFeed) -> USNewsFeedDTO:
        return cls.model_validate(value)

    def to_domain(self) -> USNewsFeed:
        return USNewsFeed(
            instrument_id=self.instrument_id,
            query=self.query,
            as_of=self.as_of,
            articles=tuple(
                USNewsArticle(
                    article_id=item.article_id,
                    instrument_id=item.instrument_id,
                    scope=item.scope,
                    title=item.title,
                    summary=item.summary,
                    publisher=item.publisher,
                    url=item.url,
                    published_at=item.published_at,
                    vendor=VendorId(item.vendor),
                    source_sentiment=item.source_sentiment,
                    relevance=item.relevance,
                    dedupe_key=item.dedupe_key,
                )
                for item in self.articles
            ),
            degraded=self.degraded,
            warning_codes=self.warning_codes,
        )


class USMacroObservationDTO(_DTO):
    observation_date: date
    vintage_date: date
    value: DecimalWire


class USMacroSeriesSnapshotDTO(_DTO):
    series_id: str
    title: str
    unit: str
    frequency: str
    last_updated: datetime | None
    observations: tuple[USMacroObservationDTO, ...]
    latest_value: DecimalWire | None
    window_change: DecimalWire | None


class USMacroContextDTO(_DTO):
    as_of: datetime
    series: tuple[USMacroSeriesSnapshotDTO, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: USMacroContext) -> USMacroContextDTO:
        return cls.model_validate(value)

    def to_domain(self) -> USMacroContext:
        return USMacroContext(
            as_of=self.as_of,
            series=tuple(
                USMacroSeriesSnapshot(
                    series_id=item.series_id,
                    title=item.title,
                    unit=item.unit,
                    frequency=item.frequency,
                    last_updated=item.last_updated,
                    observations=tuple(
                        USMacroObservation(
                            observation_date=observation.observation_date,
                            vintage_date=observation.vintage_date,
                            value=observation.value,
                        )
                        for observation in item.observations
                    ),
                    latest_value=item.latest_value,
                    window_change=item.window_change,
                )
                for item in self.series
            ),
            degraded=self.degraded,
            warning_codes=self.warning_codes,
        )


class USSentimentSampleDTO(_DTO):
    instrument_id: str
    source: USSentimentSource
    published_at: datetime
    text: str
    direction: USSentimentDirection
    label_origin: USSentimentLabelOrigin
    score: DecimalWire
    likes: int | None
    comments: int | None
    url: str | None
    classifier_version: str | None


class USSentimentSourceSummaryDTO(_DTO):
    source: USSentimentSource
    label_origin: USSentimentLabelOrigin
    sample_count: int
    bullish_count: int
    bearish_count: int
    neutral_count: int
    weighted_score: DecimalWire | None
    confidence: DecimalWire


class USSentimentSnapshotDTO(_DTO):
    instrument_id: str
    as_of: datetime
    summaries: tuple[USSentimentSourceSummaryDTO, ...]
    samples: tuple[USSentimentSampleDTO, ...]
    disagreement: DecimalWire | None
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: USSentimentSnapshot) -> USSentimentSnapshotDTO:
        return cls.model_validate(value)

    def to_domain(self) -> USSentimentSnapshot:
        return USSentimentSnapshot(
            instrument_id=self.instrument_id,
            as_of=self.as_of,
            summaries=tuple(
                USSentimentSourceSummary(
                    source=item.source,
                    label_origin=item.label_origin,
                    sample_count=item.sample_count,
                    bullish_count=item.bullish_count,
                    bearish_count=item.bearish_count,
                    neutral_count=item.neutral_count,
                    weighted_score=item.weighted_score,
                    confidence=item.confidence,
                )
                for item in self.summaries
            ),
            samples=tuple(
                USSentimentSample(
                    instrument_id=item.instrument_id,
                    source=item.source,
                    published_at=item.published_at,
                    text=item.text,
                    direction=item.direction,
                    label_origin=item.label_origin,
                    score=item.score,
                    likes=item.likes,
                    comments=item.comments,
                    url=item.url,
                    classifier_version=item.classifier_version,
                )
                for item in self.samples
            ),
            disagreement=self.disagreement,
            degraded=self.degraded,
            warning_codes=self.warning_codes,
        )


class USPredictionMarketDTO(_DTO):
    market_id: str
    question: str
    outcomes: tuple[tuple[str, DecimalWire], ...]
    volume: DecimalWire | None
    resolution_at: datetime | None
    weekly_change: DecimalWire | None
    url: str | None


class USPredictionMarketContextDTO(_DTO):
    topic: str
    as_of: datetime
    markets: tuple[USPredictionMarketDTO, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: USPredictionMarketContext) -> USPredictionMarketContextDTO:
        return cls.model_validate(value)

    def to_domain(self) -> USPredictionMarketContext:
        return USPredictionMarketContext(
            topic=self.topic,
            as_of=self.as_of,
            markets=tuple(
                USPredictionMarket(
                    market_id=item.market_id,
                    question=item.question,
                    outcomes=item.outcomes,
                    volume=item.volume,
                    resolution_at=item.resolution_at,
                    weekly_change=item.weekly_change,
                    url=item.url,
                )
                for item in self.markets
            ),
            degraded=self.degraded,
            warning_codes=self.warning_codes,
        )
