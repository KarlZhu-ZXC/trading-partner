"""Compact MCP surface inventory, schema, and explicit-sync boundary tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import Draft202012Validator
from mcp.server.fastmcp.exceptions import ToolError

from application.dto.view_review import ViewInboxDTO
from application.services.attention_projection import next_read_for
from domain.attention.enums import AttentionSourceType
from interfaces.mcp.progressive import READ_TOOL_NAME, WRITE_TOOL_NAME
from interfaces.mcp.server import (
    MCP_VNEXT_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_capability_registry,
    create_mcp_server,
)
from interfaces.mcp.tool_inventory import DIRECT_PUBLIC_TOOL_NAMES
from interfaces.mcp.tools.compact import (
    READ_DURABLE,
    CapabilityConfirmationRequiredError,
    CompactCapabilityRegistry,
    ConfirmationPolicy,
    _minimize_public_schema,
)
from mcp_helpers import routed_mcp_server


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


def test_attention_next_reads_match_the_current_exact_public_schemas() -> None:
    registry = create_capability_registry(_container())
    requests = tuple(
        item
        for source_type, source_ref, subject_id in (
            (AttentionSourceType.RESEARCH_CANDIDATE, "candidate_1", "case_1"),
            (AttentionSourceType.CATALYST_AGENDA, "agenda_1", "case_1"),
            (AttentionSourceType.TRADE_RETRO, "retro_1", "case_1"),
            (AttentionSourceType.SCORECARD_GAP, "thesis_1", "case_1"),
            (AttentionSourceType.MONITOR_BLIND_SPOT, "monitor_1", None),
            (AttentionSourceType.BROKER_ORDER_INTENT, "order_1", None),
            (AttentionSourceType.DATA_QUALITY, "ACCOUNT_SNAPSHOT_DEGRADED", None),
        )
        if (
            item := next_read_for(
                source_type,
                source_ref=source_ref,
                subject_id=subject_id,
            )
        )
        is not None
    )
    assert len(requests) == 7
    for item in requests:
        operation = item.request.get("operation")
        descriptor = registry.find_operation(
            item.tool,
            operation if isinstance(operation, str) else None,
        )
        errors = tuple(Draft202012Validator(descriptor.exact_schema).iter_errors(item.request))
        assert errors == (), (item.tool, item.request, errors)


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

    expected_public_names = DIRECT_PUBLIC_TOOL_NAMES | {
        "capability_discover",
        "capability_read",
        "capability_write",
    }
    assert MCP_VNEXT_TOOL_NAMES != PUBLIC_TOOL_NAMES
    assert expected_public_names == PUBLIC_TOOL_NAMES
    assert compact_names == PUBLIC_TOOL_NAMES


@pytest.mark.asyncio
async def test_compact_registration_order_and_schema_inventory_are_frozen() -> None:
    tools = await create_mcp_server(_container()).list_tools()

    assert [tool.name for tool in tools] == [
        "instrument_resolve",
        "research_judgment_propose",
        "research_judgment_confirm",
        "technical_render_chart",
        "external_state_sync",
        "broker_order_manage",
        "capability_discover",
        "capability_read",
        "capability_write",
    ]
    # The MCP entry point intentionally publishes only a small first page. Full
    # operation schemas remain in the transport-neutral registry for Console and
    # contract checks; capability discovery returns one of them on demand.
    schema_bytes = sum(len(json.dumps(tool.inputSchema, separators=(",", ":"))) for tool in tools)
    assert schema_bytes <= 2 * 1024
    assert _wire_size(tools) <= 5 * 1024


@pytest.mark.asyncio
async def test_capability_discover_returns_one_exact_operation_schema() -> None:
    container = _container()
    server = create_mcp_server(container)
    result = await server._tool_manager.call_tool(
        "capability_discover",
        {"tool": "portfolio_get", "operation": "positions"},
    )

    payload = result
    assert payload["tool"] == "portfolio_get"
    assert payload["operation"] == "positions"
    descriptor = create_capability_registry(container).find_operation(
        "portfolio_get", "positions"
    )
    assert payload["inputSchema"]["properties"]["request"] == descriptor.exact_schema
    assert "operations" not in payload
    assert "schemas" not in payload


@pytest.mark.asyncio
async def test_discovery_covers_all_106_exact_operations_without_invoking_services() -> None:
    container = _container()
    registry = create_capability_registry(container)
    server = create_mcp_server(container)
    container.services.reset_mock()
    descriptors = registry.operation_descriptors()
    assert len(descriptors) == 106
    for descriptor in descriptors:
        arguments = {"tool": descriptor.capability}
        if descriptor.operation is not None:
            arguments["operation"] = descriptor.operation
        result = await server._tool_manager.call_tool("capability_discover", arguments)
        schema = result["inputSchema"]
        Draft202012Validator.check_schema(schema)
        assert _local_definition_refs(schema) <= set(schema.get("$defs", {}))
        expected = descriptor.exact_schema
        if descriptor.operation is None:
            assert schema == expected
        else:
            definitions = expected.pop("$defs", None)
            assert schema["properties"]["request"] == expected
            assert schema.get("$defs") == definitions
        assert result["effect"] == descriptor.policy.effect.value
        assert result["confirmation"] == descriptor.policy.confirmation.value
        if descriptor.capability in DIRECT_PUBLIC_TOOL_NAMES:
            assert result["call_tool"] == descriptor.capability
        elif descriptor.policy.confirmation_required:
            assert result["call_tool"] == WRITE_TOOL_NAME
        else:
            assert result["call_tool"] == READ_TOOL_NAME
    assert container.services.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ("historical_validation_prepare", "historical_validation_import")
)
async def test_retired_backtest_operations_are_absent_and_cannot_dispatch(operation: str) -> None:
    container = _container()
    server = routed_mcp_server(container)
    registry_tools = {
        tool.name: tool for tool in create_capability_registry(container).list_tools()
    }
    schema = registry_tools["research_workflow_run"].inputSchema
    arguments = {"request": {"operation": operation}}
    assert operation not in json.dumps(schema)
    assert list(Draft202012Validator(schema).iter_errors(arguments))

    with pytest.raises(ToolError):
        await create_capability_registry(container).invoke(
            "research_workflow_run", arguments, confirmation="research_workflow_run"
        )
    with pytest.raises(ToolError):
        await server._tool_manager.call_tool("research_workflow_run", arguments)
    assert container.services.mock_calls == []


@pytest.mark.asyncio
async def test_intent_first_view_tools_are_bounded_durable_reads() -> None:
    container = _container()
    container.context.id_generator.new.return_value = "req_view_inbox"
    container.context.clock.now.return_value = datetime(2026, 9, 3, 12, tzinfo=UTC)
    container.context.secret_redactor.redact_text.side_effect = lambda value: value
    container.services.view_reviews.inbox.return_value = ViewInboxDTO(
        items=(),
        returned_count=0,
        has_more=False,
    )
    registry = create_capability_registry(container)

    result = await registry.invoke(
        "view_get",
        {"request": {"operation": "inbox", "limit": 10}},
    )

    assert result["ok"] is True
    assert result["data"] == {"items": [], "returned_count": 0, "has_more": False}
    descriptor = registry.find_operation("view_get", "inbox")
    assert descriptor.policy is READ_DURABLE
    assert descriptor.arguments_schema["properties"]["limit"]["maximum"] == 100


@pytest.mark.asyncio
async def test_escalated_view_review_is_explicit_and_never_a_read_side_effect() -> None:
    container = _container()
    container.context.id_generator.new.return_value = "req_deep_review"
    container.context.clock.now.return_value = datetime(2026, 9, 3, 12, tzinfo=UTC)
    container.context.secret_redactor.redact_text.side_effect = lambda value: value
    container.services.external_note_review_drafts.review = AsyncMock(return_value=None)
    registry = create_capability_registry(container)

    with pytest.raises(CapabilityConfirmationRequiredError):
        await registry.invoke(
            "research_workflow_run",
            {
                "request": {
                    "operation": "evaluate_view",
                    "note_revision_id": "external_note_revision_test",
                }
            },
        )
    result = await registry.invoke(
        "research_workflow_run",
        {
            "request": {
                "operation": "evaluate_view",
                "note_revision_id": "external_note_revision_test",
            }
        },
        confirmation="research_workflow_run",
    )

    assert result["ok"] is True
    assert result["data"] == {
        "note_revision_id": "external_note_revision_test",
        "status": "NOT_REQUIRED_OR_NOT_CONFIGURED",
    }
    container.services.external_note_review_drafts.review.assert_awaited_once_with(
        "external_note_revision_test",
        explicit_review=True,
        force=False,
    )


@pytest.mark.asyncio
async def test_research_proposal_tool_documents_direct_instrument_attachment() -> None:
    tools = {tool.name: tool for tool in create_capability_registry(_container()).list_tools()}

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

    assert "Research Subjects (标的)" in tools["research_get"].description
    assert "decision inbox" in tools["research_get"].description
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
        "research_get",
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
        "research_get",
        {"request": {"operation": "attention"}},
    )
    assert result["ok"] is True
    assert result["data"]["mode"] == "durable_only_read"
    container.services.attention.list_digest.assert_called_once()
    container.services.review_items.reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_technical_tools_publish_canonical_interval_enums() -> None:
    tools = {tool.name: tool for tool in create_capability_registry(_container()).list_tools()}

    snapshot = tools["technical_get_snapshot"].inputSchema["properties"]
    chart = tools["technical_render_chart"].inputSchema["properties"]

    assert snapshot["intervals"]["items"]["enum"] == ["1d", "1w"]
    assert chart["interval"]["enum"] == ["1d", "1w"]


@pytest.mark.asyncio
async def test_monitor_schema_exposes_technical_timeframe_and_hysteresis() -> None:
    tools = {tool.name: tool for tool in create_capability_registry(_container()).list_tools()}
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
async def test_registry_and_mcp_transport_preserve_identity_while_thinning_schemas() -> None:
    container = _container()
    registry_tools = {
        tool.name: tool for tool in create_capability_registry(container).list_tools()
    }
    mcp_tools = {tool.name: tool for tool in await create_mcp_server(container).list_tools()}

    assert set(mcp_tools) == set(PUBLIC_TOOL_NAMES)
    assert set(registry_tools) == set(MCP_VNEXT_TOOL_NAMES)
    for name, full in registry_tools.items():
        if name not in DIRECT_PUBLIC_TOOL_NAMES:
            continue
        thin = mcp_tools[name]
        assert thin.name == full.name
        assert thin.title == full.title
        assert thin.annotations == full.annotations
        full_size = len(json.dumps(full.inputSchema, separators=(",", ":")))
        thin_size = len(json.dumps(thin.inputSchema, separators=(",", ":")))
        if "request" in full.inputSchema.get("properties", {}):
            assert thin_size < full_size

    grouped = {
        name
        for name, tool in registry_tools.items()
        if "oneOf" in json.dumps(tool.inputSchema, separators=(",", ":"))
    }
    assert grouped
    grouped_direct = grouped & DIRECT_PUBLIC_TOOL_NAMES
    assert grouped_direct
    assert any(
        len(json.dumps(mcp_tools[name].inputSchema, separators=(",", ":")))
        < len(json.dumps(registry_tools[name].inputSchema, separators=(",", ":")))
        for name in grouped_direct
    )


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
    mcp_result = await routed_mcp_server(container)._tool_manager.call_tool(
        "system_health",
        {},
    )

    assert registry_result == mcp_result


@pytest.mark.asyncio
async def test_local_uncompacted_invocation_keeps_validated_full_result() -> None:
    async def large_read(request: dict[str, Any]) -> dict[str, Any]:
        assert request == {"operation": "items"}
        return {
            "ok": True,
            "data": {
                "items": [{"item_id": f"item_{index}", "detail": "x" * 1000} for index in range(40)]
            },
        }

    registry = CompactCapabilityRegistry()
    registry.add_capability(
        large_read,
        name="large_read",
        description="large durable read",
        policy=READ_DURABLE,
    )

    compacted = await registry.invoke("large_read", {"request": {"operation": "items"}})
    full = await registry.invoke_uncompacted("large_read", {"request": {"operation": "items"}})

    assert compacted["_truncated"] is True
    assert len(full["data"]["items"]) == 40
    assert all("_truncated" not in item for item in full["data"]["items"])


@pytest.mark.asyncio
async def test_technical_snapshot_description_discloses_cross_market_support() -> None:
    tools = {tool.name: tool for tool in create_capability_registry(_container()).list_tools()}

    description = tools["technical_get_snapshot"].description
    assert "A-share" in description
    assert "US" in description
    assert "CME" in description
    assert "OTC" in description


@pytest.mark.asyncio
async def test_compact_grouped_tools_publish_closed_discriminated_request_unions() -> None:
    tools = {tool.name: tool for tool in create_capability_registry(_container()).list_tools()}
    expected_variants = {
        "external_state_sync": 3,
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
async def test_console_catalog_lists_all_merged_query_operations() -> None:
    from interfaces.console.catalog import capability_catalog

    container = _container()
    registry = create_capability_registry(container)
    tools = registry.list_tools()
    catalog = {item["name"]: item for item in capability_catalog(tools, registry.policies)}
    assert "capability_discover" not in catalog
    for name, count in (("research_get", 11), ("portfolio_get", 15), ("us_get_facts", 10)):
        expected = {
            descriptor.operation
            for descriptor in registry.operation_descriptors()
            if descriptor.capability == name
        }
        assert len(expected) == count
        assert set(catalog[name]["operations"]) == expected


@pytest.mark.asyncio
async def test_judgment_confirmation_schema_exposes_chat_authorization_provenance() -> None:
    tools = {tool.name: tool for tool in create_capability_registry(_container()).list_tools()}
    serialized = json.dumps(tools["research_judgment_confirm"].inputSchema)

    assert '"candidate"' in serialized
    assert '"reviewed_by"' in serialized
    assert '"submitted_via"' in serialized
    assert '"authorization_note"' in serialized


@pytest.mark.asyncio
async def test_compact_wire_schema_and_each_tool_stay_bounded() -> None:
    compact = await create_mcp_server(_container()).list_tools()

    assert _wire_size(compact) <= 5 * 1024
    for tool in compact:
        assert len(json.dumps(tool.inputSchema, separators=(",", ":"))) <= 2 * 1024, tool.name


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
    container = _container()
    server = create_mcp_server(container)
    registry = create_capability_registry(container)
    tools = {tool.name: tool for tool in registry.list_tools()}
    schema = tools["external_state_sync"].inputSchema
    validator = Draft202012Validator(schema)

    assert not list(validator.iter_errors({"request": {"operation": "accounts"}}))
    errors = list(
        validator.iter_errors(
            {"request": {"operation": "accounts", "limit": 20}},
        )
    )
    assert errors

    with pytest.raises(ToolError):
        await server._tool_manager.call_tool(
            "external_state_sync",
            {"request": {"operation": "accounts", "limit": 20}},
        )

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
    judgment_validator = Draft202012Validator(tools["research_get"].inputSchema)
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
            "research_get",
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

    assert tools["capability_read"].annotations.model_dump() == {
        "title": None,
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    assert tools["capability_write"].annotations.model_dump() == {
        "title": None,
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    assert tools["external_state_sync"].annotations.readOnlyHint is False
    assert tools["external_state_sync"].annotations.openWorldHint is True
    assert tools["instrument_resolve"].annotations.destructiveHint is False
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
    assert request.hard_cash_floor == 3000

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
    result = await routed_mcp_server(container)._tool_manager.call_tool("system_health", {})

    assert result["data"] == {
        "data_quality": {
            "component_checks": {},
            "component_check_limitations": [
                "CONFIGURATION_CHECK_IS_NOT_UPSTREAM_REACHABILITY",
                "ONLY_COMPONENTS_WITH_EXPLICIT_PROBES_ARE_LISTED",
            ],
        },
        "mcp_surface_profile": "mcp_vnext_shadow",
        "public_tool_count": len(PUBLIC_TOOL_NAMES),
        "surface_schema_version": "mcp-vnext-shadow-v11",
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

    result = await routed_mcp_server(container)._tool_manager.call_tool("system_health", {})

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

    result = await routed_mcp_server(container)._tool_manager.call_tool("system_health", {})

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

    result = await routed_mcp_server(container)._tool_manager.call_tool(
        "portfolio_get",
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
async def test_trade_cycles_routes_through_durable_transaction_projection() -> None:
    container = _container()
    container.services.account_transactions.get_trade_cycles.return_value = _Envelope()

    result = await routed_mcp_server(container)._tool_manager.call_tool(
        "portfolio_get",
        {
            "request": {
                "operation": "trade_cycles",
                "account_refs": ["account_1"],
                "instrument_ids": ["equity:US:NVDA"],
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-08-01T00:00:00Z",
                "limit": 25,
            }
        },
    )

    assert result["ok"] is True
    container.services.account_transactions.get_trade_cycles.assert_called_once()


@pytest.mark.asyncio
async def test_behavior_summary_routes_aware_date_window_to_durable_calculator() -> None:
    container = _container()
    container.services.account_transactions.get_behavior_summary.return_value = _Envelope()

    result = await routed_mcp_server(container)._tool_manager.call_tool(
        "portfolio_get",
        {
            "request": {
                "operation": "behavior_summary",
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-31T23:59:59Z",
            }
        },
    )

    assert result["ok"] is True
    request = container.services.account_transactions.get_behavior_summary.call_args.args[0]
    assert request.start == datetime(2026, 7, 1, tzinfo=UTC)
    assert request.end == datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)


@pytest.mark.asyncio
async def test_behavior_summary_no_longer_accepts_a_minimum_sample_policy() -> None:
    container = _container()
    server = routed_mcp_server(container)
    tools = {
        tool.name: tool for tool in create_capability_registry(container).list_tools()
    }
    assert "minimum_sample_size" not in json.dumps(tools["portfolio_get"].inputSchema)
    with pytest.raises(ToolError, match="minimum_sample_size"):
        await server._tool_manager.call_tool(
            "portfolio_get",
            {"request": {"operation": "behavior_summary", "minimum_sample_size": 3}},
        )
    container.services.account_transactions.get_behavior_summary.assert_not_called()


@pytest.mark.asyncio
async def test_performance_series_routes_through_durable_return_calculator() -> None:
    container = _container()
    container.services.account_transactions.get_performance_series.return_value = _Envelope()

    result = await routed_mcp_server(container)._tool_manager.call_tool(
        "portfolio_get",
        {
            "request": {
                "operation": "performance_series",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-08-01T00:00:00Z",
                "account_refs": ["account_1"],
            }
        },
    )

    assert result["ok"] is True
    container.services.account_transactions.get_performance_series.assert_called_once()


@pytest.mark.asyncio
async def test_journal_timeline_routes_complete_durable_chain_without_provider_reads() -> None:
    container = _container()
    now = datetime(2026, 8, 21, 12, tzinfo=UTC)
    container.context.clock.now.return_value = now
    container.context.id_generator.new.return_value = "req_journal_timeline"
    container.services.research_timeline.get_timeline.return_value = SimpleNamespace(
        ok=True, data=SimpleNamespace(items=())
    )
    container.services.account_transactions.list_durable_transactions.return_value = (
        SimpleNamespace(ok=True, data=SimpleNamespace(transactions=()))
    )
    container.services.activity_annotations.list_annotations.return_value = ()
    container.services.broker_orders.list_recent.return_value = ()

    result = await routed_mcp_server(container)._tool_manager.call_tool(
        "portfolio_get",
        {
            "request": {
                "operation": "journal_timeline",
                "case_id": "case_00000000-0000-7000-8000-000000000001",
                "instrument_id": "equity:US:NVDA",
                "limit": 50,
            }
        },
    )

    assert result["ok"] is True
    assert result["data"]["items"] == []
    container.services.broker_orders.list_recent.assert_called_once_with(limit=50)


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
    assert "view_get" in MCP_VNEXT_TOOL_NAMES


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
        "research_get",
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
    assert "view_get" in MCP_VNEXT_TOOL_NAMES


@pytest.mark.asyncio
async def test_catalyst_agenda_read_and_confirmed_append_reuse_memory_tools() -> None:
    container = _container()
    container.services.catalyst_agenda.query.return_value = _Envelope({"items": [], "coverage": []})
    container.services.catalyst_agenda.manage.return_value = _Envelope(
        {"agenda_item_id": "agenda_1", "version": 1}
    )
    registry = create_capability_registry(container)

    read_result = await registry.invoke(
        "research_get",
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
    assert "view_get" in MCP_VNEXT_TOOL_NAMES


@pytest.mark.asyncio
async def test_durable_account_and_watchlist_reads_cannot_refresh_upstreams() -> None:
    container = _container()
    container.services.portfolio.get_account_positions.return_value = _Envelope()
    container.services.portfolio.get_account_snapshot = AsyncMock(return_value=_Envelope())
    container.services.watchlist.get_items = AsyncMock(return_value=_Envelope())
    manager = routed_mcp_server(container)._tool_manager

    account_result = await manager.call_tool(
        "portfolio_get", {"request": {"operation": "positions"}}
    )
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

    result = await routed_mcp_server(container)._tool_manager.call_tool(
        "portfolio_get",
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
    manager = routed_mcp_server(container)._tool_manager

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
