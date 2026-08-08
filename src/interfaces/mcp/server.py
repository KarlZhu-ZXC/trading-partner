"""Production MCP composition and stdio lifecycle.

Tool adapters live under :mod:`interfaces.mcp.tools`; this module is the stable
public entry point used by the console script and existing hosts.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from bootstrap import (
    ApplicationContainer,
    build_application,
    build_default_application,
    load_settings,
)
from interfaces.mcp import tool_inventory as _tool_inventory
from interfaces.mcp.chart_artifacts import persist_chart_png
from interfaces.mcp.tools.compact import (
    CompactCapabilityRegistry,
    create_compact_capability_registry,
)
from interfaces.mcp.tools.compact import (
    create_compact_mcp_server as _create_compact_mcp_server,
)

COMPACT_28_TOOL_NAMES = _tool_inventory.COMPACT_28_TOOL_NAMES
PUBLIC_TOOL_NAMES = _tool_inventory.PUBLIC_TOOL_NAMES
FORBIDDEN_PUBLIC_TOOL_NAMES = _tool_inventory.FORBIDDEN_PUBLIC_TOOL_NAMES
RETIRED_PUBLIC_TOOL_NAMES = _tool_inventory.RETIRED_PUBLIC_TOOL_NAMES


def create_mcp_server(container: ApplicationContainer) -> FastMCP:
    """Bind the sole public compact MCP surface."""
    return _create_compact_mcp_server(container, chart_persister=persist_chart_png)


def create_capability_registry(
    container: ApplicationContainer,
) -> CompactCapabilityRegistry:
    """Build the transport-neutral compact registry used by local HTTP clients."""
    return create_compact_capability_registry(
        container,
        chart_persister=persist_chart_png,
    )


async def _run_stdio(env_file: Path | None = None) -> None:
    """Build and run the production stdio server in one event loop."""
    _suppress_sensitive_http_client_logs()
    container = (
        build_default_application()
        if env_file is None
        else build_application(load_settings(env_file))
    )
    try:
        server = create_mcp_server(container)
        await server.run_stdio_async()
    finally:
        await container.aclose()


def _suppress_sensitive_http_client_logs() -> None:
    """Keep provider URLs (which may contain credentials) off MCP stdio logs."""
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).disabled = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-partner-mcp")
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "Explicit runtime environment file. Installed tools never search the "
            "current directory for .env."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run FastMCP and close its container in the same event loop."""
    args = _parser().parse_args(argv)
    asyncio.run(_run_stdio(args.env_file))


if __name__ == "__main__":
    main()
