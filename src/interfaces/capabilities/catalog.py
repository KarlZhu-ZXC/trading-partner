"""Transport-neutral capability discovery categories.

The category catalogue contains capability identities only.  It deliberately
does not know about validators, handlers, or a particular transport so that
discovery consumers and audit tooling can share the same closed grouping.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

type CapabilityCategory = Literal[
    "research",
    "portfolio",
    "market",
    "monitoring",
    "operations",
]

CATEGORY_CAPABILITIES: Final[Mapping[CapabilityCategory, frozenset[str]]] = MappingProxyType(
    {
        "research": frozenset(
            {
                "view_get",
                "research_get",
                "investment_case_manage",
                "research_judgment_propose",
                "research_judgment_confirm",
                "research_memory_append",
                "research_workflow_run",
            }
        ),
        "portfolio": frozenset(
            {
                "portfolio_get",
                "watchlist_get",
                "watchlist_manage",
                "portfolio_risk_get",
                "risk_policy_update",
            }
        ),
        "market": frozenset(
            {
                "instrument_resolve",
                "a_share_get_facts",
                "market_data_get",
                "technical_get_snapshot",
                "technical_render_chart",
                "us_get_facts",
            }
        ),
        "monitoring": frozenset(
            {
                "monitor_read",
                "monitor_manage",
                "monitor_evaluate",
            }
        ),
        "operations": frozenset(
            {
                "system_health",
                "external_state_sync",
                "broker_order_manage",
            }
        ),
    }
)

__all__ = ["CATEGORY_CAPABILITIES", "CapabilityCategory"]
