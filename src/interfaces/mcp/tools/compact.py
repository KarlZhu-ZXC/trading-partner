"""Compact 28-tool MCP surface over explicit capability adapters."""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cache, reduce
from operator import or_
from types import SimpleNamespace
from typing import Annotated, Any, Literal, cast, get_type_hints

from mcp.server.fastmcp import FastMCP
from mcp.types import Tool as MCPTool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, create_model

from bootstrap import ApplicationContainer
from interfaces.mcp.tools.a_share import build_a_share_adapters
from interfaces.mcp.tools.challenge import build_challenge_adapters
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
from interfaces.mcp.tools.watchlist import build_watchlist_adapters
from interfaces.mcp.tools.workflows import build_workflow_adapters

READ_DURABLE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
READ_PROVIDER = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
MANAGE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
MANAGE_OPEN_WORLD = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)
APPEND = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
APPEND_OPEN_WORLD = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
SYNC = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
EVALUATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
LOCAL_ARTIFACT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


@dataclass(frozen=True, slots=True)
class VariantSpec:
    operation: str
    adapter: Any
    fields: tuple[str, ...] = ()
    adapter_operation: str | None = None
    overrides: dict[str, object] = field(default_factory=dict)
    extra_fields: dict[str, tuple[object, object]] = field(default_factory=dict)
    adapter_operation_field: str | None = None


def _spec(
    operation: str,
    adapter: Any,
    fields: tuple[str, ...] = (),
    *,
    adapter_operation: str | None = None,
    overrides: dict[str, object] | None = None,
    extra_fields: dict[str, tuple[object, object]] | None = None,
    adapter_operation_field: str | None = None,
) -> VariantSpec:
    return VariantSpec(
        operation=operation,
        adapter=adapter,
        fields=fields,
        adapter_operation=adapter_operation,
        overrides=overrides or {},
        extra_fields=extra_fields or {},
        adapter_operation_field=adapter_operation_field,
    )


@cache
def _adapter_fields(adapter: Any) -> dict[str, Any]:
    hints = get_type_hints(adapter, include_extras=True)
    definitions: dict[str, Any] = {}
    for parameter in inspect.signature(adapter).parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            raise TypeError("variadic compact adapters are unsupported")
        annotation = hints.get(parameter.name, Any)
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        definitions[parameter.name] = (annotation, default)
    model = create_model(
        f"{adapter.__name__}_arguments",
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )
    return cast(dict[str, Any], model.model_fields)


def _variant_model(*, compact_tool_name: str, spec: VariantSpec) -> type[BaseModel]:
    source_fields = _adapter_fields(spec.adapter)
    definitions: dict[str, Any] = {
        "operation": (Literal[spec.operation], ...),
    }
    for name in spec.fields:
        source_field = source_fields.get(name)
        if source_field is None:
            raise RuntimeError(f"compact adapter field is missing: {spec.adapter.__name__}.{name}")
        copied = deepcopy(source_field)
        # The compact tool description and closed operation literals carry routing
        # guidance. Repeating legacy prose in every union variant inflates tools/list
        # without adding validation value; constraints and defaults stay intact.
        copied.title = None
        copied.description = None
        copied.examples = None
        definitions[name] = (copied.annotation, copied)
    definitions.update(spec.extra_fields)
    model_name = "".join(
        part.capitalize() for part in f"{compact_tool_name}_{spec.operation}_request".split("_")
    )
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )


def _register_dispatch_tool(
    server: FastMCP,
    *,
    name: str,
    description: str,
    variants: tuple[VariantSpec, ...],
    annotations: ToolAnnotations,
) -> None:
    models = tuple(_variant_model(compact_tool_name=name, spec=spec) for spec in variants)
    request_union = reduce(or_, models)
    request_type = Annotated[request_union, Field(discriminator="operation")]  # type: ignore[valid-type]
    by_operation = {spec.operation: spec for spec in variants}

    async def dispatch(request: Any) -> Any:
        operation = request.operation
        spec = by_operation[operation]
        arguments = request.model_dump(mode="python")
        arguments.pop("operation", None)
        if spec.adapter_operation_field is not None:
            arguments["operation"] = arguments.pop(spec.adapter_operation_field)
        elif spec.adapter_operation is not None:
            arguments["operation"] = spec.adapter_operation
        arguments.update(spec.overrides)
        result = spec.adapter(**arguments)
        return await result if inspect.isawaitable(result) else result

    dispatch.__name__ = name
    dispatch.__doc__ = description
    dispatch.__annotations__ = {"request": request_type, "return": Any}
    server.add_tool(dispatch, name=name, annotations=annotations)


def _copy_handler(
    server: FastMCP,
    *,
    adapter: Any,
    target_name: str | None = None,
    annotations: ToolAnnotations,
) -> None:
    server.add_tool(
        adapter,
        name=target_name or adapter.__name__,
        description=inspect.getdoc(adapter) or "",
        annotations=annotations,
    )


