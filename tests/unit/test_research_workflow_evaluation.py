"""Focused checks for the synthetic research workflow evaluator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate_research_workflow.py"
_SPEC = importlib.util.spec_from_file_location("research_workflow_evaluation", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
evaluation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluation)


def test_percentile_and_baseline_compatibility_are_explicit() -> None:
    current = {
        "schema_version": 1,
        "fixture": {"kind": "synthetic"},
        "boundary": {"transport": "test"},
        "environment": {"python_version": "3.13"},
    }
    assert evaluation._compatible_baseline(current, current) == (True, [])
    compatible = dict(current)
    compatible["environment"] = {"python_version": "3.12"}
    assert evaluation._compatible_baseline(current, compatible) == (False, ["environment"])
    assert evaluation._ratio(12, 8) == 1.5
    assert evaluation._ratio(12, 0) is None


def test_quality_report_separates_gaps_from_action_gate_failures(monkeypatch) -> None:
    async def fake_catalog(*, live: bool = False):
        _ = live
        return {
            "passed": False,
            "results": [
                {
                    "id": "agent_watchlist_add_pending",
                    "passed": False,
                    "errors": ["pending_action_executed"],
                },
                {"id": "agent_portfolio_durable_first", "passed": True, "errors": []},
                {
                    "id": "agent_broker_order_stays_closed",
                    "passed": False,
                    "errors": ["broker_order_path_used"],
                },
            ],
            "research_results": [
                {
                    "passed": True,
                    "expected_gap": True,
                    "actual_gap": True,
                    "missing_count": 1,
                },
                {
                    "passed": False,
                    "expected_gap": True,
                    "actual_gap": False,
                    "missing_count": 0,
                },
                {
                    "passed": False,
                    "expected_gap": False,
                    "actual_gap": True,
                    "missing_count": 1,
                },
            ],
            "schema_repair": {"passed": True},
        }

    monkeypatch.setattr(
        evaluation,
        "run_catalog",
        fake_catalog,
    )
    report = evaluation._quality_report()
    citation = report["citation_and_missing_source"]
    action = report["action_gate_regressions"]
    assert citation["expected_gap_denominator"] == 2
    assert citation["expected_gap_surfaced"] == 1
    assert citation["false_acceptance_count"] == 1
    assert citation["false_rejection_count"] == 1
    assert action["denominator"] == 1
    assert action["failed_assertions"] == 1
    assert action["observed_unauthorized_write_execution_signals"] == 2
    assert action["execution_signal_runtime_case_denominator"] == 3
    assert report["passed"] is False


def test_read_route_verifies_facts_and_records_zero_mutations(tmp_path: Path) -> None:
    measurement = evaluation._measure_workload(tmp_path / "workflow.db", 1, 3)
    assert measurement["facts_verified"] is True
    assert measurement["read_mutations"] is False
    assert measurement["write_counts"] == [0, 0, 0]
    assert len(measurement["samples_ms"]) == 3
    assert measurement["p95_ms"] >= measurement["p50_ms"]


def _baseline_fixture() -> dict[str, object]:
    workloads = {
        f"sources_{count}": {
            "p50_ms": 1.0,
            "p95_ms": 2.0,
            "samples_ms": [1.0, 2.0, 1.5],
            "select_counts": [11, 11, 11],
            "write_counts": [0, 0, 0],
            "result_sha256": f"digest-{count}",
        }
        for count in (1, 10, 100)
    }
    return {
        "schema_version": 1,
        "fixture": {"kind": "synthetic"},
        "boundary": {"transport": "test"},
        "environment": {"python_version": "3.13"},
        "performance": {"workloads": workloads},
        "quality": {
            "runtime_cases": {"denominator": 1, "failed_assertions": 0},
            "research_field_cases": {"denominator": 1, "failed_assertions": 0},
            "citation_and_missing_source": {
                "denominator": 1,
                "expected_gap_denominator": 1,
                "expected_gap_surfaced": 1,
                "false_acceptance_count": 0,
                "valid_case_denominator": 0,
                "false_rejection_count": 0,
                "observed_missing_count": 1,
            },
            "action_gate_regressions": {
                "denominator": 1,
                "failed_assertions": 0,
                "observed_unauthorized_write_execution_signals": 0,
            },
        },
    }


def test_baseline_rejects_missing_measurements_and_marks_changed_responses(tmp_path: Path) -> None:
    report = _baseline_fixture()
    missing = dict(report)
    missing["performance"] = {"workloads": {"sources_1": {}}}
    missing_path = tmp_path / "missing.json"
    missing_path.write_text(json.dumps(missing), encoding="utf-8")
    result = evaluation._baseline_comparison(report, missing_path)
    assert result["status"] == "INCOMPATIBLE"
    assert "performance.workloads" in result["differences"]

    changed = _baseline_fixture()
    changed_workloads = changed["performance"]["workloads"]
    changed_workloads["sources_10"]["result_sha256"] = "different-response"
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    result = evaluation._baseline_comparison(report, changed_path)
    assert result["status"] == "COMPARABLE"
    assert result["workloads"]["sources_10"]["status"] == "RESPONSE_CHANGED"
    assert result["workloads"]["sources_1"]["p50_ratio"] == 1.0


def test_semantic_holdout_is_not_promoted_by_automated_field_checks() -> None:
    report = evaluation._semantic_acceptance()
    assert (
        report["catalog_sha256"]
        == "17ec88d7ac4c0e67553ae7f347bca710322eb48027fa0195dd3ec84b433a6806"
    )
    assert report["denominator"] == 5
    assert report["evaluated_count"] == 0
    assert report["model_quality_passed"] is None
    assert report["active_user_time_ms"] is None
    assert set(report["dimensions"]) == {
        "period",
        "scope",
        "causality",
        "counterevidence",
        "insufficient_information",
    }
    assert all(value["passed"] is None for value in report["dimensions"].values())
