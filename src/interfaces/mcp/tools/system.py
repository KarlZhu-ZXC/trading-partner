"""Compact system adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_system_adapters(
    container: ApplicationContainer,
    *,
    surface_profile: str,
    public_tool_count: int,
    surface_schema_version: str = "compact-v9",
) -> SimpleNamespace:
    """Build the compact system adapter."""

    # ------------------------------------------------------------------ Phase 1A
    def system_health() -> dict[str, Any]:
        """Return application and database health as a Tool Envelope."""
        try:
            envelope = container.services.health.check()
            result = envelope.model_dump(mode="json")
            data = result.get("data")
            if isinstance(data, dict):
                data.update(
                    {
                        "mcp_surface_profile": surface_profile,
                        "public_tool_count": public_tool_count,
                        "surface_schema_version": surface_schema_version,
                    }
                )
            return result
        except Exception as exc:  # noqa: BLE001 — MCP must return ToolEnvelope
            return _unexpected_failure(container, exc)

    return SimpleNamespace(system_health=system_health)
