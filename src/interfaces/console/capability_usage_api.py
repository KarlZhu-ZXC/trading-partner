"""Read local aggregate usage evidence without invoking a business operation."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request

from bootstrap import ApplicationContainer
from interfaces.capabilities.registry import CompactCapabilityRegistry
from interfaces.mcp.tool_inventory import PUBLIC_TOOL_NAMES

router = APIRouter()


@router.get("/api/capability-usage")
def capability_usage(request: Request) -> dict[str, Any]:
    container = cast(ApplicationContainer, request.app.state.container)
    registry = cast(CompactCapabilityRegistry, request.app.state.capability_registry)
    store = container.context.capability_usage
    try:
        snapshot = store.snapshot() if store is not None else {
            "rows": [], "initialized": False, "completeness": "disabled",
        }
    except Exception:  # noqa: BLE001 - never expose paths, SQL or exception content
        snapshot = {"rows": [], "initialized": False, "completeness": "unavailable"}
    return {
        **snapshot,
        "recording_failures_this_process": registry.usage_recording_failures,
        "inventory": {
            "public_tools": len(PUBLIC_TOOL_NAMES),
            "capabilities": len(registry.policies),
            "operations": len(registry.operation_descriptors()),
        },
        "limitations": [
            "Only invocations reaching the shared registry are counted.",
            "Direct application/CLI calls, discovery and pre-dispatch rejection are not counted.",
            "No observation is not evidence that a capability is unused.",
        ],
    }
