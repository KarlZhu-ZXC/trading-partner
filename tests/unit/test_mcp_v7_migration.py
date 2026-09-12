"""Regression contracts for the MCP v7 grouped capability migration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.us_context import DEFAULT_MACRO_SERIES
from interfaces.mcp.server import create_capability_registry
from interfaces.mcp.tools.compact import CapabilityNotFoundError

_CONTRACTS_PATH = Path(__file__).parents[1] / "fixtures" / "mcp_v7_contracts.json"


class _Envelope:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {
            "ok": True,
            "request_id": "req_mcp_v7",
            "market": None,
            "as_of": None,
            "fetched_at": None,
            "freshness": "unknown",
            "sources": [],
            "degraded": False,
            "data": self._data,
            "warnings": [],
            "errors": [],
        }


def _container() -> MagicMock:
    container = MagicMock()
    container.settings = SimpleNamespace(mcp_server_name="Trading Partner Test")
    container.services = MagicMock()
    return container


def _contracts() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(_CONTRACTS_PATH.read_text(encoding="utf-8")),
    )


def _without_titles_and_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_titles_and_refs(item)
            for key, item in sorted(value.items())
            if key != "title"
        }
    if isinstance(value, list):
        return [_without_titles_and_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return "#/$defs/REF"
    return value


def _normalize_schema(value: Any, aliases: dict[str, str] | None = None) -> Any:
    if isinstance(value, list):
        return [_normalize_schema(item, aliases) for item in value]
    if isinstance(value, str):
        if aliases is not None and value.startswith("#/$defs/"):
            return f"#/$defs/{aliases.get(value.rsplit('/', 1)[-1], 'REF')}"
        return value
    if not isinstance(value, dict):
        return value

    definitions = value.get("$defs")
    local_aliases = aliases
    if isinstance(definitions, dict):
        signatures = sorted(
            (
                json.dumps(
                    _without_titles_and_refs(body),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                name,
            )
            for name, body in definitions.items()
        )
        local_aliases = {name: f"D{index}" for index, (_, name) in enumerate(signatures)}

    normalized: dict[str, Any] = {}
    for key in sorted(value):
        if key == "title":
            continue
        if key == "$defs" and isinstance(value[key], dict):
            assert local_aliases is not None
            normalized[key] = {
                local_aliases[name]: _normalize_schema(body, local_aliases)
                for name, body in sorted(
                    value[key].items(), key=lambda item: local_aliases[item[0]]
                )
            }
        else:
            normalized[key] = _normalize_schema(value[key], local_aliases)
    return normalized


def _contract_sha256(schema: dict[str, Any], arguments_schema: dict[str, Any]) -> str:
    value = {
        "schema": _normalize_schema(schema),
        "arguments_schema": _normalize_schema(arguments_schema),
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_v7_public_inventory_renames_only_the_three_approved_groups() -> None:
    contracts = _contracts()
    registry = create_capability_registry(_container())

    assert [tool.name for tool in registry.list_tools()] == contracts["public_tool_names"]
    assert set(registry.policies) == set(contracts["public_tool_names"])
    assert set(contracts["stable_tool_names"]).issubset(registry.policies)
    assert set(contracts["removed_tool_names"]).isdisjoint(registry.policies)


def test_v7_retains_every_operation_schema_default_and_policy() -> None:
    contracts = _contracts()
    registry = create_capability_registry(_container())
    descriptors = registry.operation_descriptors()
    expected_operations = contracts["operations"]

    assert contracts["operation_count"] == 106
    assert len(expected_operations) == contracts["operation_count"]
    assert len({(item[0], item[1]) for item in expected_operations}) == 106
    assert len(descriptors) == contracts["operation_count"]

    actual = {
        (descriptor.capability, descriptor.operation): descriptor for descriptor in descriptors
    }
    expected_keys = {(item[2], item[1]) for item in expected_operations}
    assert set(actual) == expected_keys

    for old_capability, operation, new_capability, schema_sha256, policy in expected_operations:
        descriptor = actual[(new_capability, operation)]
        assert (
            _contract_sha256(descriptor.exact_schema, descriptor.arguments_schema)
            == schema_sha256
        )
        exception_key = (
            f"{old_capability}:{operation}" if operation is not None else old_capability
        )
        if exception_key in contracts["schema_exceptions"]:
            exception = contracts["schema_exceptions"][exception_key]
            assert schema_sha256 == exception["current_contract_sha256"]
        assert [
            descriptor.effect.value,
            descriptor.confirmation_required,
            descriptor.auto_allowed,
            descriptor.direct,
        ] == policy
        annotations = descriptor.policy.annotations.model_dump(mode="json")
        annotations.pop("title", None)
        assert annotations == contracts["policy_annotations"][policy[0]]
        if old_capability != new_capability:
            with pytest.raises(CapabilityNotFoundError):
                registry.find_operation(old_capability, operation)


def test_v7_portfolio_get_reserves_discriminator_and_forwards_adapter_operation() -> None:
    contracts = _contracts()
    registry = create_capability_registry(_container())
    portfolio_tool = next(tool for tool in registry.list_tools() if tool.name == "portfolio_get")
    request_schema = portfolio_tool.inputSchema["properties"]["request"]
    portfolio_operations = [
        item[1] for item in contracts["operations"] if item[2] == "portfolio_get"
    ]

    assert request_schema["properties"]["operation"]["enum"] == portfolio_operations
    assert request_schema["required"] == ["operation"]

    exact_schema = registry.find_operation(
        "portfolio_get", "trade_cycle_override_preview"
    ).exact_schema
    assert exact_schema["properties"]["operation"] == {
        "const": "trade_cycle_override_preview"
    }
    assert exact_schema["properties"]["override_operation"] == {"type": "string"}
    assert "override_operation" in exact_schema["required"]

    translated = registry.validate_operation(
        "portfolio_get",
        "trade_cycle_override_preview",
        {
            "root_cycle_id": "cycle_1",
            "override_operation": "SPLIT",
            "cycle_ids": ["cycle_1"],
        },
    )
    assert translated["operation"] == "SPLIT"
    assert "override_operation" not in translated


def test_v7_merged_validation_preserves_defaults_none_and_paging() -> None:
    registry = create_capability_registry(_container())

    query_default = registry.validate_operation("research_get", "query", {})
    query_none = registry.validate_operation("research_get", "query", {"case_id": None})
    assert query_default == query_none
    assert query_default["include_archived"] is False
    assert query_default["limit"] == 50
    assert query_default["offset"] == 0
    assert registry.validate_operation(
        "research_get", "query", {"limit": 7, "offset": 11}
    )["limit"] == 7
    assert registry.validate_operation(
        "research_get", "query", {"limit": 7, "offset": 11}
    )["offset"] == 11

    positions_default = registry.validate_operation("portfolio_get", "positions", {})
    positions_none = registry.validate_operation(
        "portfolio_get", "positions", {"snapshot_id": None}
    )
    assert positions_default == positions_none == {
        "snapshot_id": None,
        "operation": "positions",
    }

    macro_default = registry.validate_operation("us_get_facts", "macro", {})
    macro_none = registry.validate_operation("us_get_facts", "macro", {"as_of": None})
    assert macro_default == macro_none
    assert macro_default["series_ids"] == tuple(DEFAULT_MACRO_SERIES)
    assert macro_default["lookback_days"] == 365


@pytest.mark.asyncio
async def test_v7_merged_dispatch_forwards_defaults_none_and_paging() -> None:
    container = _container()
    container.services.research_subjects.list_subjects.return_value = _Envelope()
    container.services.us_context.get_macro_context = AsyncMock(return_value=_Envelope())
    registry = create_capability_registry(container)

    await registry.invoke(
        "research_get",
        {
            "request": {
                "operation": "query",
                "case_id": None,
                "limit": 7,
                "offset": 11,
            }
        },
    )
    container.services.research_subjects.list_subjects.assert_called_once_with(
        subject_type=None,
        status=None,
        primary_instrument_id=None,
        topic_tag=None,
        include_archived=False,
        limit=7,
        offset=11,
    )

    await registry.invoke(
        "us_get_facts",
        {"request": {"operation": "macro", "as_of": None}},
    )
    macro_request = container.services.us_context.get_macro_context.await_args.args[0]
    assert macro_request.series_ids == tuple(DEFAULT_MACRO_SERIES)
    assert macro_request.lookback_days == 365
    assert macro_request.as_of is None


@pytest.mark.asyncio
async def test_v7_cross_operation_fields_fail_closed_before_service_calls() -> None:
    container = _container()
    registry = create_capability_registry(container)

    invalid_requests = (
        ("research_get", "query", {"thesis_id": "thesis_1"}),
        ("portfolio_get", "positions", {"limit": 5}),
        ("us_get_facts", "macro", {"instrument_id": "equity:US:NVDA"}),
    )
    for capability, operation, extra in invalid_requests:
        result = await registry.invoke(
            capability,
            {"request": {"operation": operation, **extra}},
        )
        assert result["ok"] is False
        assert result["errors"][0]["code"] == "TOOL_INPUT_INVALID"

    assert container.services.mock_calls == []


@pytest.mark.asyncio
async def test_v7_legacy_tool_names_are_rejected_without_dispatch() -> None:
    container = _container()
    registry = create_capability_registry(container)

    for name in _contracts()["removed_tool_names"]:
        with pytest.raises(CapabilityNotFoundError):
            await registry.invoke(name, {})

    assert container.services.mock_calls == []


@pytest.mark.asyncio
async def test_v7_portfolio_reads_remain_durable_after_merge() -> None:
    container = _container()
    container.services.portfolio.get_account_positions.return_value = _Envelope(
        {"positions": []}
    )
    container.services.account_transactions.list_durable_transactions.return_value = _Envelope(
        {"transactions": []}
    )
    registry = create_capability_registry(container)

    positions = await registry.invoke(
        "portfolio_get", {"request": {"operation": "positions"}}
    )
    transactions = await registry.invoke(
        "portfolio_get", {"request": {"operation": "transactions"}}
    )

    assert positions["ok"] is True
    assert transactions["ok"] is True
    container.services.portfolio.get_account_positions.assert_called_once()
    container.services.account_transactions.list_durable_transactions.assert_called_once()
    container.services.account_transactions.get_transactions.assert_not_called()


@pytest.mark.asyncio
async def test_v7_console_full_result_path_still_bypasses_mcp_compaction() -> None:
    container = _container()
    container.services.portfolio.get_account_positions.return_value = _Envelope(
        {
            "positions": [
                {"position_id": f"position_{index}", "detail": "x" * 1000}
                for index in range(40)
            ]
        }
    )
    registry = create_capability_registry(container)

    mcp_result = await registry.invoke(
        "portfolio_get", {"request": {"operation": "positions"}}
    )
    console_result = await registry.invoke_uncompacted(
        "portfolio_get", {"request": {"operation": "positions"}}
    )

    assert mcp_result["_truncated"] is True
    assert len(console_result["data"]["positions"]) == 40
    assert all("_truncated" not in item for item in console_result["data"]["positions"])
