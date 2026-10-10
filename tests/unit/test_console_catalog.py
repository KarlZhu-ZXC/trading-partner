from mcp.types import Tool, ToolAnnotations

from interfaces.capabilities import READ_DURABLE, CompactCapabilityRegistry
from interfaces.console.catalog import capability_catalog, registry_catalog


def test_console_catalog_reads_full_neutral_registration_without_mcp_listing() -> None:
    def read_notes(limit: int = 5) -> dict[str, int]:
        """Read bounded durable observations."""
        return {"limit": limit}

    registry = CompactCapabilityRegistry()
    registry.add_capability(read_notes, name="read_notes", policy=READ_DURABLE)
    result = registry_catalog(registry)
    schema = result[0]["input_schema"]
    assert schema == registry.registered_capabilities[0].tool.parameters
    assert schema["properties"]["limit"]["title"] == "Limit"
    assert schema["properties"]["limit"]["default"] == 5
    assert result[0]["read_only"] is True
    assert result[0]["confirmation_required"] is False


def test_capability_catalog_extracts_closed_operations_and_hints() -> None:
    tool = Tool(
        name="monitor_read",
        description="Read monitoring state.",
        inputSchema={
            "$defs": {
                "V0": {"properties": {"operation": {"const": "dashboard"}}},
                "V1": {"properties": {"operation": {"const": "runs"}}},
                "S0": {"type": "string"},
            },
            "type": "object",
        },
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )

    result = capability_catalog([tool])

    assert result == [
        {
            "name": "monitor_read",
            "group": "Monitoring",
            "description": "Read monitoring state.",
            "operations": ("dashboard", "runs"),
            "read_only": True,
            "open_world": False,
            "destructive": False,
            "effect": None,
            "confirmation_required": False,
            "input_schema": {
                "$defs": {
                    "V0": {"properties": {"operation": {"const": "dashboard"}}},
                    "V1": {"properties": {"operation": {"const": "runs"}}},
                    "S0": {"type": "string"},
                },
                "type": "object",
            },
        }
    ]
