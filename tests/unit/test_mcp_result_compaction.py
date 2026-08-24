from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from application.dto.attention import AttentionQueryInput
from interfaces.mcp.server import create_capability_registry, create_mcp_server
from interfaces.mcp.validation import closed_variant_invalid_details, tool_input_invalid_envelope
from interfaces.shared.result_compaction import (
    CANONICAL_RESULT_MAX_BYTES,
    MCP_TEXT_CONTENT_MAX_BYTES,
    compact_mcp_result,
    encode_result,
)


def _container() -> MagicMock:
    container = MagicMock()
    container.settings = SimpleNamespace(mcp_server_name="Trading Partner Test")
    container.services = MagicMock()
    return container


def _huge_envelope() -> dict[str, object]:
    return {
        "ok": True,
        "request_id": "req_huge",
        "as_of": "2026-08-17T12:00:00+00:00",
        "fetched_at": "2026-08-17T12:00:01+00:00",
        "freshness": "fresh",
        "degraded": True,
        "sources": [{"name": "durable_store", "role": "PRIMARY"}],
        "warnings": [{"code": "STALE_DATA", "message": "do not leak this warning text"}],
        "errors": [],
        "data": {
            "items": [
                {"monitor_id": f"monitor_{index}", "status": "ACTIVE", "blob": "x" * 2_000}
                for index in range(40)
            ]
        },
    }


@pytest.mark.asyncio
async def test_registry_invoke_compacts_large_envelope_and_keeps_codes() -> None:
    container = _container()
    huge = _huge_envelope()
    container.services.monitoring.dashboard.return_value.model_dump.return_value = huge
    registry = create_capability_registry(container)
    result = await registry.invoke(
        "monitor_read",
        {"request": {"operation": "dashboard"}},
    )
    encoded = encode_result(result)
    assert len(encoded) <= CANONICAL_RESULT_MAX_BYTES
    assert result["_truncated"] is True
    assert result["ok"] is True
    assert result["degraded"] is True
    assert result["compaction"] == "monitor_read_dashboard_v1"
    assert any(
        item.get("code") == "STALE_DATA"
        for item in result["warnings"]
        if isinstance(item, dict)
    )
    assert "do not leak this warning text" not in encoded.decode()


@pytest.mark.asyncio
async def test_fastmcp_call_tool_uses_the_same_compactor() -> None:
    container = _container()
    huge = _huge_envelope()
    container.services.monitoring.dashboard.return_value.model_dump.return_value = huge
    server = create_mcp_server(container)
    result = await server._tool_manager.call_tool(
        "monitor_read",
        {"request": {"operation": "dashboard"}},
    )
    payload = result
    if hasattr(result, "content"):
        payload = json.loads(result.content[0].text)
    elif isinstance(result, list) and result and hasattr(result[0], "text"):
        payload = json.loads(result[0].text)
    encoded = encode_result(payload)
    assert len(encoded) <= MCP_TEXT_CONTENT_MAX_BYTES
    assert payload["_truncated"] is True
    assert payload["ok"] is True
    assert payload["compaction"] == "monitor_read_dashboard_v1"


def test_compact_mcp_result_leaves_image_blocks_untouched() -> None:
    envelope = {
        "ok": True,
        "request_id": "req_chart",
        "as_of": "2026-08-17T12:00:00+00:00",
        "fetched_at": "2026-08-17T12:00:00+00:00",
        "freshness": "fresh",
        "degraded": False,
        "data": {"note": "x" * 20_000},
    }
    image = {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"}
    result = compact_mcp_result(
        [
            {"type": "text", "text": json.dumps(envelope)},
            image,
        ],
        capability="technical_render_chart",
        arguments={},
    )
    assert result[1] == image
    text = result[0]["text"]
    parsed = json.loads(text)
    assert parsed["ok"] is True
    assert len(text.encode()) <= MCP_TEXT_CONTENT_MAX_BYTES


def test_compact_mcp_result_enforces_one_aggregate_text_budget() -> None:
    first = json.dumps(_huge_envelope())
    second = json.dumps({**_huge_envelope(), "request_id": "req_second"})
    result = compact_mcp_result(
        [
            {"type": "text", "text": first},
            {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
            {"type": "text", "text": second},
        ],
        capability="monitor_read",
        arguments={"request": {"operation": "dashboard"}},
    )
    text_blocks = [item["text"] for item in result if item.get("type") == "text"]
    assert sum(len(item.encode()) for item in text_blocks) <= MCP_TEXT_CONTENT_MAX_BYTES
    assert all(json.loads(item)["_truncated"] is True for item in text_blocks)
    assert result[1]["type"] == "image"


@pytest.mark.asyncio
async def test_closed_variant_invalid_does_not_call_application() -> None:
    container = _container()
    registry = create_capability_registry(container)
    result = await registry.invoke(
        "research_workflow_run",
        {"request": {"operation": "deep_dive"}},
        confirmation="research_workflow_run",
    )
    assert result["ok"] is False
    error = result["errors"][0]
    assert error["code"] == "TOOL_INPUT_INVALID"
    assert error["details"]["tool"] == "research_workflow_run"
    assert error["details"]["operation"] == "deep_dive"
    assert "idempotency_key" in error["details"]["missing_fields"]
    assert "traceback" not in json.dumps(result).lower()
    assert "/Users/" not in json.dumps(result)
    container.services.workflows.run_deep_dive.assert_not_called()


@pytest.mark.asyncio
async def test_transport_missing_request_stays_mcp_error_and_is_redacted() -> None:
    container = _container()
    server = create_mcp_server(container)
    with pytest.raises(ToolError) as exc:
        await server._tool_manager.call_tool("investment_case_read", {})
    message = str(exc.value)
    assert "Traceback" not in message
    assert "/Users/" not in message
    assert "Exception" not in message


def test_validation_details_do_not_echo_payload_or_exception_text() -> None:
    with pytest.raises(ValidationError) as exc:
        AttentionQueryInput.model_validate({"limit": 0, "secret": "sk-live"})
    details = closed_variant_invalid_details(exc.value)
    serialized = json.dumps(details)
    assert "sk-live" not in serialized
    envelope = tool_input_invalid_envelope(
        tool="investment_case_read",
        operation="attention",
        error=exc.value,
    )
    dumped = json.dumps(envelope)
    assert envelope["errors"][0]["code"] == "TOOL_INPUT_INVALID"
    assert "sk-live" not in dumped
    assert "traceback" not in dumped.lower()
