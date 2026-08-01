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
    surface_schema_version: str = "compact-v11",
) -> SimpleNamespace:
    """Build the compact system adapter."""

    # ------------------------------------------------------------------ Phase 1A
    def system_health() -> dict[str, Any]:
        """Return application and database health as a Tool Envelope."""
        try:
            envelope = container.services.health.check()
            result = envelope.model_dump(mode="json")
            try:
                quality_result = container.services.data_quality.check().model_dump(mode="json")
            except Exception:  # noqa: BLE001 — base health must survive quality-ledger failure
                quality_result = {
                    "data": {
                        "status": "error",
                        "generated_at": result.get("fetched_at"),
                        "mode": "durable_only",
                        "account_snapshots": [],
                        "account_activity": [],
                        "monitors": [],
                        "provider_routes": [],
                        "provider_route_window_truncated": False,
                        "issues": [
                            {
                                "code": "DATA_QUALITY_CENTER_UNAVAILABLE",
                                "severity": "error",
                                "scope": "persistence",
                                "subject_ref": None,
                                "observed_at": result.get("fetched_at"),
                                "detail": (
                                    "The quality center failed closed; base health "
                                    "remains available."
                                ),
                            }
                        ],
                        "limitations": [
                            "DURABLE_ONLY_NO_UPSTREAM_PROBE",
                            "PROVIDER_ROUTE_HISTORY_UNAVAILABLE",
                            "ACCOUNT_AGE_REPORTED_WITHOUT_GLOBAL_STALENESS_THRESHOLD",
                        ],
                    },
                }
            data = result.get("data")
            if isinstance(data, dict):
                quality_data = quality_result.get("data")
                if isinstance(quality_data, dict):
                    quality_data["component_checks"] = data.get("components", {})
                    quality_data["component_check_limitations"] = [
                        "CONFIGURATION_CHECK_IS_NOT_UPSTREAM_REACHABILITY",
                        "ONLY_COMPONENTS_WITH_EXPLICIT_PROBES_ARE_LISTED",
                    ]
                    data["data_quality"] = quality_data
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
