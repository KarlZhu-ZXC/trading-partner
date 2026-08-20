"""Compact MCP surface inventory, schema, and explicit-sync boundary tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import Draft202012Validator
from mcp.server.fastmcp.exceptions import ToolError

from interfaces.mcp.server import (
    MCP_VNEXT_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_capability_registry,
    create_mcp_server,
)
from interfaces.mcp.tools.compact import ConfirmationPolicy, _minimize_public_schema


class _Envelope:
    def __init__(
        self,
        data: dict[str, Any] | None = None,
        *,
        degraded: bool = False,
        warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        self._data = data or {}
        self._degraded = degraded
        self._warnings = warnings or []

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {
            "ok": True,
            "request_id": "req_compact",
            "degraded": self._degraded,
            "warnings": self._warnings,
            "data": dict(self._data),
        }


def _container() -> MagicMock:
    container = MagicMock()
    container.settings = SimpleNamespace(mcp_server_name="Trading Partner Test")
    container.services = MagicMock()
    container.services.data_quality.check.return_value = _Envelope()
    container.services.attention.health_summary.return_value.model_dump.return_value = {
        "generated_at": "2026-08-17T12:00:00+00:00",
        "basis": "materialized_review_items",
        "live_projections_not_included": True,
        "open_review_item_count": 0,
        "acknowledged_review_item_count": 0,
        "catalyst_sync_receipt_missing": True,
        "coverage_status": "UNKNOWN",
    }
    return container


def _wire_size(tools: list[Any]) -> int:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
            "outputSchema": tool.outputSchema,
            "annotations": (tool.annotations.model_dump(mode="json") if tool.annotations else None),
        }
        for tool in tools
    ]
    return len(json.dumps(payload, separators=(",", ":")))


def _local_definition_refs(value: Any) -> set[str]:
    if isinstance(value, dict):
        refs: set[str] = set()
        for item in value.values():
            refs.update(_local_definition_refs(item))
        return refs
    if isinstance(value, list):
        refs = set()
        for item in value:
            refs.update(_local_definition_refs(item))
        return refs
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return {value.rsplit("/", 1)[-1]}
    return set()


def test_compact_schema_inlines_profitable_nullable_definition_equivalently() -> None:
    original = {
        "$defs": {"Only": {"enum": ["A", "B"]}},
        "properties": {
            "value": {
                "anyOf": [
                    {"$ref": "#/$defs/Only"},
                    {"type": "null"},
                ]
            }
        },
        "required": ["value"],
        "type": "object",
    }

    compact = _minimize_public_schema(original)

    assert "$defs" not in compact
    assert compact["properties"]["value"] == {"enum": ["A", "B", None]}
    original_validator = Draft202012Validator(original)
    compact_validator = Draft202012Validator(compact)
    for value in ("A", "B", None, "C", 1, {}, []):
        payload = {"value": value}
        assert bool(list(original_validator.iter_errors(payload))) == bool(
            list(compact_validator.iter_errors(payload))
        )


@pytest.mark.asyncio
async def test_compact_is_the_only_public_surface() -> None:
    compact = await create_mcp_server(_container()).list_tools()
    compact_names = {tool.name for tool in compact}

    assert PUBLIC_TOOL_NAMES == MCP_VNEXT_TOOL_NAMES
    assert compact_names == MCP_VNEXT_TOOL_NAMES
    assert len(compact_names) == 27


@pytest.mark.asyncio
async def test_compact_registration_order_and_schema_inventory_are_frozen() -> None:
    tools = await create_mcp_server(_container()).list_tools()

    assert [tool.name for tool in tools] == [
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
        "broker_order_manage",
        "portfolio_analyze",
        "research_workflow_run",
        "watchlist_get",
        "watchlist_manage",
        "portfolio_risk_get",
        "risk_policy_update",
        "monitor_read",
        "monitor_manage",
        "monitor_evaluate",
    ]
    # Exact inventory bytes were captured before the registration split.
    assert sum(len(json.dumps(tool.inputSchema, separators=(",", ":"))) for tool in tools) == 25_728
    assert _wire_size(tools) == 35_951


@pytest.mark.asyncio
async def test_research_proposal_tool_documents_direct_instrument_attachment() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}

    description = tools["research_judgment_propose"].description
    assert "confirmed watchlist_item create attaches the Instrument directly" in description
    assert "do not require Shortlist or Select afterward" in description


@pytest.mark.asyncio
async def test_compact_v18_keeps_legacy_case_transport_discoverable_and_callable() -> None:
    container = _container()
    legacy_case_id = "case_00000000-0000-7000-8000-000000000001"
    container.services.research_subjects.get_subject.return_value = _Envelope(
        {
            "subject_id": legacy_case_id,
            "subject_type": "company",
            "linked_subject_ids": [],
            "title": "Copper cycle",
        }
    )
    registry = create_capability_registry(container)
    tools = {tool.name: tool for tool in registry.list_tools()}

    assert "Research Subjects (标的)" in tools["investment_case_read"].description
    assert "decision inbox" in tools["investment_case_read"].description
    assert "Legacy transport" in tools["investment_case_manage"].description
    serialized_manage_schema = json.dumps(
        tools["investment_case_manage"].inputSchema, separators=(",", ":")
    )
    assert '"case_type"' in serialized_manage_schema
    assert '"case_id"' in serialized_manage_schema
    assert '"linked_case_ids"' in serialized_manage_schema
    assert '"title"' in serialized_manage_schema
    manage_validator = Draft202012Validator(tools["investment_case_manage"].inputSchema)
    assert not list(
        manage_validator.iter_errors(
            {
                "request": {
                    "operation": "create",
                    "case_type": "company",
                    "title": "Copper cycle",
                    "summary": "Long-horizon copper supply and demand",
                    "confirmed_by": "user",
                    "idempotency_key": "legacy-create-1",
                }
            }
        )
    )

    result = await registry.invoke(
        "investment_case_read",
        {"request": {"operation": "query", "case_id": legacy_case_id}},
    )

    container.services.research_subjects.get_subject.assert_called_once_with(legacy_case_id)
    assert result["data"]["case_id"] == legacy_case_id
    assert result["data"]["case_type"] == "company"
    assert result["data"]["linked_case_ids"] == []
    assert "subject_id" not in result["data"]


@pytest.mark.asyncio
async def test_investment_case_read_attention_is_read_only() -> None:
    from datetime import UTC, datetime

    from application.dto.attention import AttentionDigestDTO, AttentionMetricsDTO

    container = _container()
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)
    container.context.clock.now.return_value = now
    container.context.id_generator.new.return_value = "req_attention"
    container.services.attention.list_digest.return_value = AttentionDigestDTO(
        generated_at=now,
        scope="global",
        total_count=0,
        returned_count=0,
        truncated=False,
        metrics=AttentionMetricsDTO(
            open_count=0,
            acknowledged_count=0,
            overdue_count=0,
            unknown_execution_count=0,
        ),
    )
    registry = create_capability_registry(container)
    result = await registry.invoke(
        "investment_case_read",
        {"request": {"operation": "attention"}},
    )
    assert result["ok"] is True
    assert result["data"]["mode"] == "durable_only_read"
    container.services.attention.list_digest.assert_called_once()
    container.services.review_items.reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_technical_tools_publish_canonical_interval_enums() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}

    snapshot = tools["technical_get_snapshot"].inputSchema["properties"]
    chart = tools["technical_render_chart"].inputSchema["properties"]

    assert snapshot["intervals"]["items"]["enum"] == ["1d", "1w"]
    assert chart["interval"]["enum"] == ["1d", "1w"]


@pytest.mark.asyncio
async def test_monitor_schema_exposes_technical_timeframe_and_hysteresis() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}
    schema = tools["monitor_manage"].inputSchema
    serialized = json.dumps(schema)

    assert '"technical_interval"' in serialized
    assert '"recovery_threshold"' in serialized
    validator = Draft202012Validator(schema)
    errors = list(
        validator.iter_errors(
            {
                "request": {
                    "operation": "create",
                    "name": "NVDA weekly RSI",
                    "cadence": "US_POST_MARKET",
                    "rules": [
                        {
                            "rule_code": "RSI_OVERSOLD",
                            "description": "Weekly RSI below 30; recover at 35.",
                            "rule_type": "FACT_COMPARISON",
                            "severity": "MEDIUM",
                            "instrument_id": "equity:US:NVDA",
                            "max_fact_age_seconds": 691200,
                            "fact_type": "TECHNICAL",
                            "metric_key": "rsi_14",
                            "comparator": "LT",
                            "numeric_threshold": "30",
                            "recovery_threshold": "35",
                            "technical_interval": "1w",
                        }
                    ],
                    "confirmed_by": "user",
                    "idempotency_key": "weekly-rsi-monitor",
                }
            }
        )
    )
    assert errors == []


@pytest.mark.asyncio
async def test_registry_and_mcp_transport_publish_identical_contracts() -> None:
    container = _container()
    registry_tools = {
        tool.name: tool.model_dump(mode="json")
        for tool in create_capability_registry(container).list_tools()
    }
    mcp_tools = {
        tool.name: tool.model_dump(mode="json")
        for tool in await create_mcp_server(container).list_tools()
    }

    assert registry_tools == mcp_tools


def test_registry_uses_explicit_confirmation_policy_not_read_only_hint() -> None:
    policies = create_capability_registry(_container()).policies

    assert policies["instrument_resolve"].annotations.readOnlyHint is False
    assert policies["instrument_resolve"].confirmation is ConfirmationPolicy.NONE
    assert policies["external_state_sync"].confirmation is ConfirmationPolicy.MATCH_CAPABILITY_NAME


@pytest.mark.asyncio
async def test_registry_and_mcp_transport_invoke_the_same_health_handler() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope()

    registry_result = await create_capability_registry(container).invoke(
        "system_health",
        {},
    )
    mcp_result = await create_mcp_server(container)._tool_manager.call_tool(
        "system_health",
        {},
    )

    assert registry_result == mcp_result


@pytest.mark.asyncio
async def test_technical_snapshot_description_discloses_cross_market_support() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}

    description = tools["technical_get_snapshot"].description
    assert "A-share" in description
    assert "US" in description
    assert "CME" in description
    assert "OTC" in description


@pytest.mark.asyncio
async def test_compact_grouped_tools_publish_closed_discriminated_request_unions() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}
    expected_variants = {
        "investment_case_read": 3,
        "external_state_sync": 3,
        "research_judgment_get": 4,
        "research_judgment_confirm": 2,
        "monitor_read": 4,
    }

    for name, variant_count in expected_variants.items():
        schema = tools[name].inputSchema
        assert schema["required"] == ["request"]
        request = schema["properties"]["request"]
        assert "discriminator" not in request
        assert request["required"][0] == "operation"
        assert request["unevaluatedProperties"] is False
        assert len(request["oneOf"]) == variant_count
        for variant in request["oneOf"]:
            assert "const" in variant["properties"]["operation"]
            assert "type" not in variant


@pytest.mark.asyncio
async def test_judgment_confirmation_schema_exposes_chat_authorization_provenance() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}
    serialized = json.dumps(tools["research_judgment_confirm"].inputSchema)

    assert '"candidate"' in serialized
    assert '"reviewed_by"' in serialized
    assert '"submitted_via"' in serialized
    assert '"authorization_note"' in serialized


@pytest.mark.asyncio
async def test_compact_wire_schema_and_each_tool_stay_bounded() -> None:
    compact = await create_mcp_server(_container()).list_tools()

    assert _wire_size(compact) <= 48 * 1024
    # Keep a reserve below the external 30 KiB ceiling so a tiny operation
    # addition cannot turn the next release into an emergency compression pass.
    assert (
        sum(len(json.dumps(tool.inputSchema, separators=(",", ":"))) for tool in compact)
        <= 29.5 * 1024
    )
    for tool in compact:
        assert len(json.dumps(tool.inputSchema, separators=(",", ":"))) <= 4.5 * 1024, tool.name


@pytest.mark.asyncio
async def test_compact_schema_compression_keeps_every_local_ref_resolvable() -> None:
    compact = await create_mcp_server(_container()).list_tools()

    for tool in compact:
        Draft202012Validator.check_schema(tool.inputSchema)
        definitions = tool.inputSchema.get("$defs", {})
        assert _local_definition_refs(tool.inputSchema) <= set(definitions), tool.name
    for tool in compact:
        assert all(len(name) == 1 for name in tool.inputSchema.get("$defs", {}))
        serialized = json.dumps(tool.inputSchema, separators=(",", ":"))
        assert '"anyOf":[{"enum":' not in serialized


@pytest.mark.asyncio
async def test_compact_public_schema_rejects_fields_from_other_operations() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}
    schema = tools["account_get"].inputSchema
    validator = Draft202012Validator(schema)

    assert not list(validator.iter_errors({"request": {"operation": "positions"}}))
    errors = list(
        validator.iter_errors(
            {"request": {"operation": "positions", "limit": 20}},
        )
    )
    assert errors

    registry = create_capability_registry(_container())
    resolve_invalid = await registry.invoke(
        "monitor_manage",
        {
            "request": {
                "operation": "resolve_event",
                "event_id": "event_1",
                "action": "RESOLVE",
                "note": "reviewed",
                "confirmed_by": "user",
                "idempotency_key": "resolve-1",
                "judgment_policy": {
                    "playbook": "must not belong to resolve_event",
                    "reference_instrument_ids": ["equity:US:NVDA"],
                },
            }
        },
        confirmation="monitor_manage",
    )
    assert resolve_invalid["errors"][0]["code"] == "TOOL_INPUT_INVALID"
    assert "judgment_policy" in resolve_invalid["errors"][0]["details"]["unexpected_fields"]

    scorecard_invalid = await registry.invoke(
        "research_workflow_run",
        {
            "request": {
                "operation": "judgment_scorecard",
                "case_id": "case_1",
                "thesis_id": "thesis_1",
                "idempotency_key": "scorecard-1",
                "start": "2026-08-01T00:00:00Z",
            }
        },
        confirmation="research_workflow_run",
    )
    assert scorecard_invalid["errors"][0]["code"] == "TOOL_INPUT_INVALID"
    assert "start" in scorecard_invalid["errors"][0]["details"]["unexpected_fields"]
    judgment_validator = Draft202012Validator(tools["research_judgment_get"].inputSchema)
    assert list(
        judgment_validator.iter_errors(
            {
                "request": {
                    "operation": "scorecard_history",
                    "limit": 20,
                    "idempotency_key": "must-not-leak-from-write",
                }
            }
        )
    )
    with pytest.raises(ToolError, match="payload"):
        await registry.invoke(
            "research_memory_get",
            {
                "request": {
                    "operation": "agenda",
                    "window_days": 30,
                    "payload": {"title": "write field"},
                }
            },
        )
    with pytest.raises(ToolError, match="window_days"):
        await registry.invoke(
            "research_memory_append",
            {
                "request": {
                    "operation": "agenda_item",
                    "action": "CANCEL",
                    "agenda_item_id": "agenda_1",
                    "expected_version": 1,
                    "confirmed_by": "user",
                    "authorization_note": "cancel",
                    "idempotency_key": "cancel-1",
                    "payload": {"cancellation_reason": "no longer relevant"},
                    "window_days": 30,
                }
            },
            confirmation="research_memory_append",
        )


@pytest.mark.asyncio
async def test_compact_annotations_distinguish_reads_sync_appends_and_destructive_manage() -> None:
    tools = {tool.name: tool for tool in await create_mcp_server(_container()).list_tools()}

    assert tools["account_get"].annotations.model_dump() == {
        "title": None,
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert tools["external_state_sync"].annotations.readOnlyHint is False
    assert tools["external_state_sync"].annotations.openWorldHint is True
    assert tools["instrument_resolve"].annotations.destructiveHint is False
    assert tools["research_memory_append"].annotations.destructiveHint is False
    assert tools["investment_case_manage"].annotations.destructiveHint is True
    assert tools["research_workflow_run"].annotations.readOnlyHint is False
    assert tools["research_workflow_run"].annotations.idempotentHint is True
    assert tools["broker_order_manage"].annotations.readOnlyHint is False
    assert tools["broker_order_manage"].annotations.destructiveHint is True
    assert tools["broker_order_manage"].annotations.openWorldHint is True


@pytest.mark.asyncio
async def test_broker_order_manage_keeps_shadow_preview_and_live_write_closed() -> None:
    container = _container()
    container.services.broker_order_preview.preview = AsyncMock(
        return_value=_Envelope({"shadow_only": True, "execution_effect": False})
    )
    container.services.broker_orders.preview = AsyncMock(
        return_value=_Envelope({"status": "PREVIEWED", "execution_effect": False})
    )
    registry = create_capability_registry(container)

    result = await registry.invoke(
        "broker_order_manage",
        {
            "request": {
                "operation": "cash_sweep_preview",
                "account_refs": [],
                "operational_buffer": "200",
            },
        },
        confirmation="broker_order_manage",
    )

    assert result["data"] == {"shadow_only": True, "execution_effect": False}
    request = container.services.broker_order_preview.preview.await_args.args[0]
    assert request.instrument_id == "etf:US:SGOV"
    assert request.hard_cash_floor == 2000

    exact = await registry.invoke(
        "broker_order_manage",
        {
            "request": {
                "operation": "preview",
                "account_ref": "schwab_account_1",
                "instrument_id": "etf:US:SGOV",
                "instruction": "BUY",
                "quantity": 26,
                "order_type": "LIMIT",
                "limit_price": "100.56",
                "idempotency_key": "preview-key",
            },
        },
        confirmation="broker_order_manage",
    )

    assert exact["data"] == {"status": "PREVIEWED", "execution_effect": False}
    exact_request = container.services.broker_orders.preview.await_args.args[0]
    assert exact_request.account_ref == "schwab_account_1"
    assert exact_request.quantity == 26
    assert str(exact_request.limit_price) == "100.56"
    assert "broker_order_manage" in MCP_VNEXT_TOOL_NAMES
    assert "broker_order_preview" not in MCP_VNEXT_TOOL_NAMES


@pytest.mark.asyncio
async def test_system_health_discloses_the_active_surface_profile() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope()
    result = await create_mcp_server(container)._tool_manager.call_tool("system_health", {})

    assert result["data"] == {
        "data_quality": {
            "component_checks": {},
            "component_check_limitations": [
                "CONFIGURATION_CHECK_IS_NOT_UPSTREAM_REACHABILITY",
                "ONLY_COMPONENTS_WITH_EXPLICIT_PROBES_ARE_LISTED",
            ],
        },
        "mcp_surface_profile": "mcp_vnext_shadow",
        "public_tool_count": 27,
        "surface_schema_version": "mcp-vnext-shadow-v2",
        "attention_summary": {
            "generated_at": "2026-08-17T12:00:00+00:00",
            "basis": "materialized_review_items",
            "live_projections_not_included": True,
            "open_review_item_count": 0,
            "acknowledged_review_item_count": 0,
            "catalyst_sync_receipt_missing": True,
            "coverage_status": "UNKNOWN",
        },
    }


@pytest.mark.asyncio
async def test_system_health_keeps_operational_and_data_quality_states_separate() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope(
        {"status": "ok", "components": {"provider": {"state": "ok"}}}
    )
    container.services.data_quality.check.return_value = _Envelope(
        {"status": "degraded", "issues": [{"code": "MONITOR_NEVER_EVALUATED"}]},
        degraded=True,
        warnings=[
            {
                "code": "DATA_QUALITY_ISSUES",
                "message": "Quality gap",
                "details": {},
            }
        ],
    )

    result = await create_mcp_server(container)._tool_manager.call_tool("system_health", {})

    assert result["degraded"] is False
    assert result["data"]["status"] == "ok"
    assert result["data"]["data_quality"]["status"] == "degraded"
    assert result["data"]["data_quality"]["component_checks"] == {"provider": {"state": "ok"}}
    assert result["data"]["attention_summary"]["basis"] == "materialized_review_items"
    assert result["data"]["attention_summary"]["live_projections_not_included"] is True
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_system_health_survives_data_quality_center_failure() -> None:
    container = _container()
    container.services.health.check.return_value = _Envelope({"status": "ok"})
    container.services.data_quality.check.side_effect = RuntimeError("token=secret")

    result = await create_mcp_server(container)._tool_manager.call_tool("system_health", {})

    assert result["ok"] is True
    assert result["degraded"] is False
    assert result["data"]["status"] == "ok"
    quality = result["data"]["data_quality"]
    assert quality["status"] == "error"
    assert quality["account_snapshots"] == []
    assert quality["issues"][0]["code"] == "DATA_QUALITY_CENTER_UNAVAILABLE"
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_performance_summary_routes_through_durable_attribution_service() -> None:
    container = _container()
    container.services.account_transactions.get_performance_attribution.return_value = _Envelope()

    result = await create_mcp_server(container)._tool_manager.call_tool(
        "portfolio_analyze",
        {
            "request": {
                "operation": "performance_summary",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-08-01T00:00:00Z",
                "cost_basis_method": "FIFO",
            }
        },
    )

    assert result["ok"] is True
    container.services.account_transactions.get_performance_attribution.assert_called_once()


@pytest.mark.asyncio
async def test_trade_retro_review_routes_through_existing_grouped_tool() -> None:
    container = _container()
    container.services.trade_retro.review.return_value = _Envelope({"version": 1})
    registry = create_capability_registry(container)
    request = {
        "operation": "trade_retro",
        "action": "review",
        "run_id": "retro_00000000-0000-7000-8000-000000000001",
        "expected_version": 0,
        "review_status": "DISPUTED",
        "note_markdown": "The immutable result needs human context.",
        "action_items": ["Record the next decision before execution."],
        "finding_reviews": [
            {
                "finding_key": f"finding_{'a' * 64}",
                "status": "DISPUTED",
                "note": "Evidence was recorded outside the system.",
            }
        ],
        "confirmed_by": "user",
        "authorization_note": "User saved the review in the local Console.",
        "idempotency_key": "review-v1",
    }

    result = await registry.invoke(
        "research_workflow_run",
        {"request": request},
        confirmation="research_workflow_run",
    )

    assert result["ok"] is True
    review_input = container.services.trade_retro.review.call_args.args[0]
    assert review_input.run_id == request["run_id"]
    assert review_input.expected_version == 0
    assert review_input.finding_reviews[0].finding_key == f"finding_{'a' * 64}"
    assert len(MCP_VNEXT_TOOL_NAMES) == 27


@pytest.mark.asyncio
async def test_judgment_scorecard_run_and_history_route_without_new_public_tools() -> None:
    container = _container()
    container.services.scorecards.run.return_value = _Envelope({"scorecard_id": "scorecard_1"})
    container.services.scorecards.history.return_value = _Envelope(
        {"runs": [], "total": 0, "has_more": False}
    )
    registry = create_capability_registry(container)

    run_result = await registry.invoke(
        "research_workflow_run",
        {
            "request": {
                "operation": "judgment_scorecard",
                "case_id": "case_00000000-0000-7000-8000-000000000001",
                "thesis_id": "thesis_00000000-0000-7000-8000-000000000001",
                "idempotency_key": "scorecard-run-1",
            }
        },
        confirmation="research_workflow_run",
    )
    history_result = await registry.invoke(
        "research_judgment_get",
        {
            "request": {
                "operation": "scorecard_history",
                "case_id": "case_00000000-0000-7000-8000-000000000001",
                "limit": 12,
                "offset": 2,
            }
        },
    )

    assert run_result["ok"] is True
    assert history_result["ok"] is True
    container.services.scorecards.run.assert_called_once_with(
        subject_id="case_00000000-0000-7000-8000-000000000001",
        thesis_id="thesis_00000000-0000-7000-8000-000000000001",
        idempotency_key="scorecard-run-1",
    )
    history_input = container.services.scorecards.history.call_args.args[0]
    assert history_input.subject_id == "case_00000000-0000-7000-8000-000000000001"
    assert history_input.thesis_id is None
    assert history_input.limit == 12
    assert history_input.offset == 2
    assert len(MCP_VNEXT_TOOL_NAMES) == 27


@pytest.mark.asyncio
async def test_catalyst_agenda_read_and_confirmed_append_reuse_memory_tools() -> None:
    container = _container()
    container.services.catalyst_agenda.query.return_value = _Envelope({"items": [], "coverage": []})
    container.services.catalyst_agenda.manage.return_value = _Envelope(
        {"agenda_item_id": "agenda_1", "version": 1}
    )
    registry = create_capability_registry(container)

    read_result = await registry.invoke(
        "research_memory_get",
        {
            "request": {
                "operation": "agenda",
                "window_days": 30,
                "filters": {"case_ids": ["case_1"]},
                "limit": 25,
            }
        },
    )
    write_result = await registry.invoke(
        "research_memory_append",
        {
            "request": {
                "operation": "agenda_item",
                "action": "CREATE",
                "confirmed_by": "user",
                "authorization_note": "User created this agenda item in Codex chat.",
                "idempotency_key": "agenda-create-1",
                "submitted_via": "codex_chat",
                "payload": {
                    "case_id": "case_1",
                    "kind": "USER_DEFINED",
                    "title": "Review product launch evidence",
                    "date_certainty": "UNKNOWN",
                    "expected_question": "Did adoption improve after launch?",
                },
            }
        },
        confirmation="research_memory_append",
    )
    outcome_result = await registry.invoke(
        "research_memory_append",
        {
            "request": {
                "operation": "agenda_item",
                "action": "LINK_OUTCOME",
                "agenda_item_id": "agenda_1",
                "expected_version": 1,
                "confirmed_by": "user",
                "authorization_note": "User linked the observed outcome in Codex chat.",
                "idempotency_key": "agenda-outcome-1",
                "submitted_via": "codex_chat",
                "payload": {
                    "evidence_id": "evidence_1",
                    "outcome_occurred_at": "2026-08-09T08:00:00Z",
                    "outcome_note": "Observed result linked after review.",
                },
            }
        },
        confirmation="research_memory_append",
    )

    assert read_result["ok"] is True
    assert write_result["ok"] is True
    assert outcome_result["ok"] is True
    query_input = container.services.catalyst_agenda.query.call_args.args[0]
    assert query_input.window_days == 30
    assert query_input.filters.subject_ids == ("case_1",)
    manage_calls = container.services.catalyst_agenda.manage.call_args_list
    create_input = manage_calls[0].args[0]
    assert create_input.action.value == "CREATE"
    assert create_input.payload.title == "Review product launch evidence"
    manage_input = manage_calls[1].args[0]
    assert manage_input.action.value == "LINK_OUTCOME"
    assert manage_input.payload.evidence_id == "evidence_1"
    actor_context = container.services.catalyst_agenda.manage.call_args.kwargs["actor_context"]
    assert actor_context is not None
    assert len(MCP_VNEXT_TOOL_NAMES) == 27


@pytest.mark.asyncio
async def test_durable_account_and_watchlist_reads_cannot_refresh_upstreams() -> None:
    container = _container()
    container.services.portfolio.get_account_positions.return_value = _Envelope()
    container.services.portfolio.get_account_snapshot = AsyncMock(return_value=_Envelope())
    container.services.watchlist.get_items = AsyncMock(return_value=_Envelope())
    manager = create_mcp_server(container)._tool_manager

    account_result = await manager.call_tool("account_get", {"request": {"operation": "positions"}})
    watchlist_result = await manager.call_tool(
        "watchlist_get",
        {"request": {"operation": "items"}},
    )

    assert account_result["ok"] is True
    assert watchlist_result["ok"] is True
    container.services.portfolio.get_account_snapshot.assert_not_awaited()
    request = container.services.watchlist.get_items.await_args.args[0]
    assert request.refresh is False


@pytest.mark.asyncio
async def test_account_transactions_read_is_durable_only() -> None:
    container = _container()
    container.services.account_transactions.list_durable_transactions.return_value = _Envelope()
    container.services.account_transactions.get_transactions = AsyncMock(return_value=_Envelope())

    result = await create_mcp_server(container)._tool_manager.call_tool(
        "account_get",
        {"request": {"operation": "transactions", "limit": 20}},
    )

    assert result["ok"] is True
    container.services.account_transactions.list_durable_transactions.assert_called_once()
    container.services.account_transactions.get_transactions.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_state_sync_refreshes_accounts_and_watchlist_only_when_selected() -> None:
    container = _container()
    container.services.portfolio.get_account_snapshot = AsyncMock(return_value=_Envelope())
    container.services.watchlist.sync_all = AsyncMock(return_value=_Envelope())
    manager = create_mcp_server(container)._tool_manager

    accounts_result = await manager.call_tool(
        "external_state_sync",
        {"request": {"operation": "accounts"}},
    )
    watchlist_result = await manager.call_tool(
        "external_state_sync",
        {"request": {"operation": "watchlist"}},
    )

    assert accounts_result["ok"] is True
    assert watchlist_result["ok"] is True
    container.services.portfolio.get_account_snapshot.assert_awaited_once()
    container.services.watchlist.sync_all.assert_awaited_once_with()
