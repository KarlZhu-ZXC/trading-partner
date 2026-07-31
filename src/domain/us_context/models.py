"""Immutable news, macro, sentiment, and prediction-market facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from domain.common.enums import AssetType, Market, VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.us_context.enums import (
    USNewsScope,
    USSentimentDirection,
    USSentimentLabelOrigin,
    USSentimentSource,
)


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(
            f"{field} must be a bounded non-blank string", details={"field": field}
        )
    return value


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    return None if value is None else _text(value, field, maximum)


def _decimal(
    value: object,
    field: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal | None:
    if value is None:
        return None
    if type(value) is not Decimal or not value.is_finite():
        raise DataContractError(f"{field} must be finite Decimal", details={"field": field})
    if minimum is not None and value < minimum:
        raise DataContractError(f"{field} is below minimum", details={"field": field})
    if maximum is not None and value > maximum:
        raise DataContractError(f"{field} exceeds maximum", details={"field": field})
    return value


def _nonnegative_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise DataContractError(f"{field} must be a nonnegative int", details={"field": field})
    return value


def _instrument(value: str | None) -> None:
    if value is None:
        return
    asset, market, _ = parse_instrument_id(value)
    if asset not in {AssetType.EQUITY, AssetType.ETF} or market is not Market.US:
        raise DataContractError("instrument_id must be a US equity or ETF")


def _warnings(value: tuple[str, ...]) -> None:
    if not isinstance(value, tuple) or len(set(value)) != len(value):
        raise DataContractError("warning_codes must be a unique tuple")
    for code in value:
        _text(code, "warning_code", 128)


@dataclass(frozen=True, slots=True)
class USNewsArticle:
    article_id: str
    instrument_id: str | None
    scope: USNewsScope
    title: str
    summary: str | None
    publisher: str | None
    url: str | None
    published_at: datetime
    vendor: VendorId
    source_sentiment: Decimal | None
    relevance: Decimal | None
    dedupe_key: str

    def __post_init__(self) -> None:
        _text(self.article_id, "article_id", 256)
        _instrument(self.instrument_id)
        if not isinstance(self.scope, USNewsScope) or not isinstance(self.vendor, VendorId):
            raise DataContractError("scope/vendor enum is invalid")
        _text(self.title, "title", 500)
        _optional_text(self.summary, "summary", 4_000)
        _optional_text(self.publisher, "publisher", 256)
        _optional_text(self.url, "url", 2_000)
        require_aware_datetime(self.published_at, field_name="published_at")
        _decimal(
            self.source_sentiment, "source_sentiment", minimum=Decimal("-1"), maximum=Decimal("1")
        )
        _decimal(self.relevance, "relevance", minimum=Decimal(0), maximum=Decimal(1))
        _text(self.dedupe_key, "dedupe_key", 256)


@dataclass(frozen=True, slots=True)
class USNewsFeed:
    instrument_id: str | None
    query: str | None
    as_of: datetime
    articles: tuple[USNewsArticle, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _instrument(self.instrument_id)
        _optional_text(self.query, "query", 256)
        require_aware_datetime(self.as_of, field_name="as_of")
        if type(self.degraded) is not bool or not isinstance(self.articles, tuple):
            raise DataContractError("news feed tuple/degraded contract is invalid")
        _warnings(self.warning_codes)
        seen: set[str] = set()
        for article in self.articles:
            if not isinstance(article, USNewsArticle) or article.published_at > self.as_of:
                raise DataContractError("news article violates feed cutoff/type")
            if article.dedupe_key in seen:
                raise DataContractError("news dedupe_key must be unique")
            seen.add(article.dedupe_key)


@dataclass(frozen=True, slots=True)
class USMacroObservation:
    observation_date: date
    vintage_date: date
    value: Decimal

    def __post_init__(self) -> None:
        if type(self.observation_date) is not date or type(self.vintage_date) is not date:
            raise DataContractError("macro dates must be exact dates")
        _decimal(self.value, "value")


@dataclass(frozen=True, slots=True)
class USMacroSeriesSnapshot:
    series_id: str
    title: str
    unit: str
    frequency: str
    last_updated: datetime | None
    observations: tuple[USMacroObservation, ...]
    latest_value: Decimal | None
    window_change: Decimal | None

    def __post_init__(self) -> None:
        _text(self.series_id, "series_id", 32)
        _text(self.title, "title", 256)
        _text(self.unit, "unit", 128)
        _text(self.frequency, "frequency", 128)
        if self.last_updated is not None:
            require_aware_datetime(self.last_updated, field_name="last_updated")
        if not isinstance(self.observations, tuple):
            raise DataContractError("observations must be tuple")
        for observation in self.observations:
            if not isinstance(observation, USMacroObservation):
                raise DataContractError("observations must contain macro observations")
        if any(
            left.observation_date > right.observation_date
            for left, right in zip(self.observations, self.observations[1:], strict=False)
        ):
            raise DataContractError("observations must be ordered by observation_date")
        _decimal(self.latest_value, "latest_value")
        _decimal(self.window_change, "window_change")
        if self.observations and self.latest_value != self.observations[-1].value:
            raise DataContractError("latest_value must match last observation")


@dataclass(frozen=True, slots=True)
class USMacroContext:
    as_of: datetime
    series: tuple[USMacroSeriesSnapshot, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        require_aware_datetime(self.as_of, field_name="as_of")
        if type(self.degraded) is not bool or not isinstance(self.series, tuple):
            raise DataContractError("macro context contract is invalid")
        _warnings(self.warning_codes)
        if any(not isinstance(item, USMacroSeriesSnapshot) for item in self.series):
            raise DataContractError("series must contain macro snapshots")
        if len({item.series_id for item in self.series}) != len(self.series):
            raise DataContractError("macro series_id must be unique")


@dataclass(frozen=True, slots=True)
class USSentimentSample:
    instrument_id: str
    source: USSentimentSource
    published_at: datetime
    text: str
    direction: USSentimentDirection
    label_origin: USSentimentLabelOrigin
    score: Decimal
    likes: int | None
    comments: int | None
    url: str | None
    classifier_version: str | None

    def __post_init__(self) -> None:
        _instrument(self.instrument_id)
        if (
            not isinstance(self.source, USSentimentSource)
            or not isinstance(self.direction, USSentimentDirection)
            or not isinstance(self.label_origin, USSentimentLabelOrigin)
        ):
            raise DataContractError("sentiment enum is invalid")
        require_aware_datetime(self.published_at, field_name="published_at")
        _text(self.text, "text", 1_000)
        _decimal(self.score, "score", minimum=Decimal("-1"), maximum=Decimal("1"))
        _nonnegative_int(self.likes, "likes")
        _nonnegative_int(self.comments, "comments")
        _optional_text(self.url, "url", 2_000)
        _optional_text(self.classifier_version, "classifier_version", 64)
        if (
            self.label_origin is USSentimentLabelOrigin.USER_LABEL
            and self.classifier_version is not None
        ):
            raise DataContractError("user labels must not claim classifier_version")
        if (
            self.label_origin is USSentimentLabelOrigin.DETERMINISTIC_INFERENCE
            and self.classifier_version is None
        ):
            raise DataContractError("inferred labels require classifier_version")


@dataclass(frozen=True, slots=True)
class USSentimentSourceSummary:
    source: USSentimentSource
    label_origin: USSentimentLabelOrigin
    sample_count: int
    bullish_count: int
    bearish_count: int
    neutral_count: int
    weighted_score: Decimal | None
    confidence: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.source, USSentimentSource) or not isinstance(
            self.label_origin, USSentimentLabelOrigin
        ):
            raise DataContractError("sentiment summary enum is invalid")
        for field, value in (
            ("sample_count", self.sample_count),
            ("bullish_count", self.bullish_count),
            ("bearish_count", self.bearish_count),
            ("neutral_count", self.neutral_count),
        ):
            _nonnegative_int(value, field)
        if self.bullish_count + self.bearish_count + self.neutral_count != self.sample_count:
            raise DataContractError("sentiment counts must sum to sample_count")
        _decimal(self.weighted_score, "weighted_score", minimum=Decimal("-1"), maximum=Decimal("1"))
        _decimal(self.confidence, "confidence", minimum=Decimal(0), maximum=Decimal(1))


@dataclass(frozen=True, slots=True)
class USSentimentSnapshot:
    instrument_id: str
    as_of: datetime
    summaries: tuple[USSentimentSourceSummary, ...]
    samples: tuple[USSentimentSample, ...]
    disagreement: Decimal | None
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _instrument(self.instrument_id)
        require_aware_datetime(self.as_of, field_name="as_of")
        if not isinstance(self.summaries, tuple) or not isinstance(self.samples, tuple):
            raise DataContractError("sentiment collections must be tuples")
        _decimal(self.disagreement, "disagreement", minimum=Decimal(0), maximum=Decimal(2))
        if type(self.degraded) is not bool:
            raise DataContractError("degraded must be bool")
        _warnings(self.warning_codes)
        if any(sample.published_at > self.as_of for sample in self.samples):
            raise DataContractError("sentiment sample is after as_of")


@dataclass(frozen=True, slots=True)
class USPredictionMarket:
    market_id: str
    question: str
    outcomes: tuple[tuple[str, Decimal], ...]
    volume: Decimal | None
    resolution_at: datetime | None
    weekly_change: Decimal | None
    url: str | None

    def __post_init__(self) -> None:
        _text(self.market_id, "market_id", 256)
        _text(self.question, "question", 500)
        if not isinstance(self.outcomes, tuple) or not self.outcomes:
            raise DataContractError("outcomes must be non-empty tuple")
        labels: set[str] = set()
        for label, probability in self.outcomes:
            _text(label, "outcome", 128)
            if label in labels:
                raise DataContractError("outcome labels must be unique")
            labels.add(label)
            _decimal(probability, "probability", minimum=Decimal(0), maximum=Decimal(1))
        _decimal(self.volume, "volume", minimum=Decimal(0))
        if self.resolution_at is not None:
            require_aware_datetime(self.resolution_at, field_name="resolution_at")
        _decimal(self.weekly_change, "weekly_change", minimum=Decimal("-1"), maximum=Decimal("1"))
        _optional_text(self.url, "url", 2_000)


@dataclass(frozen=True, slots=True)
class USPredictionMarketContext:
    topic: str
    as_of: datetime
    markets: tuple[USPredictionMarket, ...]
    degraded: bool
    warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.topic, "topic", 256)
        require_aware_datetime(self.as_of, field_name="as_of")
        if not isinstance(self.markets, tuple) or type(self.degraded) is not bool:
            raise DataContractError("prediction context contract is invalid")
        _warnings(self.warning_codes)
