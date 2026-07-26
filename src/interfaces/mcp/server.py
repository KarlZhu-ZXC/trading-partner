"""Production MCP composition and stdio lifecycle.

Tool adapters live under :mod:`interfaces.mcp.tools`; this module is the stable
public entry point used by the console script and existing hosts.
"""

from __future__ import annotations

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from bootstrap import ApplicationContainer, build_default_application
from interfaces.mcp import tool_inventory as _tool_inventory
from interfaces.mcp.chart_artifacts import persist_chart_png
from interfaces.mcp.tools.compact import create_compact_mcp_server as _create_compact_mcp_server

COMPACT_28_TOOL_NAMES = _tool_inventory.COMPACT_28_TOOL_NAMES
PUBLIC_TOOL_NAMES = _tool_inventory.PUBLIC_TOOL_NAMES
FORBIDDEN_PUBLIC_TOOL_NAMES = _tool_inventory.FORBIDDEN_PUBLIC_TOOL_NAMES
RETIRED_PUBLIC_TOOL_NAMES = _tool_inventory.RETIRED_PUBLIC_TOOL_NAMES


def create_mcp_server(container: ApplicationContainer) -> FastMCP:
    """Bind the sole public compact MCP surface."""
    return _create_compact_mcp_server(container, chart_persister=persist_chart_png)


async def _run_stdio() -> None:
    """Build and run the production stdio server in one event loop."""
    _suppress_sensitive_http_client_logs()
    container = build_default_application()
    try:
        server = create_mcp_server(container)
        await server.run_stdio_async()
    finally:
        await container.aclose()


def _suppress_sensitive_http_client_logs() -> None:
    """Keep provider URLs (which may contain credentials) off MCP stdio logs."""
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).disabled = True


def main() -> None:
    """Run FastMCP and close its container in the same event loop."""
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
