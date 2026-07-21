"""A-share tool policies, capital metric ownership, and asset support (Phase 1E §§18–19).

Constants only in E1 — no service orchestration.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from application.dto.provider_routing import ToolDataPolicy
from domain.a_share.enums import CapitalMetricType
from domain.common.enums import AssetType, DataCategory, VendorId

# ---------------------------------------------------------------------------
# §18.1 ToolDataPolicy constants
# ---------------------------------------------------------------------------

SNAPSHOT_SUMMARY_POLICY = ToolDataPolicy(
    tool_name="a_share_get_snapshot.summary",
    required_categories=(DataCategory.MARKET_QUOTE,),
    optional_categories=(
        DataCategory.FUNDAMENTALS,
        DataCategory.ANNOUNCEMENTS,
        DataCategory.NEWS,
    ),
    category_chain_overrides={},
)

SNAPSHOT_FULL_POLICY = ToolDataPolicy(
    tool_name="a_share_get_snapshot.full",
    required_categories=(DataCategory.MARKET_QUOTE,),
    optional_categories=(
        DataCategory.FUNDAMENTALS,
        DataCategory.FINANCIAL_STATEMENTS,
        DataCategory.ANNOUNCEMENTS,
        DataCategory.NEWS,
        DataCategory.CORPORATE_ACTIONS,
    ),
    category_chain_overrides={},
)

# Optional F10 (and any other optional FUNDAMENTALS use) must not elevate CORE
# merely because it shares DataCategory.FUNDAMENTALS with required fundamentals.
SNAPSHOT_OPTIONAL_FUNDAMENTALS_POLICY = ToolDataPolicy(
    tool_name="a_share_get_snapshot.optional_fundamentals",
    required_categories=(),
    optional_categories=(DataCategory.FUNDAMENTALS,),
    category_chain_overrides={},
)

STRUCTURE_INSTRUMENT_BARS_POLICY = ToolDataPolicy(
    tool_name="a_share_get_market_structure.instrument.bars",
    required_categories=(DataCategory.MARKET_OHLCV,),
    optional_categories=(),
    category_chain_overrides={},
)

STRUCTURE_INSTRUMENT_BOOK_TICKS_POLICY = ToolDataPolicy(
    tool_name="a_share_get_market_structure.instrument.book_ticks",
    required_categories=(DataCategory.MARKET_STRUCTURE,),
    optional_categories=(),
    category_chain_overrides={},
)

STRUCTURE_MARKET_INDUSTRY_POLICY = ToolDataPolicy(
    tool_name="a_share_get_market_structure.market_industry",
    required_categories=(DataCategory.MARKET_STRUCTURE,),
    optional_categories=(DataCategory.LIMIT_UP,),
    category_chain_overrides={},
)

# Default capital summary: daily_flow required; other summary metrics optional.
# §18.1 / §8: default summary set is daily_flow, margin, shareholder_count,
# chip_distribution, unlock, dividend.
CAPITAL_DEFAULT_SUMMARY_POLICY = ToolDataPolicy(
    tool_name="a_share_get_capital_snapshot.summary",
    required_categories=(DataCategory.CAPITAL,),
    optional_categories=(DataCategory.CORPORATE_ACTIONS,),
    category_chain_overrides={},
)

LIMIT_UP_POLICY = ToolDataPolicy(
    tool_name="a_share_get_limit_up_context",
    required_categories=(DataCategory.LIMIT_UP,),
    optional_categories=(DataCategory.SENTIMENT,),
    category_chain_overrides={},
)

SENTIMENT_POLICY = ToolDataPolicy(
    tool_name="a_share_get_sentiment_snapshot",
    required_categories=(),
    optional_categories=(
        DataCategory.SENTIMENT,
        DataCategory.INTERACTIVE_QA,
        DataCategory.NEWS,
    ),
    category_chain_overrides={},
)

# OPTIONS elevated to CORE for the options tool (§18.1).
OPTIONS_POLICY = ToolDataPolicy(
    tool_name="a_share_get_etf_option_snapshot",
    required_categories=(DataCategory.OPTIONS,),
    optional_categories=(),
    category_chain_overrides={},
)

REPORTS_POLICY = ToolDataPolicy(
    tool_name="research_search_reports",
    required_categories=(),
    optional_categories=(DataCategory.RESEARCH_REPORTS,),
    category_chain_overrides={},
)

# ---------------------------------------------------------------------------
# §18.5 Capital metric routing ownership
# ---------------------------------------------------------------------------

# Category for each metric. unlock/dividend elevate CORPORATE_ACTIONS, not CAPITAL.
CAPITAL_METRIC_CATEGORY: Mapping[CapitalMetricType, DataCategory] = MappingProxyType(
    {
        CapitalMetricType.INTRADAY_FLOW: DataCategory.CAPITAL,
        CapitalMetricType.DAILY_FLOW: DataCategory.CAPITAL,
        CapitalMetricType.NORTHBOUND: DataCategory.CAPITAL,
        CapitalMetricType.DRAGON_TIGER: DataCategory.CAPITAL,
        CapitalMetricType.MARGIN: DataCategory.CAPITAL,
        CapitalMetricType.BLOCK_TRADE: DataCategory.CAPITAL,
        CapitalMetricType.SHAREHOLDER_COUNT: DataCategory.CAPITAL,
        CapitalMetricType.CHIP_DISTRIBUTION: DataCategory.CAPITAL,
        CapitalMetricType.UNLOCK: DataCategory.CORPORATE_ACTIONS,
        CapitalMetricType.DIVIDEND: DataCategory.CORPORATE_ACTIONS,
    }
)

# Metric-specific chain overrides (versioned constants; not caller-supplied).
CAPITAL_METRIC_CHAIN_OVERRIDES: Mapping[CapitalMetricType, tuple[VendorId, ...]] = (
    MappingProxyType(
        {
            CapitalMetricType.NORTHBOUND: (VendorId.HKEX, VendorId.EASTMONEY),
            CapitalMetricType.DRAGON_TIGER: (
                VendorId.EASTMONEY,
                VendorId.SSE,
                VendorId.SZSE,
            ),
            CapitalMetricType.DAILY_FLOW: (VendorId.EASTMONEY, VendorId.SINA),
            CapitalMetricType.INTRADAY_FLOW: (VendorId.EASTMONEY,),
            CapitalMetricType.MARGIN: (VendorId.EASTMONEY,),
            CapitalMetricType.BLOCK_TRADE: (VendorId.EASTMONEY,),
            CapitalMetricType.SHAREHOLDER_COUNT: (VendorId.EASTMONEY,),
            CapitalMetricType.CHIP_DISTRIBUTION: (VendorId.EASTMONEY,),
            CapitalMetricType.UNLOCK: (VendorId.EASTMONEY,),
            CapitalMetricType.DIVIDEND: (VendorId.EASTMONEY,),
        }
    )
)

# Default summary metrics when caller passes empty metrics=() (§8).
CAPITAL_DEFAULT_SUMMARY_METRICS: tuple[CapitalMetricType, ...] = (
    CapitalMetricType.DAILY_FLOW,
    CapitalMetricType.MARGIN,
    CapitalMetricType.SHAREHOLDER_COUNT,
    CapitalMetricType.CHIP_DISTRIBUTION,
    CapitalMetricType.UNLOCK,
    CapitalMetricType.DIVIDEND,
)

# Within default summary, only daily_flow is required; remaining are optional.
CAPITAL_DEFAULT_REQUIRED_METRICS: frozenset[CapitalMetricType] = frozenset(
    {CapitalMetricType.DAILY_FLOW}
)

CAPITAL_DEFAULT_OPTIONAL_METRICS: frozenset[CapitalMetricType] = frozenset(
    {
        CapitalMetricType.MARGIN,
        CapitalMetricType.SHAREHOLDER_COUNT,
        CapitalMetricType.CHIP_DISTRIBUTION,
        CapitalMetricType.UNLOCK,
        CapitalMetricType.DIVIDEND,
    }
)

# Protocol method name per capital metric (for router inventory / tests).
CAPITAL_METRIC_METHOD_NAME: Mapping[CapitalMetricType, str] = MappingProxyType(
    {
        CapitalMetricType.INTRADAY_FLOW: "get_intraday_flow",
        CapitalMetricType.DAILY_FLOW: "get_daily_flow",
        CapitalMetricType.NORTHBOUND: "get_northbound",
        CapitalMetricType.DRAGON_TIGER: "get_dragon_tiger",
        CapitalMetricType.MARGIN: "get_margin",
        CapitalMetricType.BLOCK_TRADE: "get_block_trades",
        CapitalMetricType.SHAREHOLDER_COUNT: "get_shareholder_counts",
        CapitalMetricType.CHIP_DISTRIBUTION: "get_chip_distribution",
        CapitalMetricType.UNLOCK: "get_corporate_actions",
        CapitalMetricType.DIVIDEND: "get_corporate_actions",
    }
)

# ---------------------------------------------------------------------------
# §19 Asset support matrix (tool × AssetType)
# ---------------------------------------------------------------------------

# Values: "full" | "partial" | "market_only" | "reject" | "yes" | "optional"
# Frozen for service selection; E1 only exposes the constant matrix.
A_SHARE_TOOL_ASSET_SUPPORT: Mapping[str, Mapping[AssetType, str]] = MappingProxyType(
    {
        "snapshot": MappingProxyType(
            {
                AssetType.EQUITY: "full",
                AssetType.ETF: "quote_market",
                AssetType.INDEX: "quote_market",
                AssetType.OPTION: "reject",
                AssetType.FUTURE: "reject",
            }
        ),
        "structure": MappingProxyType(
            {
                AssetType.EQUITY: "yes",
                AssetType.ETF: "yes",
                AssetType.INDEX: "yes",
                AssetType.OPTION: "reject",
                AssetType.FUTURE: "reject",
            }
        ),
        "capital": MappingProxyType(
            {
                AssetType.EQUITY: "yes",
                AssetType.ETF: "provider_supported_only",
                AssetType.INDEX: "market_scope_only",
                AssetType.OPTION: "reject",
                AssetType.FUTURE: "reject",
            }
        ),
        "limit_sentiment": MappingProxyType(
            {
                AssetType.EQUITY: "market_or_equity",
                AssetType.ETF: "market_context",
                AssetType.INDEX: "market_context",
                AssetType.OPTION: "reject",
                AssetType.FUTURE: "reject",
            }
        ),
        "etf_options": MappingProxyType(
            {
                AssetType.EQUITY: "reject_as_underlying",
                AssetType.ETF: "required",
                AssetType.INDEX: "reject",
                AssetType.OPTION: "contracts_only_in_output",
                AssetType.FUTURE: "reject",
            }
        ),
        "reports": MappingProxyType(
            {
                AssetType.EQUITY: "yes",
                AssetType.ETF: "optional_industry_theme",
                AssetType.INDEX: "optional_industry_theme",
                AssetType.OPTION: "reject",
                AssetType.FUTURE: "reject",
            }
        ),
    }
)

# Snapshot news lookback window (natural days) — §22.
SNAPSHOT_NEWS_LOOKBACK_DAYS: int = 7

# Warning codes frozen for A-share tools (§10).
A_SHARE_WARNING_CODES: frozenset[str] = frozenset(
    {
        "LOW_RELIABILITY_MARKET_SIGNAL",
        "NORTHBOUND_DISCLOSURE_INCOMPLETE",
        "SOURCE_PROVIDED_GREEKS",
        "PUBLICATION_TIME_UNKNOWN_EXCLUDED",
        "PARTIAL_A_SHARE_SNAPSHOT",
    }
)


def capital_metric_router_policy(
    metric: CapitalMetricType,
    *,
    required: bool,
) -> ToolDataPolicy:
    """Build the single-metric policy for one ``ProviderRouter.execute``.

    Returns exactly one declared category (required or optional) and that
    metric's exact non-empty vendor-chain override. Capital services must call
    this factory once per metric — never multi-metric empty overrides.
    """
    category = CAPITAL_METRIC_CATEGORY[metric]
    chain = CAPITAL_METRIC_CHAIN_OVERRIDES[metric]
    if not chain:
        raise ValueError(
            f"capital metric {metric.value} must declare a non-empty chain override"
        )
    if required:
        required_categories: tuple[DataCategory, ...] = (category,)
        optional_categories: tuple[DataCategory, ...] = ()
    else:
        required_categories = ()
        optional_categories = (category,)
    return ToolDataPolicy(
        tool_name=f"a_share_get_capital_snapshot.metric.{metric.value}",
        required_categories=required_categories,
        optional_categories=optional_categories,
        category_chain_overrides={category: chain},
    )


def capital_tool_policy_for_metrics(
    metrics: tuple[CapitalMetricType, ...],
) -> ToolDataPolicy:
    """Classification-only category elevation for explicit capital metrics.

    Aggregates unique categories for the metric set with **no** chain overrides.
    Empty ``metrics`` returns the default summary policy.

    **Not for ``ProviderRouter.execute``.** Router calls must use
    ``capital_metric_router_policy(metric, required=...)`` so each execute gets
    exactly one category and that metric's non-empty chain.
    """
    if not metrics:
        return CAPITAL_DEFAULT_SUMMARY_POLICY

    required_categories: list[DataCategory] = []
    seen: set[DataCategory] = set()
    for metric in metrics:
        category = CAPITAL_METRIC_CATEGORY[metric]
        if category not in seen:
            required_categories.append(category)
            seen.add(category)

    return ToolDataPolicy(
        tool_name="a_share_get_capital_snapshot.explicit",
        required_categories=tuple(required_categories),
        optional_categories=(),
        # Intentionally empty — classification only; do not pass to Router.
        category_chain_overrides={},
    )
