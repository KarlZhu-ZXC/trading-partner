"""Compact Phase 1E E5c MCP exposure: inventory, schema, handler delegation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from domain.common.enums import Freshness, Market
from interfaces.mcp.server import (
    FORBIDDEN_PUBLIC_TOOL_NAMES,
    LEGACY_PUBLIC_TOOL_NAMES,
    PHASE1E_A_SHARE_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

_AS_OF = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
_INSTRUMENT_ID = "equity:A_SHARE:600519.SH"
_STUB_ERROR = ErrorInfo(
    code="PROVIDER_UNAVAILABLE",
    message="stub",
    retryable=False,
    details={},
)

# tool_name → (coordinator_method, minimal valid MCP args, required schema props)
_A_SHARE_TOOLS: tuple[tuple[str, str, dict[str, Any], frozenset[str]], ...] = (
    (
        "a_share_get_facts",
        "get_snapshot",
        {"operation": "snapshot", "instrument_id": _INSTRUMENT_ID},
        frozenset(),
    ),
    (
        "research_search_reports",
        "search_reports",
        {"text": "茅台"},
        frozenset(),
    ),
)


def _stub_envelope() -> ToolEnvelope[None]:
    return ToolEnvelope.failure(
        request_id="req_test",
        market=Market.A_SHARE,
        as_of=_AS_OF,
        fetched_at=_AS_OF,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(_STUB_ERROR,),
        degraded=True,
        data=None,
    )


def _container_with_coordinator() -> MagicMock:
    container = MagicMock()
    container.settings.mcp_server_name = "trading-partner-e5c-test"
    container.id_generator.new.return_value = "req_unexpected"
    container.clock.now.return_value = _AS_OF
    container.secret_redactor = MagicMock()
    container.secret_redactor.redact_text.side_effect = lambda s: s
    coordinator = MagicMock()
    for _, method, _, _ in _A_SHARE_TOOLS:
        setattr(coordinator, method, AsyncMock(return_value=_stub_envelope()))
    container.a_share_tool_coordinator = coordinator
    return container


def test_phase1e_public_inventory_exact() -> None:
    assert len(LEGACY_PUBLIC_TOOL_NAMES) == 15
    assert len(PHASE1E_A_SHARE_TOOL_NAMES) == 2
    assert len(PUBLIC_TOOL_NAMES) == 52
    assert PHASE1E_A_SHARE_TOOL_NAMES.isdisjoint(LEGACY_PUBLIC_TOOL_NAMES)
    assert PHASE1E_A_SHARE_TOOL_NAMES <= PUBLIC_TOOL_NAMES
    assert PUBLIC_TOOL_NAMES.isdisjoint(FORBIDDEN_PUBLIC_TOOL_NAMES)
    assert frozenset(name for name, *_ in _A_SHARE_TOOLS) == PHASE1E_A_SHARE_TOOL_NAMES


@pytest.mark.asyncio
async def test_phase1e_schema_and_handler_delegation() -> None:
    """The façade and report search remain thin validated delegates."""
    container = _container_with_coordinator()
    server = create_mcp_server(container)
    manager = server._tool_manager
    listed = {t.name: t for t in manager.list_tools()}

    assert set(listed) == set(PUBLIC_TOOL_NAMES)
    assert len(listed) == 52

    for tool_name, method, args, required_props in _A_SHARE_TOOLS:
        tool = listed[tool_name]
        assert tool.is_async is True
        schema = tool.parameters
        assert schema.get("type") == "object"
        props = schema.get("properties") or {}
        for prop in required_props:
            assert prop in props, f"{tool_name} missing required property {prop}"
        schema_required = set(schema.get("required") or [])
        assert required_props <= schema_required, tool_name

        result = await manager.call_tool(tool_name, args)
        assert isinstance(result, dict)
        assert result["request_id"] == "req_test"
        assert result["market"] == "A_SHARE"
        assert result["ok"] is False

        mock_fn = getattr(container.a_share_tool_coordinator, method)
        mock_fn.assert_awaited_once()
        (call_arg,) = mock_fn.await_args.args
        # Coordinator receives the frozen application input DTO, not raw kwargs.
        assert call_arg.__class__.__name__.endswith("Input")


@pytest.mark.asyncio
async def test_phase1e_validation_error_reraises() -> None:
    """Syntactic/schema invalid input re-raises ValidationError (not failure envelope)."""
    container = _container_with_coordinator()
    server = create_mcp_server(container)
    manager = server._tool_manager

    # Call the registered fn directly so FastMCP does not wrap ValidationError.
    snapshot_fn = manager.get_tool("a_share_get_facts").fn
    with pytest.raises(ValidationError):
        await snapshot_fn(instrument_id="not-a-valid-instrument-id")
    container.a_share_tool_coordinator.get_snapshot.assert_not_awaited()

    reports_fn = manager.get_tool("research_search_reports").fn
    with pytest.raises(ValidationError):
        await reports_fn()  # needs text | instrument_id | industry_code
    container.a_share_tool_coordinator.search_reports.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_share_financials_operation_delegates_without_new_public_tool() -> None:
    container = _container_with_coordinator()
    container.a_share_tool_coordinator.get_financial_statements = AsyncMock(
        return_value=_stub_envelope()
    )
    server = create_mcp_server(container)

    result = await server._tool_manager.call_tool(
        "a_share_get_facts",
        {
            "operation": "financials",
            "instrument_id": _INSTRUMENT_ID,
            "periods": 8,
            "statement_types": ["income_statement", "cash_flow"],
        },
    )

    assert result["ok"] is False
    container.a_share_tool_coordinator.get_financial_statements.assert_awaited_once()
    (request,) = container.a_share_tool_coordinator.get_financial_statements.await_args.args
    assert request.__class__.__name__ == "AShareGetFinancialStatementsInput"
    assert request.periods == 8
