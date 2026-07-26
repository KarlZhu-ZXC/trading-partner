"""Compact market-data and technical-analysis adapters."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Literal

from mcp.server.fastmcp import Image
from mcp.types import ImageContent, TextContent
from pydantic import ValidationError

from application.dto.technical import TechnicalAnalysisInput, TechnicalChartInput
from application.dto.us_market import (
    MarketGetBarsInput,
    MarketGetContextInput,
    MarketGetSnapshotInput,
    USGetSnapshotInput,
)
from bootstrap import ApplicationContainer
from interfaces.mcp.chart_artifacts import LocalChartArtifact
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_market_technical_adapters(
    container: ApplicationContainer,
    chart_persister: Callable[..., LocalChartArtifact],
) -> SimpleNamespace:
    """Build compact market and technical adapters."""

    # ---------------------------------------------------------- Phase 1F US market
    async def market_get_snapshot(
        instrument_id: str,
        as_of: datetime | None = None,
        operation: Literal["quote", "composite"] = "quote",
        lookback_sessions: int = 260,
    ) -> dict[str, Any]:
        """Return a quote (US/CME/OTC) or the full US composite snapshot."""
        if operation == "composite":
            return await us_get_snapshot(instrument_id, as_of, lookback_sessions)
        if operation != "quote":
            raise ValueError("operation must be quote or composite")
        try:
            inp = MarketGetSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                }
            )
            envelope = await container.market_tool_coordinator.get_market_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def market_get_bars(
        instrument_id: str,
        start: date,
        end: date,
        interval: str = "1d",
        adjustment: str | None = None,
        offer_side: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Return OHLCV for US/CME/OTC; futures and OTC force adjustment=none."""
        try:
            inp = MarketGetBarsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "start": start,
                    "end": end,
                    "interval": interval,
                    "adjustment": adjustment,
                    "offer_side": offer_side,
                    "as_of": as_of,
                }
            )
            envelope = await container.market_tool_coordinator.get_market_bars(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def market_get_context(
        operation: Literal["us_market", "futures_curve", "spot_future_basis"] = "us_market",
        as_of: datetime | None = None,
        product_key: str | None = None,
        price_basis: str = "settlement",
        trade_date: date | None = None,
        contract_limit: int = 6,
        left_instrument_id: str | None = None,
        right_instrument_id: str | None = None,
        max_observation_lag_seconds: int = 300,
    ) -> dict[str, Any]:
        """US market context (default), futures settlement curve, or spot/future basis."""
        try:
            payload: dict[str, Any] = {
                "operation": operation,
                "as_of": as_of,
                "price_basis": price_basis,
                "contract_limit": contract_limit,
                "max_observation_lag_seconds": max_observation_lag_seconds,
            }
            if product_key is not None:
                payload["product_key"] = product_key
            if trade_date is not None:
                payload["trade_date"] = trade_date
            if left_instrument_id is not None:
                payload["left_instrument_id"] = left_instrument_id
            if right_instrument_id is not None:
                payload["right_instrument_id"] = right_instrument_id
            inp = MarketGetContextInput.model_validate(payload)
            envelope = await container.market_tool_coordinator.get_market_context(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def technical_get_snapshot(
        instrument_id: str,
        as_of: datetime | None = None,
        lookback_sessions: int = 260,
        intervals: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return daily/weekly technical facts for supported cross-market instruments.

        Supported identities include A-share, US, CME, and Dukascopy OTC instruments.
        """
        try:
            inp = TechnicalAnalysisInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_sessions": lookback_sessions,
                    "intervals": tuple(intervals or ("1d", "1w")),
                }
            )
            envelope = await container.technical_tool_coordinator.get_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def technical_render_chart(
        instrument_id: str,
        as_of: datetime | None = None,
        interval: str = "1d",
        lookback_sessions: int = 160,
    ) -> list[TextContent | ImageContent]:
        """Return an auditable technical-analysis envelope followed by a PNG chart."""
        inp = TechnicalChartInput.model_validate(
            {
                "instrument_id": instrument_id,
                "as_of": as_of,
                "interval": interval,
                "lookback_sessions": lookback_sessions,
            }
        )
        artifact = await container.technical_tool_coordinator.render_chart(inp)
        content: list[TextContent | ImageContent] = [
            TextContent(
                type="text",
                text=json.dumps(
                    artifact.envelope.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        ]
        if artifact.png is not None:
            local = chart_persister(
                artifact.png,
                request_id=artifact.envelope.request_id,
            )
            content.append(
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "chart_artifact": {
                                "path": str(local.path),
                                "mime_type": local.mime_type,
                                "display_markdown": local.markdown,
                                "instruction": (
                                    "Embed display_markdown in the assistant response so the "
                                    "local chart is visible in Codex."
                                ),
                            }
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
            content.append(Image(data=artifact.png, format="png").to_image_content())
        return content

    async def us_get_snapshot(
        instrument_id: str,
        as_of: datetime | None = None,
        lookback_sessions: int = 260,
    ) -> dict[str, Any]:
        """Return a US composite snapshot (quote/bars/technical/context) Tool Envelope."""
        try:
            inp = USGetSnapshotInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "lookback_sessions": lookback_sessions,
                }
            )
            envelope = await container.us_tool_coordinator.get_us_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        market_get_snapshot=market_get_snapshot,
        market_get_bars=market_get_bars,
        market_get_context=market_get_context,
        technical_get_snapshot=technical_get_snapshot,
        technical_render_chart=technical_render_chart,
    )
