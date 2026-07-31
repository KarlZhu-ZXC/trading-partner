from mcp.types import Tool, ToolAnnotations

from interfaces.console.catalog import capability_catalog


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
