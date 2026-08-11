"""Single source of truth for the sole public MCP inventory."""

MCP_VNEXT_TOOL_NAMES = frozenset(
    {
        "system_health",
        "instrument_resolve",
        "investment_case_read",
        "investment_case_manage",
        "research_judgment_get",
        "research_judgment_propose",
        "research_judgment_confirm",
        "research_memory_get",
        "research_memory_append",
        "a_share_get_facts",
        "market_data_get",
        "technical_get_snapshot",
        "technical_render_chart",
        "us_company_get",
        "us_context_get",
        "account_get",
        "external_state_sync",
        "portfolio_analyze",
        "broker_order_manage",
        "research_workflow_run",
        "watchlist_get",
        "watchlist_manage",
        "portfolio_risk_get",
        "risk_policy_update",
        "monitor_read",
        "monitor_manage",
        "monitor_evaluate",
    }
)

PUBLIC_TOOL_NAMES = MCP_VNEXT_TOOL_NAMES

FORBIDDEN_PUBLIC_TOOL_NAMES = frozenset(
    {
        "evidence_create",
        "evidence_update",
        "report_create",
        "event_create",
        "decision_update",
        "journal_update",
        "journal_delete",
        "order_place",
        "order_modify",
        "order_cancel",
        "trade_unlock",
    }
)

RETIRED_PUBLIC_TOOL_NAMES = frozenset(
    {
        "market_get_mock_snapshot",
        "investment_case_get",
        "investment_case_list",
        "journal_search",
        "a_share_get_snapshot",
        "a_share_get_market_structure",
        "a_share_get_capital_snapshot",
        "a_share_get_limit_up_context",
        "a_share_get_sentiment_snapshot",
        "a_share_get_etf_option_snapshot",
        "us_get_market",
        "us_get_snapshot",
        "fundamental_get_snapshot",
        "fundamental_get_statements",
        "us_get_filings",
        "us_get_insider_activity",
        "research_get_company_updates",
        "events_search",
        "account_get_snapshot",
        "account_get_positions",
        "account_get_transactions",
        "watchlist_get_groups",
        "watchlist_get_items",
        "monitor_get",
        "monitor_list",
    }
)

assert len(MCP_VNEXT_TOOL_NAMES) == 27
assert MCP_VNEXT_TOOL_NAMES.isdisjoint(FORBIDDEN_PUBLIC_TOOL_NAMES)
assert MCP_VNEXT_TOOL_NAMES.isdisjoint(RETIRED_PUBLIC_TOOL_NAMES)
