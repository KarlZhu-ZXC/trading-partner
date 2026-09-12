"""Focused MCP contracts for the confirmed Trade Cycle override operation."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from domain.portfolio.trade_cycle_overrides import TradeCycleOverrideOperation
from interfaces.mcp.server import create_capability_registry
from interfaces.mcp.tools.compact import (
    CapabilityConfirmationRequiredError,
    _spec,
)


def _container() -> MagicMock:
    container = MagicMock()
    container.settings = SimpleNamespace(mcp_server_name="Trading Partner Test")
    container.services = MagicMock()
    container.context.clock.now.return_value = datetime(2026, 9, 8, tzinfo=UTC)
    container.context.id_generator.new.return_value = "req_cycle_override"
    container.context.secret_redactor.redact_text.side_effect = lambda value: value
    container.services.trade_cycle_overrides.append_revision.return_value = {
        "override_id": "trade_cycle_override_1"
    }
    return container


def _request(
    override_operation: str = "SPLIT",
    **updates: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation": "trade_cycle_override",
        "root_cycle_id": "cycle_1",
        "override_operation": override_operation,
        "cycle_ids": ["cycle_1"],
        "split_groups": [["activity_1"], ["activity_2"]],
        "confirmed_by": "user",
        "authorization_note": "Reviewed the cycle correction.",
        "idempotency_key": "cycle-override-1",
    }
    payload.update(updates)
    return {"request": payload}


def test_trade_cycle_override_group_exposes_six_operations_and_requires_operation() -> None:
    registry = create_capability_registry(_container())
    tool = next(item for item in registry.list_tools() if item.name == "research_memory_append")
    request_schema = tool.inputSchema["properties"]["request"]

    assert request_schema["properties"]["operation"]["enum"] == [
        "journal",
        "decision",
        "agenda_item",
        "activity_annotation",
        "trade_cycle_override",
        "behavior_review",
    ]
    assert request_schema["required"] == ["operation"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alias", "expected", "updates"),
    (
        (" split ", TradeCycleOverrideOperation.SPLIT, {}),
        (
            "merge",
            TradeCycleOverrideOperation.MERGE,
            {"cycle_ids": ["cycle_1", "cycle_2"], "split_groups": []},
        ),
        (
            "RELINK",
            TradeCycleOverrideOperation.RELINK,
            {
                "cycle_ids": ["cycle_1", "cycle_2"],
                "split_groups": [],
                "activity_ids": ["activity_1"],
                "target_cycle_id": "cycle_2",
            },
        ),
    ),
)
async def test_trade_cycle_override_maps_business_operation_and_calls_service(
    alias: str,
    expected: TradeCycleOverrideOperation,
    updates: dict[str, Any],
) -> None:
    container = _container()
    registry = create_capability_registry(container)

    result = await registry.invoke(
        "research_memory_append",
        _request(alias, expected_version=3, **updates),
        confirmation="research_memory_append",
    )

    assert result["ok"] is True
    container.services.trade_cycle_overrides.append_revision.assert_called_once()
    request = container.services.trade_cycle_overrides.append_revision.call_args.args[0]
    assert request.operation is expected
    assert request.actor == "user"
    assert request.expected_version == 3
    assert request.idempotency_key == "cycle-override-1"
    assert request.authorization_note == "Reviewed the cycle correction."


@pytest.mark.asyncio
async def test_trade_cycle_override_requires_explicit_confirmation() -> None:
    container = _container()
    registry = create_capability_registry(container)

    with pytest.raises(CapabilityConfirmationRequiredError):
        await registry.invoke("research_memory_append", _request())

    container.services.trade_cycle_overrides.append_revision.assert_not_called()


def test_trade_cycle_override_requires_business_operation_and_keeps_router_closed() -> None:
    registry = create_capability_registry(_container())
    payload = _request()["request"]
    payload.pop("override_operation")

    with pytest.raises(ValidationError):
        registry.validate_operation("research_memory_append", "trade_cycle_override", payload)

    with pytest.raises(ValidationError):
        registry.validate_operation(
            "research_memory_append",
            "trade_cycle_override",
            {**_request()["request"], "operation": "SPLIT"},
        )


@pytest.mark.asyncio
async def test_invalid_operation_and_actor_never_write() -> None:
    container = _container()
    registry = create_capability_registry(container)

    with pytest.raises(ToolError):
        await registry.invoke(
            "research_memory_append",
            _request("not-an-operation"),
            confirmation="research_memory_append",
        )
    assert container.services.trade_cycle_overrides.append_revision.call_count == 0

    result = await registry.invoke(
        "research_memory_append",
        _request(confirmed_by="robot"),
        confirmation="research_memory_append",
    )
    assert result["ok"] is False
    assert container.services.trade_cycle_overrides.append_revision.call_count == 0


def test_spec_rejects_reserved_operation_fields() -> None:
    with pytest.raises(ValueError, match="reserved"):
        _spec("bad", lambda: None, ("operation",))
    with pytest.raises(ValueError, match="reserved"):
        _spec("bad", lambda: None, extra_fields={"operation": (str, ...)})
