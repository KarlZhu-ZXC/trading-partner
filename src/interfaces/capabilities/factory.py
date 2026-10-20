"""Canonical transport-neutral capability graph composition."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from bootstrap import ApplicationContainer
from interfaces.capabilities.policy import (
    APPEND,
    CACHE_DISCOVERY,
    MANAGE,
    MANAGE_OPEN_WORLD,
    READ_DURABLE,
)
from interfaces.capabilities.registration import (
    _all_fields,
    _copy_handler,
    _register_dispatch_tool,
    _register_flat_dispatch_tool,
    _spec,
)
from interfaces.capabilities.registry import CompactCapabilityRegistry
from interfaces.mcp.tool_inventory import MCP_VNEXT_TOOL_NAMES, PUBLIC_TOOL_NAMES
from interfaces.mcp.tools.a_share import build_a_share_adapters
from interfaces.mcp.tools.challenge import build_challenge_adapters
from interfaces.mcp.tools.compact_registration_a_share import _register_a_share
from interfaces.mcp.tools.compact_registration_external_sync import _register_external_sync
from interfaces.mcp.tools.compact_registration_market_us import _register_market_and_us
from interfaces.mcp.tools.compact_registration_portfolio_challenge import (
    _register_portfolio_challenge_workflows,
    _register_portfolio_get,
)
from interfaces.mcp.tools.compact_registration_watchlist_risk_monitoring import (
    _register_watchlist_risk_monitoring,
)
from interfaces.mcp.tools.execution import build_execution_adapters
from interfaces.mcp.tools.instrument import build_instrument_adapters
from interfaces.mcp.tools.market_technical import build_market_technical_adapters
from interfaces.mcp.tools.monitoring import build_monitoring_adapters
from interfaces.mcp.tools.portfolio import build_portfolio_adapters
from interfaces.mcp.tools.research import build_research_adapters
from interfaces.mcp.tools.research_memory import build_research_memory_adapters
from interfaces.mcp.tools.risk import build_risk_adapters
from interfaces.mcp.tools.system import build_system_adapters
from interfaces.mcp.tools.us_context import build_us_context_adapters
from interfaces.mcp.tools.us_research import build_us_research_adapters
from interfaces.mcp.tools.view_review import build_view_review_adapters
from interfaces.mcp.tools.watchlist import build_watchlist_adapters
from interfaces.mcp.tools.workflows import build_workflow_adapters


def create_capability_registry(
    container: ApplicationContainer,
    *,
    chart_persister: Any,
    source: str = "INTERNAL",
) -> CompactCapabilityRegistry:
    """Build the sole transport-neutral handler/schema/policy registry."""

    adapters = SimpleNamespace(
        system=build_system_adapters(
            container,
            surface_profile="mcp_vnext_shadow",
            public_tool_count=len(PUBLIC_TOOL_NAMES),
            surface_schema_version="mcp-vnext-shadow-v16",
        ),
        instrument=build_instrument_adapters(container),
        research=build_research_adapters(container),
        research_memory=build_research_memory_adapters(container),
        a_share=build_a_share_adapters(container),
        market=build_market_technical_adapters(container, chart_persister),
        us_research=build_us_research_adapters(container),
        us_context=build_us_context_adapters(container),
        portfolio=build_portfolio_adapters(container),
        challenge=build_challenge_adapters(container),
        execution=build_execution_adapters(container),
        workflows=build_workflow_adapters(container),
        watchlist=build_watchlist_adapters(container),
        risk=build_risk_adapters(container),
        monitoring=build_monitoring_adapters(container),
        view_review=build_view_review_adapters(container),
    )
    registry = CompactCapabilityRegistry(
        getattr(container.context, "capability_usage", None),
        source=source,
    )

    _copy_handler(
        registry,
        adapter=adapters.system.system_health,
        policy=READ_DURABLE,
    )
    _copy_handler(
        registry,
        adapter=adapters.instrument.instrument_resolve,
        policy=CACHE_DISCOVERY,
    )
    _register_dispatch_tool(
        registry,
        name="view_get",
        description=(
            "Read the Moomoo-first judgment intake: list pending note changes, compare "
            "one exact revision with durable context, or restore a Subject's latest "
            "confirmed view. Notes need no Subject or interpretation. Private text requires "
            "an explicit owner request; reads never call a Provider or a model."
        ),
        variants=(
            _spec(
                "notes",
                adapters.view_review.notes_inbox,
                _all_fields(adapters.view_review.notes_inbox),
            ),
            _spec(
                "note", adapters.view_review.note_read, _all_fields(adapters.view_review.note_read)
            ),
            _spec(
                "inbox",
                adapters.view_review.view_inbox,
                _all_fields(adapters.view_review.view_inbox),
            ),
            _spec(
                "review",
                adapters.view_review.view_review_get,
                _all_fields(adapters.view_review.view_review_get),
            ),
            _spec(
                "current",
                adapters.view_review.current_view_get,
                _all_fields(adapters.view_review.current_view_get),
            ),
        ),
        policy=READ_DURABLE,
    )

    _register_flat_dispatch_tool(
        registry,
        name="research_get",
        description=(
            "Read durable Research Subjects (标的), build one bounded current research "
            "context, read research state and history, search research memory, restore a "
            "timeline or Catalyst Agenda, or read the cross-domain decision inbox. Legacy "
            "transport keeps case_id/case_type; operation=attention is read-only."
        ),
        variants=(
            _spec(
                "query",
                adapters.research.investment_case_query,
                _all_fields(adapters.research.investment_case_query),
            ),
            _spec(
                "context",
                adapters.research.research_context_build,
                _all_fields(adapters.research.research_context_build),
            ),
            _spec(
                "attention",
                adapters.research.attention_read,
                _all_fields(adapters.research.attention_read),
            ),
            _spec(
                "state",
                adapters.research.research_state_get,
                _all_fields(adapters.research.research_state_get),
            ),
            _spec(
                "thesis_history",
                adapters.research.thesis_history_get,
                _all_fields(adapters.research.thesis_history_get),
            ),
            _spec(
                "scorecard_history",
                adapters.research.judgment_scorecard_history,
                _all_fields(adapters.research.judgment_scorecard_history),
            ),
            _spec(
                "challenge_review",
                adapters.challenge.challenge_review_get,
                _all_fields(adapters.challenge.challenge_review_get),
            ),
            _spec(
                "search",
                adapters.research_memory.research_search,
                _all_fields(adapters.research_memory.research_search),
            ),
            _spec(
                "report",
                adapters.research_memory.research_report_get,
                _all_fields(adapters.research_memory.research_report_get),
            ),
            _spec(
                "timeline",
                adapters.research_memory.research_timeline_get,
                _all_fields(adapters.research_memory.research_timeline_get),
            ),
            _spec(
                "agenda",
                adapters.research_memory.catalyst_agenda_get,
                _all_fields(adapters.research_memory.catalyst_agenda_get),
            ),
        ),
        policy=READ_DURABLE,
    )
    _register_flat_dispatch_tool(
        registry,
        name="investment_case_manage",
        description=(
            "Create, update, or archive a durable Research Subject (标的) with confirmation "
            "and idempotency. Legacy transport keeps investment_case_manage plus case_id, "
            "case_type, and linked_case_ids. "
            "Title identifies the research object/question; summary defines research scope. "
            "Action levels, sizing, and entry/exit belong to the Thesis or Trade Plan."
        ),
        variants=(
            _spec(
                "create",
                adapters.research.investment_case_create,
                _all_fields(adapters.research.investment_case_create),
            ),
            _spec(
                "update",
                adapters.research.investment_case_update,
                _all_fields(adapters.research.investment_case_update),
            ),
            _spec(
                "archive",
                adapters.research.investment_case_archive,
                _all_fields(adapters.research.investment_case_archive),
            ),
        ),
        policy=MANAGE,
    )
    _register_flat_dispatch_tool(
        registry,
        name="research_judgment_propose",
        description=(
            "Propose Research state, an Instrument attachment, Trade Plan, or Thesis "
            "changes; never confirm them. A confirmed watchlist_item create attaches "
            "the Instrument directly; do not require Shortlist or Select afterward."
        ),
        variants=(
            _spec(
                "research_state",
                adapters.research.research_state_update,
                _all_fields(adapters.research.research_state_update),
            ),
            _spec(
                "thesis_revision",
                adapters.research.thesis_revision_propose,
                _all_fields(adapters.research.thesis_revision_propose),
            ),
            _spec(
                "challenge_review",
                adapters.challenge.challenge_review_start,
                _all_fields(adapters.challenge.challenge_review_start),
            ),
        ),
        policy=APPEND,
    )
    _register_dispatch_tool(
        registry,
        name="research_judgment_confirm",
        description=(
            "Apply an explicit candidate decision or resolve a non-executing Challenge Review."
        ),
        variants=(
            _spec(
                "candidate",
                adapters.research.thesis_revision_confirm,
                _all_fields(adapters.research.thesis_revision_confirm),
            ),
            _spec(
                "challenge_review",
                adapters.challenge.challenge_review_resolve,
                _all_fields(adapters.challenge.challenge_review_resolve),
            ),
        ),
        policy=APPEND,
    )

    _register_flat_dispatch_tool(
        registry,
        name="research_memory_append",
        description=(
            "Append a confirmed Journal, Decision intent, Broker-activity annotation, "
            "or Catalyst Agenda version; never create an order."
        ),
        variants=(
            _spec(
                "journal",
                adapters.research_memory.journal_append,
                _all_fields(adapters.research_memory.journal_append),
            ),
            _spec(
                "decision",
                adapters.research_memory.decision_record_append,
                _all_fields(adapters.research_memory.decision_record_append),
            ),
            _spec(
                "agenda_item",
                adapters.research_memory.catalyst_agenda_manage,
                _all_fields(adapters.research_memory.catalyst_agenda_manage),
            ),
            _spec(
                "activity_annotation",
                adapters.research_memory.activity_annotation_append,
                _all_fields(adapters.research_memory.activity_annotation_append),
            ),
            _spec(
                "trade_cycle_override",
                adapters.research_memory.trade_cycle_override_append,
                tuple(
                    field
                    for field in _all_fields(adapters.research_memory.trade_cycle_override_append)
                    if field != "operation"
                ),
                extra_fields={"override_operation": (str, ...)},
                adapter_operation_field="override_operation",
            ),
            _spec(
                "behavior_review",
                adapters.research_memory.behavior_review_run,
                _all_fields(adapters.research_memory.behavior_review_run),
            ),
        ),
        policy=APPEND,
    )

    _register_a_share(registry, adapters.a_share)
    _register_market_and_us(
        registry,
        adapters.market,
        adapters.us_research,
        adapters.us_context,
    )

    _register_portfolio_get(registry, adapters.portfolio)
    _register_external_sync(registry, adapters.portfolio, adapters.watchlist, adapters.view_review)
    _register_dispatch_tool(
        registry,
        name="broker_order_manage",
        description=(
            "Preview an SGOV cash sweep or manage one exact Schwab US equity/ETF order. "
            "Live submit/cancel requires a short-lived durable preview and explicit user "
            "authorization; uncertain writes are never retried automatically."
        ),
        variants=(
            _spec(
                "cash_sweep_preview",
                adapters.execution.cash_sweep_preview,
                _all_fields(adapters.execution.cash_sweep_preview),
            ),
            _spec(
                "preview",
                adapters.execution.order_preview,
                _all_fields(adapters.execution.order_preview),
            ),
            _spec(
                "submit",
                adapters.execution.order_submit,
                _all_fields(adapters.execution.order_submit),
            ),
            _spec(
                "status",
                adapters.execution.order_status,
                _all_fields(adapters.execution.order_status),
            ),
            _spec(
                "cancel",
                adapters.execution.order_cancel,
                _all_fields(adapters.execution.order_cancel),
            ),
        ),
        policy=MANAGE_OPEN_WORLD,
    )
    _register_portfolio_challenge_workflows(
        registry,
        adapters.workflows,
        adapters.view_review,
    )
    _register_watchlist_risk_monitoring(
        registry,
        adapters.watchlist,
        adapters.risk,
        adapters.monitoring,
    )
    if set(registry.policies) != set(MCP_VNEXT_TOOL_NAMES):
        raise RuntimeError("MCP capability registry does not match vNext inventory")
    return registry
