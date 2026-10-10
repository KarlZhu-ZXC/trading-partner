"""Focused checks for the synthetic capability inventory audit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parents[2]
_MODULE_SPEC = importlib.util.spec_from_file_location(
    "audit_capabilities",
    ROOT / "scripts" / "audit_capabilities.py",
)
assert _MODULE_SPEC is not None and _MODULE_SPEC.loader is not None
audit = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(audit)


def test_audit_uses_only_graph_metadata_and_covers_the_canonical_inventory(monkeypatch) -> None:
    container = audit._synthetic_container()
    monkeypatch.setattr(audit, "_synthetic_container", lambda: container)

    report = audit._build_report(ROOT)

    assert report["inventory"]["capability_count"] == 24
    assert report["inventory"]["operation_count"] == 109
    assert report["surface"]["entrypoint_count"] == 9
    operation_keys = {
        (item["capability"], item["operation"])
        for item in report["inventory"]["operations"]
    }
    assert len(operation_keys) == 109
    assert all(
        item["status"] == "NOT_OBSERVED"
        for item in report["inventory"]["operations"]
    )
    for item in report["inventory"]["operations"]:
        evidence = item["source_evidence"]
        assert "static_call_references" not in evidence
        for reference in evidence["static_literal_references"]:
            assert reference["kind"] == "static_literal"
            assert reference["context"] in {
                "registration",
                "catalogue",
                "other_static_literal",
            }
    assert container.services.mock_calls == []
    assert container.context.mock_calls == []


def test_static_references_find_exact_literals_and_skip_comments_and_generated_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "console" / "app" / "lib" / "generated").mkdir(parents=True)
    (tmp_path / "src" / "consumer.py").write_text(
        """
# registry.invoke(\"market_data_get\", {\"operation\": \"quote\"})
registry.invoke(\"market_data_get\", {\"operation\": \"quote\"})
technical_render_chart(instrument_id=\"equity:US:SPY\")
""",
        encoding="utf-8",
    )
    (tmp_path / "console" / "app" / "consumer.tsx").write_text(
        """
// invoke(\"market_data_get\", { operation: \"quote\" })
invoke(\"market_data_get\", { request: { operation: \"quote\" } });
""",
        encoding="utf-8",
    )
    (tmp_path / "console" / "app" / "lib" / "generated" / "contracts.ts").write_text(
        'invoke("market_data_get", { operation: "quote" });\n',
        encoding="utf-8",
    )

    refs = audit._scan_static_references(
        tmp_path,
        {
            audit.OperationKey("market_data_get", "quote"),
            audit.OperationKey("technical_render_chart", None),
        },
    )

    market_paths = {
        item["path"] for item in refs[audit.OperationKey("market_data_get", "quote")]
    }
    assert market_paths == {"src/consumer.py", "console/app/consumer.tsx"}
    chart_refs = refs[audit.OperationKey("technical_render_chart", None)]
    assert chart_refs == (
        {
            "path": "src/consumer.py",
            "line": 4,
            "kind": "static_literal",
            "context": "other_static_literal",
        },
    )


def test_usage_is_aggregate_only_and_unknown_or_malicious_rows_are_dropped(
    tmp_path: Path,
) -> None:
    usage_path = tmp_path / "usage.json"
    usage_path.write_text(
        json.dumps(
            {
                "initialized": True,
                "rows": [
                    {
                        "source": "CONSOLE",
                        "capability": "market_data_get",
                        "operation": "quote",
                        "outcome": "OK",
                        "calls": 2,
                        "total_elapsed_ms": 10,
                        "max_elapsed_ms": 7,
                        "payload": "private payload must never be copied",
                    },
                    {
                        "source": "MCP",
                        "capability": "technical_render_chart",
                        "operation": "__direct__",
                        "outcome": "OK",
                        "calls": 1,
                        "total_elapsed_ms": 3,
                        "max_elapsed_ms": 3,
                    },
                    {
                        "day": "2999-12-31",
                        "source": "CONSOLE",
                        "capability": "market_data_get",
                        "operation": "quote",
                        "outcome": "OK",
                        "calls": 1,
                        "total_elapsed_ms": 1,
                        "max_elapsed_ms": 1,
                    },
                    {
                        "source": "CONSOLE",
                        "capability": "evil_capability",
                        "operation": "steal",
                        "outcome": "OK",
                        "calls": 999,
                        "total_elapsed_ms": 999,
                        "max_elapsed_ms": 999,
                        "payload": "malicious unknown row",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = audit._read_usage(
        usage_path,
        {
            audit.OperationKey("market_data_get", "quote"),
            audit.OperationKey("technical_render_chart", None),
        },
    )
    serialized = json.dumps(result)

    assert result["initialized"] is True
    assert result["rows"] == [
        {
            "source": "CONSOLE",
            "capability": "market_data_get",
            "operation": "quote",
            "outcome": "OK",
            "calls": 2,
            "total_elapsed_ms": 10,
            "max_elapsed_ms": 7,
        },
        {
            "source": "MCP",
            "capability": "technical_render_chart",
            "operation": None,
            "outcome": "OK",
            "calls": 1,
            "total_elapsed_ms": 3,
            "max_elapsed_ms": 3,
        },
    ]
    assert result["ignored_rows"] == 2
    assert "private payload" not in serialized
    assert "malicious unknown row" not in serialized
    assert "evil_capability" not in serialized


def test_report_byte_totals_and_hypothetical_surface_are_self_consistent() -> None:
    report = audit._build_report(ROOT)
    surface = report["surface"]
    discovery = report["discovery"]
    hypothetical = report["hypothetical_7"]

    assert surface["wire_bytes_sum"] == sum(
        item["wire_bytes"] for item in surface["entrypoints"]
    )
    assert surface["wire_bytes"] >= surface["wire_bytes_sum"]
    assert surface["input_schema_bytes"] == sum(
        item["input_schema_bytes"] for item in surface["entrypoints"]
    )
    assert surface["entrypoint_count"] == 9
    assert hypothetical["entrypoint_count"] == 7
    assert hypothetical["dropped_entrypoints"] == [
        "research_judgment_propose",
        "technical_render_chart",
    ]
    assert hypothetical["routing_quality_impact"] == "NOT_MEASURED"
    assert hypothetical["recommendation"] == "KEEP_CURRENT_SURFACE_PENDING_EVIDENCE"
    assert discovery["full"]["count"] == 24
    assert set(discovery["categories"]) == {
        "research",
        "portfolio",
        "market",
        "monitoring",
        "operations",
    }
    assert sum(item["count"] for item in discovery["categories"].values()) == 24


def test_synthetic_container_does_not_accidentally_use_a_real_runtime() -> None:
    container = audit._synthetic_container()
    assert isinstance(container, MagicMock)
    assert container.settings.mcp_server_name == "synthetic-capability-audit"
    assert container.context.clock.now.return_value == audit.SYNTHETIC_NOW
