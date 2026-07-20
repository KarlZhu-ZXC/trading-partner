"""Explicit cache codecs for Phase 1H US context provider values."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from pydantic import ValidationError

from application.dto.us_context import (
    USMacroContextDTO,
    USNewsFeedDTO,
    USPredictionMarketContextDTO,
    USSentimentSampleDTO,
)
from domain.common.enums import DataCategory
from domain.common.errors import DataContractError
from domain.us_context.models import (
    USMacroContext,
    USNewsFeed,
    USPredictionMarketContext,
    USSentimentSample,
)
from domain.us_market.models import USBreadthSnapshot, USSectorRotation
from infrastructure.providers.us.codecs import USProviderCacheCodec

CODEC_US_NEWS_FEED: Final[str] = "us.news_feed.v1"
CODEC_US_MACRO_CONTEXT: Final[str] = "us.macro_context.v1"
CODEC_US_SENTIMENT_SAMPLES: Final[str] = "us.sentiment_samples.v1"
CODEC_US_PREDICTION_MARKET_CONTEXT: Final[str] = "us.prediction_market_context.v1"
CODEC_US_MARKET_BREADTH: Final[str] = "us.market_breadth.v1"


def _contract(message: str, *, rule: str) -> DataContractError:
    return DataContractError(message, details={"field": "value", "rule": rule})


def _reject_float_tree(value: object) -> None:
    if isinstance(value, float):
        raise _contract("cache numeric values must use decimal strings", rule="no_float")
    if isinstance(value, list):
        for item in value:
            _reject_float_tree(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_float_tree(item)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _contract("cache value must be an object", rule="type")
    _reject_float_tree(value)
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _contract("cache value must be an array", rule="type")
    _reject_float_tree(value)
    return value


def _encode_news(value: USNewsFeed) -> dict[str, object]:
    if not isinstance(value, USNewsFeed):
        raise _contract("news cache value must be USNewsFeed", rule="type")
    return USNewsFeedDTO.from_domain(value).model_dump(mode="json")


def _decode_news(value: object) -> USNewsFeed:
    try:
        return USNewsFeedDTO.model_validate(_object(value)).to_domain()
    except DataContractError:
        raise
    except (ValidationError, ValueError):
        raise _contract("news cache value failed schema validation", rule="value_schema") from None


def _encode_macro(value: USMacroContext) -> dict[str, object]:
    if not isinstance(value, USMacroContext):
        raise _contract("macro cache value must be USMacroContext", rule="type")
    return USMacroContextDTO.from_domain(value).model_dump(mode="json")


def _decode_macro(value: object) -> USMacroContext:
    try:
        return USMacroContextDTO.model_validate(_object(value)).to_domain()
    except DataContractError:
        raise
    except (ValidationError, ValueError):
        raise _contract("macro cache value failed schema validation", rule="value_schema") from None


def _encode_sentiment(value: tuple[USSentimentSample, ...]) -> list[object]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, USSentimentSample) for item in value
    ):
        raise _contract("sentiment cache value must be a sample tuple", rule="type")
    return [USSentimentSampleDTO.model_validate(item).model_dump(mode="json") for item in value]


def _decode_sentiment(value: object) -> tuple[USSentimentSample, ...]:
    try:
        return tuple(
            USSentimentSample(
                instrument_id=dto.instrument_id,
                source=dto.source,
                published_at=dto.published_at,
                text=dto.text,
                direction=dto.direction,
                label_origin=dto.label_origin,
                score=dto.score,
                likes=dto.likes,
                comments=dto.comments,
                url=dto.url,
                classifier_version=dto.classifier_version,
            )
            for dto in (USSentimentSampleDTO.model_validate(item) for item in _list(value))
        )
    except DataContractError:
        raise
    except (ValidationError, ValueError):
        raise _contract(
            "sentiment cache value failed schema validation", rule="value_schema"
        ) from None


def _encode_prediction(value: USPredictionMarketContext) -> dict[str, object]:
    if not isinstance(value, USPredictionMarketContext):
        raise _contract("prediction cache value must be context", rule="type")
    return USPredictionMarketContextDTO.from_domain(value).model_dump(mode="json")


def _decode_prediction(value: object) -> USPredictionMarketContext:
    try:
        return USPredictionMarketContextDTO.model_validate(_object(value)).to_domain()
    except DataContractError:
        raise
    except (ValidationError, ValueError):
        raise _contract(
            "prediction cache value failed schema validation", rule="value_schema"
        ) from None


def _decimal_wire(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _encode_breadth(value: USBreadthSnapshot) -> dict[str, object]:
    if not isinstance(value, USBreadthSnapshot):
        raise _contract("breadth cache value must be USBreadthSnapshot", rule="type")
    return {
        "observed_at": value.observed_at.isoformat(),
        "advancing_count": value.advancing_count,
        "declining_count": value.declining_count,
        "unchanged_count": value.unchanged_count,
        "basis": value.basis,
        "universe": value.universe,
        "sector_rotation": [
            {
                "sector": row.sector,
                "index_symbol": row.index_symbol,
                "return_1d": _decimal_wire(row.return_1d),
                "return_5d": _decimal_wire(row.return_5d),
                "return_20d": _decimal_wire(row.return_20d),
                "relative_spy_20d": _decimal_wire(row.relative_spy_20d),
            }
            for row in value.sector_rotation
        ],
    }


def _decode_optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _contract("breadth decimal must be a string", rule="decimal_wire")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise _contract("breadth decimal is invalid", rule="decimal_wire") from None
    if not result.is_finite():
        raise _contract("breadth decimal must be finite", rule="decimal_wire")
    return result


def _decode_breadth(value: object) -> USBreadthSnapshot:
    try:
        obj = _object(value)
        rows_raw = _list(obj["sector_rotation"])
        rows: list[USSectorRotation] = []
        for raw in rows_raw:
            row = _object(raw)
            rows.append(
                USSectorRotation(
                    sector=row["sector"],  # type: ignore[arg-type]
                    index_symbol=row["index_symbol"],  # type: ignore[arg-type]
                    return_1d=_decode_optional_decimal(row["return_1d"]),
                    return_5d=_decode_optional_decimal(row["return_5d"]),
                    return_20d=_decode_optional_decimal(row["return_20d"]),
                    relative_spy_20d=_decode_optional_decimal(row["relative_spy_20d"]),
                )
            )
        return USBreadthSnapshot(
            observed_at=datetime.fromisoformat(obj["observed_at"]),  # type: ignore[arg-type]
            advancing_count=obj["advancing_count"],  # type: ignore[arg-type]
            declining_count=obj["declining_count"],  # type: ignore[arg-type]
            unchanged_count=obj["unchanged_count"],  # type: ignore[arg-type]
            basis=obj["basis"],  # type: ignore[arg-type]
            universe=obj["universe"],  # type: ignore[arg-type]
            sector_rotation=tuple(rows),
        )
    except DataContractError:
        raise
    except (KeyError, TypeError, ValueError):
        raise _contract(
            "breadth cache value failed schema validation", rule="value_schema"
        ) from None


def us_news_feed_codec() -> USProviderCacheCodec[USNewsFeed]:
    return USProviderCacheCodec(
        CODEC_US_NEWS_FEED,
        _encode_news,
        _decode_news,
        expected_category=DataCategory.NEWS,
    )


def us_macro_context_codec() -> USProviderCacheCodec[USMacroContext]:
    return USProviderCacheCodec(
        CODEC_US_MACRO_CONTEXT,
        _encode_macro,
        _decode_macro,
        expected_category=DataCategory.MACRO,
    )


def us_sentiment_samples_codec() -> USProviderCacheCodec[tuple[USSentimentSample, ...]]:
    return USProviderCacheCodec(
        CODEC_US_SENTIMENT_SAMPLES,
        _encode_sentiment,
        _decode_sentiment,
        expected_category=DataCategory.SENTIMENT,
    )


def us_prediction_market_context_codec() -> USProviderCacheCodec[USPredictionMarketContext]:
    return USProviderCacheCodec(
        CODEC_US_PREDICTION_MARKET_CONTEXT,
        _encode_prediction,
        _decode_prediction,
        expected_category=DataCategory.PREDICTION_MARKET,
    )


def us_market_breadth_codec() -> USProviderCacheCodec[USBreadthSnapshot]:
    return USProviderCacheCodec(
        CODEC_US_MARKET_BREADTH,
        _encode_breadth,
        _decode_breadth,
        expected_category=DataCategory.MARKET_BREADTH,
    )
