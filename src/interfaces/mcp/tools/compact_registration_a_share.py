"""Compact MCP registrations for A-share provider facts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compact import CapabilityRegistrar


def _register_a_share(registry: CapabilityRegistrar, adapters: SimpleNamespace) -> None:
    """Register the grouped A-share fact capability in stable operation order."""

    from .compact import READ_PROVIDER, _all_fields, _register_flat_dispatch_tool, _spec

    facts = "a_share_get_facts"
    variants = (
        _spec(
            "snapshot",
            adapters.a_share_get_facts,
            ("instrument_id", "as_of", "detail"),
            adapter_operation="snapshot",
        ),
        _spec(
            "market_structure",
            adapters.a_share_get_facts,
            (
                "scope",
                "instrument_id",
                "trade_date",
                "start",
                "end",
                "interval",
                "adjustment",
                "include_bars",
                "include_order_book",
                "include_ticks",
                "include_industries",
                "include_market_board",
                "industry_limit",
                "tick_limit",
                "as_of",
            ),
            adapter_operation="market_structure",
        ),
        _spec(
            "capital",
            adapters.a_share_get_facts,
            ("instrument_id", "metrics", "start", "end", "as_of"),
            adapter_operation="capital",
        ),
        _spec(
            "limit_up",
            adapters.a_share_get_facts,
            ("trade_date", "pools", "as_of"),
            adapter_operation="limit_up",
        ),
        _spec(
            "sentiment",
            adapters.a_share_get_facts,
            ("instrument_id", "sentiment_sources", "trade_date", "as_of"),
            adapter_operation="sentiment",
        ),
        _spec(
            "etf_option",
            adapters.a_share_get_facts,
            ("instrument_id", "expiry", "strike_center", "strike_count_each_side", "as_of"),
            adapter_operation="etf_option",
        ),
        _spec(
            "financials",
            adapters.a_share_get_facts,
            ("instrument_id", "statement_types", "periods", "metric_codes", "as_of"),
            adapter_operation="financials",
        ),
        _spec(
            "industry_cycle",
            adapters.a_share_get_facts,
            ("cycle", "lookback_months", "view", "metric_codes", "offset", "limit", "as_of"),
            adapter_operation="industry_cycle",
        ),
        _spec(
            "company_operating_metrics",
            adapters.a_share_get_facts,
            ("instrument_id", "lookback_months", "document_limit", "metric_codes", "as_of"),
            adapter_operation="company_operating_metrics",
        ),
        _spec(
            "research_reports",
            adapters.research_search_reports,
            _all_fields(adapters.research_search_reports),
        ),
    )
    _register_flat_dispatch_tool(
        registry,
        name=facts,
        description=(
            "Read one closed A-share fact family or search current provider research reports."
        ),
        variants=variants,
        policy=READ_PROVIDER,
    )
