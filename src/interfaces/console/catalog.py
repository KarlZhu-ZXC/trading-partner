"""Derive a safe capability catalog from the actual compact MCP surface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.types import Tool as MCPTool

from interfaces.mcp.tools.compact import CapabilityPolicy

_GROUPS = {
    "system_health": "System",
    "instrument_resolve": "Instruments",
    "investment_case_read": "Research",
    "investment_case_manage": "Research",
    "research_judgment_get": "Judgment",
    "research_judgment_propose": "Judgment",
    "research_judgment_confirm": "Judgment",
    "research_memory_get": "Memory",
    "research_memory_append": "Memory",
    "a_share_get_facts": "A-share facts",
    "market_data_get": "Market facts",
    "technical_get_snapshot": "Technical",
    "technical_render_chart": "Technical",
    "us_company_get": "US research",
    "us_context_get": "US research",
    "account_get": "Accounts",
    "external_state_sync": "Accounts",
    "portfolio_analyze": "Portfolio",
    "broker_order_manage": "Broker orders",
    "research_workflow_run": "Workflows",
    "watchlist_get": "Watchlist",
    "watchlist_manage": "Watchlist",
    "portfolio_risk_get": "Risk",
    "risk_policy_update": "Risk",
    "monitor_read": "Monitoring",
    "monitor_manage": "Monitoring",
    "monitor_evaluate": "Monitoring",
}


def _operations(schema: Mapping[str, Any]) -> tuple[str, ...]:
    values: set[str] = set()
    definitions = schema.get("$defs")
    if isinstance(definitions, Mapping):
        for definition in definitions.values():
            if not isinstance(definition, Mapping):
                continue
            properties = definition.get("properties")
            if not isinstance(properties, Mapping):
                continue
            operation = properties.get("operation")
            if isinstance(operation, Mapping) and isinstance(operation.get("const"), str):
                values.add(operation["const"])
    return tuple(sorted(values))


def capability_catalog(
    tools: list[MCPTool],
    policies: Mapping[str, CapabilityPolicy] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in sorted(tools, key=lambda item: (_GROUPS.get(item.name, "Other"), item.name)):
        annotations = tool.annotations.model_dump(mode="json") if tool.annotations else {}
        policy = policies.get(tool.name) if policies is not None else None
        result.append(
            {
                "name": tool.name,
                "group": _GROUPS.get(tool.name, "Other"),
                "description": tool.description or "",
                "operations": _operations(tool.inputSchema),
                "read_only": bool(annotations.get("readOnlyHint")),
                "open_world": bool(annotations.get("openWorldHint")),
                "destructive": bool(annotations.get("destructiveHint")),
                "effect": policy.effect.value if policy is not None else None,
                "confirmation_required": (
                    policy.confirmation_required
                    if policy is not None
                    else not bool(annotations.get("readOnlyHint"))
                ),
                "input_schema": tool.inputSchema,
            }
        )
    return result
