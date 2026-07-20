"""Compact Phase 1F F3c MCP exposure: inventory, registration, thin delegation."""

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
    PHASE1F_US_MARKET_TOOL_NAMES,
    PHASE1G_US_RESEARCH_TOOL_NAMES,
    PHASE1H_US_CONTEXT_TOOL_NAMES,
    PHASE1I_PORTFOLIO_TOOL_NAMES,
    PHASE1J_CONTEXT_TOOL_NAMES,
    PHASE1K_CHALLENGE_TOOL_NAMES,
    PHASE1L_WORKFLOW_TOOL_NAMES,
    PHASE2_WATCHLIST_TOOL_NAMES,
    PHASE2B_RISK_TOOL_NAMES,
    PHASE2C_MONITORING_TOOL_NAMES,
    PHASE2D_TECHNICAL_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    create_mcp_server,
)

_AS_OF = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
_INSTRUMENT_ID = "equity:US:NVDA"
_STUB_ERROR = ErrorInfo(
    code="PROVIDER_UNAVAILABLE",
    message="stub",
    retryable=False,
    details={},
)

# tool_name → (coordinator_method, minimal valid MCP args, required schema props)
_US_TOOLS: tuple[tuple[str, str, dict[str, Any], frozenset[str]], ...] = (
    (
        "us_get_market",
        "get_market_snapshot",
        {"operation": "quote", "instrument_id": _INSTRUMENT_ID},
        frozenset({"instrument_id"}),
    ),
    (
        "market_get_bars",
        "get_market_bars",
        {
            "instrument_id": _INSTRUMENT_ID,
            "start": "2026-07-01",
            "end": "2026-07-17",
        },
        frozenset({"instrument_id", "start", "end"}),
    ),
    (
        "market_get_context",
        "get_market_context",
        {},
        frozenset(),
    ),
    (
        "technical_get_snapshot",
        "get_snapshot",
        {"instrument_id": _INSTRUMENT_ID},
        frozenset({"instrument_id"}),
    ),
)


def _stub_envelope() -> ToolEnvelope[None]:
    return ToolEnvelope.failure(
        request_id="req_test",
        market=Market.US,
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
    container.settings.mcp_server_name = "trading-partner-f3c-test"
    container.id_generator.new.return_value = "req_unexpected"
    container.clock.now.return_value = _AS_OF
    container.secret_redactor = MagicMock()
    container.secret_redactor.redact_text.side_effect = lambda s: s
    coordinator = MagicMock()
    for tool_name, method, _, _ in _US_TOOLS:
        if tool_name == "technical_get_snapshot":
            continue
        setattr(coordinator, method, AsyncMock(return_value=_stub_envelope()))
    container.us_tool_coordinator = coordinator
    container.technical_tool_coordinator.get_snapshot = AsyncMock(
        return_value=_stub_envelope()
    )
    return container


def test_phase1f_public_inventory_exact() -> None:
    assert len(LEGACY_PUBLIC_TOOL_NAMES) == 15
    assert len(PHASE1E_A_SHARE_TOOL_NAMES) == 2
    assert len(PHASE1F_US_MARKET_TOOL_NAMES) == 4
    assert len(PHASE1G_US_RESEARCH_TOOL_NAMES) == 2
    assert len(PHASE2_WATCHLIST_TOOL_NAMES) == 3
    assert len(PHASE2B_RISK_TOOL_NAMES) == 3
    assert len(PHASE2C_MONITORING_TOOL_NAMES) == 6
    assert len(PHASE2D_TECHNICAL_TOOL_NAMES) == 1
    assert len(PUBLIC_TOOL_NAMES) == 52
    assert PUBLIC_TOOL_NAMES == (
        LEGACY_PUBLIC_TOOL_NAMES
        | PHASE1E_A_SHARE_TOOL_NAMES
        | PHASE1F_US_MARKET_TOOL_NAMES
        | PHASE1G_US_RESEARCH_TOOL_NAMES
        | PHASE1H_US_CONTEXT_TOOL_NAMES
        | PHASE1I_PORTFOLIO_TOOL_NAMES
        | PHASE1J_CONTEXT_TOOL_NAMES
        | PHASE1K_CHALLENGE_TOOL_NAMES
        | PHASE2_WATCHLIST_TOOL_NAMES
        | PHASE2B_RISK_TOOL_NAMES
        | PHASE2C_MONITORING_TOOL_NAMES
        | PHASE2D_TECHNICAL_TOOL_NAMES
        | PHASE1L_WORKFLOW_TOOL_NAMES
    )
    assert LEGACY_PUBLIC_TOOL_NAMES.isdisjoint(PHASE1F_US_MARKET_TOOL_NAMES)
    assert PHASE1E_A_SHARE_TOOL_NAMES.isdisjoint(LEGACY_PUBLIC_TOOL_NAMES)
    assert PUBLIC_TOOL_NAMES.isdisjoint(FORBIDDEN_PUBLIC_TOOL_NAMES)
    assert frozenset(name for name, *_ in _US_TOOLS) == PHASE1F_US_MARKET_TOOL_NAMES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "method", "args", "required_props"),
    _US_TOOLS,
    ids=[t[0] for t in _US_TOOLS],
)
async def test_phase1f_handler_delegation(
    tool_name: str,
    method: str,
    args: dict[str, Any],
    required_props: frozenset[str],
) -> None:
    container = _container_with_coordinator()
    server = create_mcp_server(container)
    manager = server._tool_manager
    listed = {t.name: t for t in manager.list_tools()}

    assert tool_name in listed
    assert set(listed) == set(PUBLIC_TOOL_NAMES)
    assert len(listed) == 52

    tool = listed[tool_name]
    assert tool.is_async is True
    schema = tool.parameters
    assert schema.get("type") == "object"
    props = schema.get("properties") or {}
    for prop in required_props:
        assert prop in props, f"{tool_name} missing property {prop}"
    schema_required = set(schema.get("required") or [])
    assert required_props <= schema_required, tool_name

    result = await manager.call_tool(tool_name, args)
    assert isinstance(result, dict)
    assert result["request_id"] == "req_test"
    assert result["market"] == "US"
    assert result["ok"] is False

    coordinator = (
        container.technical_tool_coordinator
        if tool_name == "technical_get_snapshot"
        else container.us_tool_coordinator
    )
    mock_fn = getattr(coordinator, method)
    mock_fn.assert_awaited_once()
    (call_arg,) = mock_fn.await_args.args
    assert call_arg.__class__.__name__.endswith("Input")


@pytest.mark.asyncio
async def test_phase1f_validation_error_reraises() -> None:
    """Invalid instrument_id re-raises ValidationError (not failure envelope)."""
    container = _container_with_coordinator()
    server = create_mcp_server(container)
    manager = server._tool_manager

    snapshot_fn = manager.get_tool("us_get_market").fn
    with pytest.raises(ValidationError):
        await snapshot_fn(instrument_id="not-a-valid-instrument-id")
    container.us_tool_coordinator.get_market_snapshot.assert_not_awaited()