def _all_fields(adapter: Any, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    return tuple(name for name in _adapter_fields(adapter) if name not in exclude)


def _minimize_public_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove prose noise, shorten refs, and share repeated property schemas."""

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key != "title"}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    minimized = cast(dict[str, Any], clean(schema))
    definitions = minimized.get("$defs", {})
    if not definitions:
        return minimized
    aliases = {name: f"V{index}" for index, name in enumerate(definitions)}
    minimized["$defs"] = {aliases[name]: value for name, value in definitions.items()}

    def rewrite_refs(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and item.startswith("#/$defs/"):
                    definition = item.rsplit("/", 1)[-1]
                    if definition in aliases:
                        value[key] = f"#/$defs/{aliases[definition]}"
                else:
                    rewrite_refs(item)
        elif isinstance(value, list):
            for item in value:
                rewrite_refs(item)

    rewrite_refs(minimized)
    _share_repeated_property_schemas(minimized)
    return minimized


def _share_repeated_property_schemas(schema: dict[str, Any]) -> None:
    """Hoist repeated variant property schemas when doing so reduces wire bytes."""
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    occurrences: dict[str, list[tuple[dict[str, Any], str]]] = {}
    values: dict[str, dict[str, Any]] = {}
    for definition in tuple(definitions.values()):
        if not isinstance(definition, dict):
            continue
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            continue
        for field_name, field_schema in properties.items():
            if field_name == "operation" or not isinstance(field_schema, dict):
                continue
            canonical = json.dumps(field_schema, sort_keys=True, separators=(",", ":"))
            occurrences.setdefault(canonical, []).append((properties, field_name))
            values[canonical] = field_schema

    shared_index = 0
    for canonical in sorted(occurrences):
        locations = occurrences[canonical]
        if len(locations) < 2:
            continue
        shared_name = f"S{shared_index}"
        reference = {"$ref": f"#/$defs/{shared_name}"}
        reference_size = len(json.dumps(reference, separators=(",", ":")))
        definition_cost = len(shared_name) + len(canonical) + 6
        if len(locations) * len(canonical) <= definition_cost + len(locations) * reference_size:
            continue
        definitions[shared_name] = values[canonical]
        for properties, field_name in locations:
            properties[field_name] = reference.copy()
        shared_index += 1


class CompactFastMCP(FastMCP):
    """FastMCP surface that minimizes schemas only at the public protocol boundary."""

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema = _minimize_public_schema(tool.inputSchema)
        return tools


def create_compact_mcp_server(
    container: ApplicationContainer,
    *,
    chart_persister: Any,
) -> FastMCP:
    """Build the sole compact 28-tool public surface."""
    adapters = SimpleNamespace(
        system=build_system_adapters(
            container,
            surface_profile="compact_28",
            public_tool_count=28,
            surface_schema_version="compact-v2",
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
        workflows=build_workflow_adapters(container),
        watchlist=build_watchlist_adapters(container),
        risk=build_risk_adapters(container),
        monitoring=build_monitoring_adapters(container),
    )
    server = CompactFastMCP(container.settings.mcp_server_name)

    _copy_handler(
        server,
        adapter=adapters.system.system_health,
        annotations=READ_DURABLE,
    )
    _copy_handler(
        server,
        adapter=adapters.instrument.instrument_resolve,
        annotations=APPEND_OPEN_WORLD,
    )

    _register_dispatch_tool(
        server,
        name="investment_case_read",
        description="Query durable research files or build one bounded current research context.",
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
        ),
        annotations=READ_DURABLE,
    )
    _register_dispatch_tool(
        server,
        name="investment_case_manage",
        description="Create or archive a durable research file with confirmation and idempotency.",
        variants=(
            _spec(
                "create",
                adapters.research.investment_case_create,
                _all_fields(adapters.research.investment_case_create),
            ),
            _spec(
                "archive",
                adapters.research.investment_case_archive,
                _all_fields(adapters.research.investment_case_archive),
            ),
        ),
        annotations=MANAGE,
    )
    _register_dispatch_tool(
        server,
        name="research_judgment_get",
        description="Read current research state or the append-only history of one Thesis.",
        variants=(
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
        ),
        annotations=READ_DURABLE,
    )
    _register_dispatch_tool(
        server,
        name="research_judgment_propose",
        description=(
            "Propose a research-state, Trade Plan, or Thesis revision candidate; never confirm it."
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
        ),
        annotations=APPEND,
    )
    _copy_handler(
        server,
        adapter=adapters.research.thesis_revision_confirm,
        target_name="research_judgment_confirm",
        annotations=APPEND,
    )

    _register_dispatch_tool(
        server,
        name="research_memory_get",
        description="Search durable research memory, read one report, or restore a Case timeline.",
        variants=(
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
        ),
        annotations=READ_DURABLE,
    )
    _register_dispatch_tool(
        server,
        name="research_memory_append",
        description="Append a confirmed Journal or Decision intent record; never create an order.",
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
        ),
        annotations=APPEND,
    )

    _register_a_share(server, adapters.a_share)
    _register_market_and_us(server, adapters.market, adapters.us_research, adapters.us_context)

    async def account_get(snapshot_id: str | None = None) -> Any:
        """Read durable account positions only; this compact tool never refreshes a broker."""
        return await adapters.portfolio.account_get(
            operation="positions",
            snapshot_id=snapshot_id,
        )

    server.add_tool(account_get, name="account_get", annotations=READ_DURABLE)
    _register_external_sync(server, adapters.portfolio, adapters.watchlist)
    _register_portfolio_challenge_workflows(
        server,
        adapters.portfolio,
        adapters.challenge,
        adapters.workflows,
    )
    _register_watchlist_risk_monitoring(
        server,
        adapters.watchlist,
        adapters.risk,
        adapters.monitoring,
    )
    return server


def _register_a_share(server: FastMCP, adapters: SimpleNamespace) -> None:
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
    _register_dispatch_tool(
        server,
        name=facts,
        description=(
            "Read one closed A-share fact family or search current provider research reports."
        ),
        variants=variants,
        annotations=READ_PROVIDER,
    )


def _register_market_and_us(
    server: FastMCP,
    market: SimpleNamespace,
    us_research: SimpleNamespace,
    us_context: SimpleNamespace,
) -> None:
    _register_dispatch_tool(
        server,
        name="market_data_get",
        description=(
            "Read a cross-market quote/composite, bars, US market context, futures curve, or basis."
        ),
        variants=(
            _spec(
                "quote",
                market.market_get_snapshot,
                ("instrument_id", "as_of"),
                adapter_operation="quote",
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
        annotations=READ_PROVIDER,
    )
    _copy_handler(server, adapter=market.technical_get_snapshot, annotations=READ_PROVIDER)
    _copy_handler(
        server,
        adapter=market.technical_render_chart,
        annotations=LOCAL_ARTIFACT,
    )
    _register_dispatch_tool(
        server,
        name="us_company_get",
        description=(
            "Read US fundamentals, filings, insiders, company updates, events, or dated live news."
        ),
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
        annotations=READ_PROVIDER,
    )
    _register_dispatch_tool(
        server,
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
        annotations=READ_PROVIDER,
    )


def _register_external_sync(
    server: FastMCP,
    portfolio: SimpleNamespace,
    watchlist: SimpleNamespace,
) -> None:
    view_type = Literal["groups", "items"]
    _register_dispatch_tool(
        server,
        name="external_state_sync",
        description=(
            "Explicitly fetch and persist broker accounts, transactions, or the active "
            "Watchlist upstream."
        ),
        variants=(
            _spec(
                "accounts",
                portfolio.account_get,
                ("providers", "as_of"),
                adapter_operation="refresh",
            ),
            _spec(
                "transactions",
                portfolio.account_get,
                ("providers", "start", "end", "limit"),
                adapter_operation="transactions",
            ),
            _spec(
                "watchlist",
                watchlist.watchlist_get,
                ("group_name", "include_inactive", "limit", "offset"),
                overrides={"refresh": True},
                extra_fields={"view": (view_type, "items")},
                adapter_operation_field="view",
            ),
        ),
        annotations=SYNC,
    )


def _register_portfolio_challenge_workflows(
    server: FastMCP,
    portfolio: SimpleNamespace,
    challenge: SimpleNamespace,
    workflows: SimpleNamespace,
) -> None:
    _register_dispatch_tool(
        server,
        name="portfolio_analyze",
        description=(
            "Analyze durable portfolio exposure or simulate one calculation-only "
            "hypothetical addition."
        ),
        variants=(
            _spec(
                "exposure",
                portfolio.portfolio_analyze,
                _all_fields(portfolio.portfolio_analyze),
            ),
            _spec(
                "simulate_addition",
                portfolio.portfolio_simulate_addition,
                _all_fields(portfolio.portfolio_simulate_addition),
            ),
        ),
        annotations=READ_DURABLE,
    )
    _copy_handler(server, adapter=challenge.challenge_review_get, annotations=READ_DURABLE)
    _register_dispatch_tool(
        server,
        name="challenge_review_manage",
        description="Start or explicitly resolve a non-executing Challenge Review.",
        variants=(
            _spec(
                "start",
                challenge.challenge_review_start,
                _all_fields(challenge.challenge_review_start),
            ),
            _spec(
                "resolve",
                challenge.challenge_review_resolve,
                _all_fields(challenge.challenge_review_resolve),
            ),
        ),
        annotations=APPEND,
    )
    _register_dispatch_tool(
        server,
        name="research_workflow_run",
        description=(
            "Run one closed research, peer-comparison, market, or portfolio fact workflow; "
            "synthesis remains external."
        ),
        variants=(
            _spec(
                "deep_dive",
                workflows.research_run_deep_dive,
                (
                    "idempotency_key",
                    "case_id",
                    "instrument_id",
                    "as_of",
                    "lookback_days",
                    "industry_cycle",
                    "industry_cycle_lookback_months",
                    "company_operating_lookback_months",
                    "company_operating_document_limit",
                ),
                overrides={"create_case": False},
            ),
            _spec(
                "catalyst_review",
                workflows.research_run_catalyst_review,
                _all_fields(workflows.research_run_catalyst_review),
            ),
            _spec(
                "a_share_market_review",
                workflows.a_share_run_market_review,
                _all_fields(workflows.a_share_run_market_review),
            ),
            _spec(
                "us_market_review",
                workflows.us_run_market_review,
                _all_fields(workflows.us_run_market_review),
            ),
            _spec(
                "portfolio_review",
                workflows.portfolio_run_review,
                (
                    "idempotency_key",
                    "account_snapshot_ids",
                    "as_of",
                    "risk_lookback_sessions",
                    "max_risk_instruments",
                ),
                overrides={"refresh_accounts": False, "providers": ()},
            ),
            _spec(
                "peer_comparison",
                workflows.research_run_peer_comparison,
                _all_fields(workflows.research_run_peer_comparison),
            ),
        ),
        annotations=APPEND_OPEN_WORLD,
    )


def _register_watchlist_risk_monitoring(
    server: FastMCP,
    watchlist: SimpleNamespace,
    risk: SimpleNamespace,
    monitoring: SimpleNamespace,
) -> None:
    _register_dispatch_tool(
        server,
        name="watchlist_get",
        description=(
            "Read durable Watchlist groups or items without contacting the upstream provider."
        ),
        variants=(
            _spec(
                "groups",
                watchlist.watchlist_get,
                ("group_name", "include_inactive", "limit", "offset"),
                adapter_operation="groups",
                overrides={"refresh": False},
            ),
            _spec(
                "items",
                watchlist.watchlist_get,
                ("group_name", "include_inactive", "limit", "offset"),
                adapter_operation="items",
                overrides={"refresh": False},
            ),
        ),
        annotations=READ_DURABLE,
    )
    _register_dispatch_tool(
        server,
        name="watchlist_manage",
        description=(
            "Add or remove one active-source Watchlist membership with confirmation and "
            "idempotency."
        ),
        variants=(
            _spec("add", watchlist.watchlist_add, _all_fields(watchlist.watchlist_add)),
            _spec(
                "remove",
                watchlist.watchlist_remove,
                _all_fields(watchlist.watchlist_remove),
            ),
        ),
        annotations=MANAGE_OPEN_WORLD,
    )
    _register_dispatch_tool(
        server,
        name="portfolio_risk_get",
        description=(
            "Read the current Risk Policy or run a deterministic non-executing Risk v2 check."
        ),
        variants=(
            _spec("policy", risk.risk_policy_get),
            _spec(
                "check",
                risk.risk_check,
                _all_fields(risk.risk_check),
                overrides={"refresh_accounts": False},
            ),
        ),
        annotations=READ_PROVIDER,
    )
    _copy_handler(server, adapter=risk.risk_policy_update, annotations=APPEND)
    _register_dispatch_tool(
        server,
        name="monitor_read",
        description="Read Monitor definitions/current rule states or durable Monitor events.",
        variants=(
            _spec(
                "definitions",
                monitoring.monitor_query,
                _all_fields(monitoring.monitor_query),
            ),
            _spec(
                "events",
                monitoring.monitor_event_list,
                _all_fields(monitoring.monitor_event_list),
            ),
        ),
        annotations=READ_DURABLE,
    )
    _register_dispatch_tool(
        server,
        name="monitor_manage",
        description=(
            "Create/update a versioned Monitor or resolve one event; never mutate Thesis "
            "or positions."
        ),
        variants=(
            _spec(
                "create",
                monitoring.monitor_create,
                _all_fields(monitoring.monitor_create),
            ),
            _spec(
                "update",
                monitoring.monitor_update,
                _all_fields(monitoring.monitor_update),
            ),
            _spec(
                "resolve_event",
                monitoring.monitor_event_resolve,
                _all_fields(monitoring.monitor_event_resolve),
            ),
        ),
        annotations=MANAGE,
    )
    _copy_handler(server, adapter=monitoring.monitor_evaluate, annotations=EVALUATE)
