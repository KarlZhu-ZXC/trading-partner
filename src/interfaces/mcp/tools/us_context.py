"""Compact US-context operation adapters."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from application.dto.us_context import (
    DEFAULT_MACRO_SERIES,
    MarketGetLiveNewsInput,
    USGetMacroContextInput,
    USGetPredictionMarketContextInput,
    USGetSentimentSnapshotInput,
)
from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_us_context_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact US-context operation adapters."""

    # --------------------------------------------------------- Phase 1H US context
    async def market_get_live_news(
        instrument_id: str | None = None,
        query: str | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return dated US company or global market news."""
        try:
            inp = MarketGetLiveNewsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "query": query,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.services.us_context.get_live_news(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def us_get_macro_context(
        series_ids: tuple[str, ...] = DEFAULT_MACRO_SERIES,
        lookback_days: int = 365,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return FRED macro series with historical-vintage cutoffs."""
        try:
            inp = USGetMacroContextInput.model_validate(
                {
                    "series_ids": series_ids,
                    "lookback_days": lookback_days,
                    "as_of": as_of,
                }
            )
            envelope = await container.services.us_context.get_macro_context(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def us_get_sentiment_snapshot(
        instrument_id: str,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit_per_source: int = 20,
    ) -> dict[str, Any]:
        """Return explicit and inferred US discussion sentiment by source."""
        try:
            inp = USGetSentimentSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit_per_source": limit_per_source,
                }
            )
            envelope = await container.services.us_context.get_sentiment_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def us_get_prediction_market_context(
        topic: str,
        as_of: datetime | None = None,
        limit: int = 6,
    ) -> dict[str, Any]:
        """Return current open Polymarket probabilities for a topic."""
        try:
            inp = USGetPredictionMarketContextInput.model_validate(
                {"topic": topic, "as_of": as_of, "limit": limit}
            )
            envelope = await container.services.us_context.get_prediction_market_context(
                inp
            )
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        market_get_live_news=market_get_live_news,
        us_get_macro_context=us_get_macro_context,
        us_get_sentiment_snapshot=us_get_sentiment_snapshot,
        us_get_prediction_market_context=us_get_prediction_market_context,
    )
