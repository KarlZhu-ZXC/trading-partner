"""Compact exact-inventory and delegation check for Phase 1I MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from domain.common.enums import Freshness
from interfaces.mcp.server import (
    PHASE1I_PORTFOLIO_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

_NOW = datetime(2026, 7, 18, 16, tzinfo=UTC)
_TOOLS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("account_get", "get_account_positions", {"operation": "positions"}),
    ("portfolio_analyze", "analyze_portfolio", {}),
    (
        "portfolio_simulate_addition",
        "simulate_addition",
        {
            "instrument_id": "equity:US:NVDA",
            "quantity": "2",
            "assumed_price": "100",
            "currency": "USD",
        },
    ),
)


def _envelope() -> ToolEnvelope[None]:
    return ToolEnvelope.failure(
        request_id="req_phase1i",
        market=None,
        as_of=_NOW,
        fetched_at=_NOW,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(ErrorInfo(code="STUB", message="stub", retryable=False, details={}),),
        degraded=True,
        data=None,
    )


@pytest.mark.asyncio
async def test_consolidated_portfolio_handlers_validate_and_delegate() -> None:
    container = MagicMock()
    container.settings.mcp_server_name = "phase1i-test"
    coordinator = MagicMock()
    coordinator.get_account_snapshot = AsyncMock(return_value=_envelope())
    coordinator.get_account_positions.return_value = _envelope()
    coordinator.analyze_portfolio.return_value = _envelope()
    coordinator.simulate_addition.return_value = _envelope()
    container.portfolio_tool_coordinator = coordinator
    manager = create_mcp_server(container)._tool_manager
    listed = {tool.name: tool for tool in manager.list_tools()}

    assert frozenset(name for name, _, _ in _TOOLS) == PHASE1I_PORTFOLIO_TOOL_NAMES
    assert set(listed) == set(PUBLIC_TOOL_NAMES)
    assert len(listed) == 52
    assert "explicitly refresh" in (listed["account_get"].description or "")
    assert "explicit user request" in (listed["risk_check"].description or "")
    assert "explicitly requests" in (listed["portfolio_run_review"].description or "")
    for tool_name, method, args in _TOOLS:
        result = await manager.call_tool(tool_name, args)
        assert result["request_id"] == "req_phase1i"
        called = getattr(coordinator, method)
        if isinstance(called, AsyncMock):
            called.assert_awaited_once()
        else:
            called.assert_called_once()
        assert called.call_args.args[0].__class__.__name__.endswith("Input")
