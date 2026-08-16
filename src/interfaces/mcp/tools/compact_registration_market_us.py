"""Compact MCP registrations for market, technical, and US context facts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compact import CapabilityRegistrar


def _register_market_and_us(
    registry: CapabilityRegistrar,
    market: SimpleNamespace,
    us_research: SimpleNamespace,
    us_context: SimpleNamespace,
) -> None:
    """Register market and US grouped capabilities in stable tool order."""

    from .compact import (
        LOCAL_ARTIFACT,
        READ_PROVIDER,
        _all_fields,
        _copy_handler,
        _register_dispatch_tool,
        _register_flat_dispatch_tool,
        _spec,
    )

    _register_flat_dispatch_tool(
        registry,
        name="market_data_get",
        description=(
            "Read cross-market quote(s)/composite, bars, US market context, "
            "futures curve, or basis. Quote previous_close follows the returned "
            "quote session; previous_close_basis distinguishes a completed regular "
            "session from a completed futures daily bar and never means calendar yesterday."
        ),
        variants=(
            _spec(
                "quote",
                market.market_get_snapshot,
                ("instrument_id", "as_of"),
                adapter_operation="quote",
            ),
            _spec(
                "quotes",
                market.market_get_quotes,
                _all_fields(market.market_get_quotes),
            ),
            _spec(
                "composite",
                market.market_get_snapshot,
                ("instrument_id", "as_of", "lookback_sessions"),
                adapter_operation="composite",
            ),
            _spec("bars", market.market_get_bars, _all_fields(market.market_get_bars)),
            _spec(
                "us_market",
                market.market_get_context,
                ("as_of",),
                adapter_operation="us_market",
            ),
            _spec(
                "futures_curve",
                market.market_get_context,
                ("as_of", "product_key", "price_basis", "trade_date", "contract_limit"),
                adapter_operation="futures_curve",
            ),
            _spec(
                "spot_future_basis",
                market.market_get_context,
                (
                    "as_of",
                    "left_instrument_id",
                    "right_instrument_id",
                    "max_observation_lag_seconds",
                ),
                adapter_operation="spot_future_basis",
            ),
        ),
        policy=READ_PROVIDER,
    )
    _copy_handler(registry, adapter=market.technical_get_snapshot, policy=READ_PROVIDER)
    _copy_handler(
        registry,
        adapter=market.technical_render_chart,
        policy=LOCAL_ARTIFACT,
    )
    _register_flat_dispatch_tool(
        registry,
        name="us_company_get",
        description=("Read US equity company facts or dated US equity/ETF live news."),
        variants=(
            _spec(
                "fundamentals_snapshot",
                us_research.us_get_fundamentals,
                ("instrument_id", "as_of"),
                adapter_operation="snapshot",
            ),
            _spec(
                "fundamental_statements",
                us_research.us_get_fundamentals,
                ("instrument_id", "as_of", "frequency", "limit", "view"),
                adapter_operation="statements",
            ),
            _spec(
                "filings",
                us_research.us_get_company_research,
                ("instrument_id", "forms", "start", "end", "as_of", "include_sections", "limit"),
                adapter_operation="filings",
            ),
            _spec(
                "insider_activity",
                us_research.us_get_company_research,
                ("instrument_id", "start", "end", "as_of", "limit"),
                adapter_operation="insider_activity",
            ),
            _spec(
                "company_updates",
                us_research.us_get_company_research,
                ("instrument_id", "since", "as_of", "limit"),
                adapter_operation="company_updates",
            ),
            _spec(
                "events",
                us_research.us_get_company_research,
                ("instrument_id", "event_types", "start", "end", "as_of", "limit"),
                adapter_operation="events",
            ),
            _spec(
                "live_news",
                us_context.market_get_live_news,
                _all_fields(us_context.market_get_live_news),
            ),
        ),
        policy=READ_PROVIDER,
    )
    _register_dispatch_tool(
        registry,
        name="us_context_get",
        description=(
            "Read vintage-safe macro, source-separated sentiment, or current "
            "prediction-market context."
        ),
        variants=(
            _spec(
                "macro",
                us_context.us_get_macro_context,
                _all_fields(us_context.us_get_macro_context),
            ),
            _spec(
                "sentiment",
                us_context.us_get_sentiment_snapshot,
                _all_fields(us_context.us_get_sentiment_snapshot),
            ),
            _spec(
                "prediction_market",
                us_context.us_get_prediction_market_context,
                _all_fields(us_context.us_get_prediction_market_context),
            ),
        ),
        policy=READ_PROVIDER,
    )
