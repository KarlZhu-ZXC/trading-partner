"""Compact exact-inventory and thin-delegation checks for Phase 1H MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from domain.common.enums import Freshness, Market
from interfaces.mcp.server import (
    PHASE1H_US_CONTEXT_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

AS_OF = datetime(2026, 7, 18, 16, tzinfo=UTC)
IID = "equity:US:NVDA"
TOOLS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("market_get_live_news", "get_live_news", {"instrument_id": IID}),
    ("us_get_macro_context", "get_macro_context", {}),
    ("us_get_sentiment_snapshot", "get_sentiment_snapshot", {"instrument_id": IID}),
    (
        "us_get_prediction_market_context",
        "get_prediction_market_context",
        {"topic": "Fed cut"},
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
    container.settings.mcp_server_name = "phase1h-test"
    coordinator = MagicMock()
    for _, method, _ in TOOLS:
        setattr(coordinator, method, AsyncMock(return_value=_envelope()))
    container.us_context_tool_coordinator = coordinator
    return container


@pytest.mark.asyncio
async def test_exact_four_context_handlers_validate_and_delegate() -> None:
    container = _container()
    manager = create_mcp_server(container)._tool_manager
    listed = {tool.name: tool for tool in manager.list_tools()}

    assert frozenset(name for name, _, _ in TOOLS) == PHASE1H_US_CONTEXT_TOOL_NAMES
    assert set(listed) == set(PUBLIC_TOOL_NAMES)
    assert len(listed) == 52

    for tool_name, method, args in TOOLS:
        result = await manager.call_tool(tool_name, args)
        assert result["request_id"] == "req_test"
        mock_fn = getattr(container.us_context_tool_coordinator, method)
        mock_fn.assert_awaited_once()
        assert mock_fn.await_args.args[0].__class__.__name__.endswith("Input")
