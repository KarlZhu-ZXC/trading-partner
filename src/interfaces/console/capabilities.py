"""Console assembly of the shared operation graph, independent of the MCP server."""

from bootstrap import ApplicationContainer
from interfaces.capabilities.factory import create_capability_registry as build_registry
from interfaces.capabilities.registry import CompactCapabilityRegistry
from interfaces.mcp.chart_artifacts import persist_chart_png


def create_capability_registry(container: ApplicationContainer) -> CompactCapabilityRegistry:
    return build_registry(container, chart_persister=persist_chart_png, source="CONSOLE")
