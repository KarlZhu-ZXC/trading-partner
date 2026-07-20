"""US research tool category policies (Phase 1G G1).

Constants only — no service orchestration. Fundamentals/statements/filings are
core for their tools; sections/actions degrade optionally within the envelope.
"""

from __future__ import annotations

from application.dto.provider_routing import ToolDataPolicy
from domain.common.enums import DataCategory, VendorId

OFFICIAL_FUNDAMENTALS_POLICY = ToolDataPolicy(
    tool_name="fundamental_get_snapshot",
    required_categories=(DataCategory.FUNDAMENTALS,),
    optional_categories=(),
    category_chain_overrides={DataCategory.FUNDAMENTALS: (VendorId.SEC_EDGAR,)},
)

FUNDAMENTAL_SNAPSHOT_POLICY = ToolDataPolicy(
    tool_name="fundamental_get_snapshot",
    required_categories=(DataCategory.FUNDAMENTALS,),
    optional_categories=(DataCategory.CORPORATE_ACTIONS,),
    category_chain_overrides={},
)

FUNDAMENTAL_STATEMENTS_POLICY = ToolDataPolicy(
    tool_name="fundamental_get_statements",
    required_categories=(DataCategory.FINANCIAL_STATEMENTS,),
    optional_categories=(),
    category_chain_overrides={},
)

US_FILINGS_POLICY = ToolDataPolicy(
    tool_name="us_get_filings",
    required_categories=(DataCategory.FILINGS,),
    optional_categories=(),
    category_chain_overrides={},
)

US_INSIDER_ACTIVITY_POLICY = ToolDataPolicy(
    tool_name="us_get_insider_activity",
    required_categories=(DataCategory.INSIDER_ACTIVITY,),
    optional_categories=(),
    category_chain_overrides={},
)

RESEARCH_COMPANY_UPDATES_POLICY = ToolDataPolicy(
    tool_name="research_get_company_updates",
    required_categories=(),
    optional_categories=(
        DataCategory.FILINGS,
        DataCategory.INSIDER_ACTIVITY,
        DataCategory.CORPORATE_ACTIONS,
    ),
    category_chain_overrides={},
)

EVENTS_SEARCH_POLICY = ToolDataPolicy(
    tool_name="events_search",
    required_categories=(),
    optional_categories=(
        DataCategory.FILINGS,
        DataCategory.INSIDER_ACTIVITY,
        DataCategory.CORPORATE_ACTIONS,
    ),
    category_chain_overrides={},
)

# Exact six future tools (design §6); order frozen for inventory tests.
PHASE1G_US_RESEARCH_TOOL_POLICIES: tuple[ToolDataPolicy, ...] = (
    FUNDAMENTAL_SNAPSHOT_POLICY,
    FUNDAMENTAL_STATEMENTS_POLICY,
    US_FILINGS_POLICY,
    US_INSIDER_ACTIVITY_POLICY,
    RESEARCH_COMPANY_UPDATES_POLICY,
    EVENTS_SEARCH_POLICY,
)

PHASE1G_US_RESEARCH_TOOL_NAMES: tuple[str, ...] = tuple(
    policy.tool_name for policy in PHASE1G_US_RESEARCH_TOOL_POLICIES
)
