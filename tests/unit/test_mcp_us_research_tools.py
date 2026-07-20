"""Compact Phase 1G thin-delegation checks within the exact inventory."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from domain.common.enums import Freshness, Market
from interfaces.mcp.server import (
    PHASE1G_US_RESEARCH_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

AS_OF = datetime(2026, 7, 18, 16, tzinfo=UTC)
IID = "equity:US:NVDA"
TOOLS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "us_get_fundamentals",
        "get_fundamental_snapshot",
        {"operation": "snapshot", "instrument_id": IID},
    ),
    (
        "us_get_company_research",
        "get_filings",
        {"operation": "filings", "instrument_id": IID},
    ),
)


def _envelope() -> ToolEnvelope[None]:
    return ToolEnvelope.failure(
        request_id="req_test",
        market=Market.US,
        as_of=AS_OF,
        fetched_at=AS_OF,
        freshness=Freshness.UNKNOWN,
        sources=(),
        errors=(
            ErrorInfo(
                code="PROVIDER_UNAVAILABLE",
                message="stub",
                retryable=False,
                details={},
            ),
        ),
        degraded=True,
        data=None,
    )


def _container() -> MagicMock:
    container = MagicMock()
    container.settings.mcp_server_name = "phase1g-test"
    coordinator = MagicMock()
    for _, method, _ in TOOLS:
        setattr(coordinator, method, AsyncMock(return_value=_envelope()))
    container.us_research_tool_coordinator = coordinator
    return container


@pytest.mark.asyncio
async def test_consolidated_research_handlers_validate_and_delegate() -> None:
    container = _container()
    manager = create_mcp_server(container)._tool_manager
    listed = {tool.name: tool for tool in manager.list_tools()}

    assert frozenset(name for name, _, _ in TOOLS) == PHASE1G_US_RESEARCH_TOOL_NAMES
    assert set(listed) == set(PUBLIC_TOOL_NAMES)
    assert len(listed) == 52

    for tool_name, method, args in TOOLS:
        assert "instrument_id" in listed[tool_name].parameters["properties"]
        result = await manager.call_tool(tool_name, args)
        assert result["request_id"] == "req_test"
        mock_fn = getattr(container.us_research_tool_coordinator, method)
        mock_fn.assert_awaited_once()
        assert mock_fn.await_args.args[0].__class__.__name__.endswith("Input")
