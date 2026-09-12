"""MCP test routing helpers for the progressive public surface.

Production MCP exposes nine small entry points.  Most unit tests still exercise
one of the 24 business capabilities directly because their assertions concern
the existing adapter behavior.  This helper keeps those calls on the real
public gateway instead of creating hidden compatibility tools in production.
"""

from __future__ import annotations

from typing import Any

from interfaces.mcp.server import create_capability_registry, create_mcp_server
from interfaces.mcp.tool_inventory import (
    DIRECT_PUBLIC_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
)


class _RoutedToolManager:
    """Proxy FastMCP's manager while routing legacy test calls through v11."""

    def __init__(self, manager: Any, policies: dict[str, Any]) -> None:
        self._manager = manager
        self._policies = policies

    def list_tools(self) -> Any:
        return self._manager.list_tools()

    def get_tool(self, name: str) -> Any:
        return self._manager.get_tool(name)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name in PUBLIC_TOOL_NAMES:
            return await self._manager.call_tool(name, arguments)

        policy = self._policies.get(name)
        if policy is None:
            # Preserve FastMCP's native unknown-tool error for retired names.
            return await self._manager.call_tool(name, arguments)

        if name in DIRECT_PUBLIC_TOOL_NAMES:
            return await self._manager.call_tool(name, arguments)

        if policy.confirmation_required:
            # Existing adapter tests represent an explicit authorization by
            # routing writes through the real gateway with the original
            # capability name.  This is test plumbing, not a production bypass.
            return await self._manager.call_tool(
                "capability_write",
                {"tool": name, "arguments": arguments, "confirmation": name},
            )
        return await self._manager.call_tool(
            "capability_read",
            {"tool": name, "arguments": arguments},
        )


def routed_mcp_server(container: Any) -> Any:
    """Return a public server whose test calls use the real v11 gateways."""

    server = create_mcp_server(container)
    registry = create_capability_registry(container)
    server._tool_manager = _RoutedToolManager(server._tool_manager, registry.policies)
    return server


def routed_mcp_manager(container: Any) -> _RoutedToolManager:
    """Return the routed manager for tests that only need tool calls."""

    return routed_mcp_server(container)._tool_manager


__all__ = ["routed_mcp_manager", "routed_mcp_server"]
