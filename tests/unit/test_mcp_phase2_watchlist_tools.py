"""Exact Phase 2 Watchlist Hub MCP inventory and thin delegation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.dto.watchlist_hub import (
    WatchlistAddInput,
    WatchlistGetItemsInput,
    WatchlistRemoveInput,
)
from domain.common.enums import Freshness
from interfaces.mcp.server import (
    PHASE2_WATCHLIST_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def _envelope() -> ToolEnvelope[None]:
    return ToolEnvelope.failure(
        request_id="req_phase2_watchlist",
        market=None,
        as_of=NOW,
        fetched_at=NOW,
        freshness=Freshness.UNKNOWN,
        errors=(ErrorInfo(code="STUB", message="stub", retryable=False),),
    )


@pytest.mark.asyncio
async def test_phase2_four_handlers_validate_and_delegate() -> None:
    container = MagicMock()
    container.settings.mcp_server_name = "phase2-watchlist-test"
    service = MagicMock()
    service.get_groups = AsyncMock(return_value=_envelope())
    service.get_items = AsyncMock(return_value=_envelope())
    service.add = AsyncMock(return_value=_envelope())
    service.remove = AsyncMock(return_value=_envelope())
    container.watchlist_hub_service = service
    manager = create_mcp_server(container)._tool_manager

    listed = {tool.name for tool in manager.list_tools()}
    assert listed == set(PUBLIC_TOOL_NAMES)
    assert len(listed) == 52
    assert {"watchlist_get", "watchlist_add", "watchlist_remove"} == PHASE2_WATCHLIST_TOOL_NAMES

    calls: tuple[tuple[str, str, dict[str, Any], type[object]], ...] = (
        (
            "watchlist_get",
            "get_items",
            {"operation": "items", "group_name": "Favorites", "refresh": False},
            WatchlistGetItemsInput,
        ),
        (
            "watchlist_add",
            "add",
            {
                "instrument_id": "equity:US:NVDA",
                "confirmed_by": "user",
                "idempotency_key": "phase2-add",
            },
            WatchlistAddInput,
        ),
        (
            "watchlist_remove",
            "remove",
            {
                "membership_id": "watch_membership_example",
                "confirmed_by": "external_agent",
                "idempotency_key": "phase2-remove",
            },
            WatchlistRemoveInput,
        ),
    )
    for tool_name, method_name, arguments, input_type in calls:
        result = await manager.call_tool(tool_name, arguments)
        assert result["request_id"] == "req_phase2_watchlist"
        method = getattr(service, method_name)
        method.assert_awaited_once()
        assert isinstance(method.call_args.args[0], input_type)
