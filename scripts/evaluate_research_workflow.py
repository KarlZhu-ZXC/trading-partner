#!/usr/bin/env python3
"""Measure the synthetic research-links workflow end to end.

The runner creates a disposable, migration-complete SQLite database, seeds one
valid Research Subject with a Thesis, an assumption, and a bounded number of
evidence links, then calls the real Console ASGI route through HTTPX ``ASGITransport``.
Only request-to-JSON time is sampled.  Fixture creation, migrations, model
calls, providers, broker APIs, and the user's configuration are outside the
measurement boundary.

Example::

    uv run python scripts/evaluate_research_workflow.py \
      --output /tmp/research-workflow.json --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import statistics
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application import __version__ as APPLICATION_VERSION
from application.services.research_links_query_service import ResearchLinksQueryService
from domain.common.ids import EntityIdPrefix
from infrastructure.persistence.orm import (
    AssumptionRow,
    ResearchEvidenceRow,
    ResearchSubjectRow,
    SubjectEvidenceLinkRow,
    ThesisRevisionRow,
    ThesisRow,
)
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.redactor import DefaultSecretRedactor
from interfaces.cli.agent_behavior_evaluation import run_catalog
from interfaces.cli.agent_evaluation_support import evaluation_fingerprint_manifest
from interfaces.console.research_links_api import router as research_links_router

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_HEAD = "0073_research_evidence_links"
WORKLOADS = (1, 10, 100)
WARMUPS = 2
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
_COUNT_TABLES = (
    "investment_cases",
    "theses",
    "thesis_revisions",
    "assumptions",
    "research_evidence",
    "case_evidence_links",
    "evidence_impacts",
)


class _FixedClock:
    def now(self) -> datetime:
        return NOW


class _EvaluationIds:
    def __init__(self) -> None:
        self._next = 1

    def new(self, prefix: EntityIdPrefix) -> str:
        token = f"01901945-7f5d-7cc3-98c4-{self._next:012x}"
        self._next += 1
        return f"{prefix.value}_{token}"


def _entity(prefix: str, ordinal: int = 0) -> str:
    return f"{prefix}_01901945-7f5d-7cc3-98c4-{ordinal:012x}"


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _migrate(path: Path) -> None:
    """Create a migration-complete DB without loading AppSettings."""

    config = Config()
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.attributes["database_url"] = _database_url(path)
    command.upgrade(config, MIGRATION_HEAD)


def _seed(path: Path, source_count: int) -> str:
    """Seed only valid synthetic rows; this runs before timing starts."""

    _migrate(path)
    engine = create_engine(_database_url(path))
    subject_id = _entity("case")
    thesis_id = _entity("thesis")
    revision_id = _entity("rev")
    assumption_id = _entity("assumption")
    timestamp = NOW.isoformat()
    with Session(engine) as session, session.begin():
        session.add(
            ResearchSubjectRow(
                subject_id=subject_id,
                subject_type="theme",
                title="Synthetic research workflow",
                summary="Disposable end-to-end performance fixture.",
                status="active",
                primary_instrument_id=None,
                topic_tags_json=("synthetic", "evaluation"),
                created_at=timestamp,
                updated_at=timestamp,
                created_by="user",
                archived_at=None,
                archived_reason=None,
                linked_subject_ids_json=(),
                evidence_ids_json=(),
                report_ids_json=(),
                event_ids_json=(),
                decision_ids_json=(),
                schema_version=1,
            )
        )
        session.add(
            ThesisRow(
                thesis_id=thesis_id,
                subject_id=subject_id,
                title="Synthetic durable demand",
                role="primary",
                status="active",
                current_revision_no=1,
                latest_revision_id=revision_id,
                parent_thesis_id=None,
                rival_thesis_ids_json=(),
                created_at=timestamp,
                updated_at=timestamp,
                archived_at=None,
            )
        )
        session.add(
            ThesisRevisionRow(
                revision_id=revision_id,
                thesis_id=thesis_id,
                subject_id=subject_id,
                revision_no=1,
                supersedes_revision_no=None,
                statement="Synthetic demand remains durable.",
                rationale="Synthetic contracted demand fixture.",
                confidence_band="medium",
                rating="watch",
                confirmation_mode="strict_review",
                proposed_by="user",
                confirmed_by="user",
                proposed_at=timestamp,
                confirmed_at=timestamp,
                observation_window_start=None,
                observation_window_end=None,
                invalidation_check_note="Synthetic invalidation note.",
                schema_version=1,
            )
        )
        session.add(
            AssumptionRow(
                assumption_id=assumption_id,
                thesis_id=thesis_id,
                subject_id=subject_id,
                revision_no=1,
                statement="Gross margin remains above the planning threshold.",
                basis="Synthetic reported margin.",
                falsifiability="A reported margin below the threshold.",
                status="accepted",
                proposed_at=timestamp,
                confirmed_at=timestamp,
                proposed_by="user",
                confirmed_by="user",
                retired_at=None,
                retired_reason=None,
            )
        )
        for index in range(source_count):
            evidence_id = _entity("evidence", index + 1)
            content_hash = hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()
            session.add(
                ResearchEvidenceRow(
                    evidence_id=evidence_id,
                    evidence_type="fundamental_snapshot",
                    origin="external_fact",
                    title=f"Synthetic report {index + 1:03d}",
                    summary="Synthetic source summary.",
                    content_text="Gross margin remains above the planning threshold.",
                    structured_data_json=None,
                    source_name="synthetic-evaluator",
                    source_vendor=None,
                    source_record_id=evidence_id,
                    source_url=f"https://synthetic.example/evidence/{index + 1}",
                    published_at=timestamp,
                    observed_at=timestamp,
                    effective_from=None,
                    effective_to=None,
                    instrument_ids_json=(),
                    topic_tags_json=("synthetic",),
                    quality="primary",
                    reliability="high",
                    confidence_decimal="1",
                    content_sha256=content_hash,
                    supersedes_evidence_id=None,
                    recorded_by="user",
                    schema_version=1,
                )
            )
            session.add(
                SubjectEvidenceLinkRow(
                    link_id=_entity("rev", index + 1),
                    subject_id=subject_id,
                    evidence_id=evidence_id,
                    linked_at=timestamp,
                    linked_by="user",
                    schema_version=1,
                )
            )
    engine.dispose()
    return subject_id


def _build_app(engine: Engine) -> FastAPI:
    clock = _FixedClock()
    ids = _EvaluationIds()
    redactor = DefaultSecretRedactor()

    def factory() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    services = SimpleNamespace(research_links=ResearchLinksQueryService(factory, clock))
    container = SimpleNamespace(services=services)
    app = FastAPI()
    app.state.container = container
    app.include_router(research_links_router)
    return app


def _row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in _COUNT_TABLES
        }


def _response_facts(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise AssertionError("research-links response omitted sources")
    return {
        "subject_id": payload.get("subject_id"),
        "source_count": len(sources),
        "source_ids": tuple(
            sorted(
                item.get("source", {}).get("source_id")
                for item in sources
                if isinstance(item, dict)
            )
        ),
        "coverage": payload.get("coverage", {}).get("sources"),
        "sources_has_more": payload.get("sources_has_more"),
        "impact_count": len(payload.get("impacts", [])),
        "target_count": len(payload.get("targets", [])),
    }


async def _measure_workload_async(path: Path, source_count: int, repeats: int) -> dict[str, Any]:
    engine = create_engine(_database_url(path))
    subject_id = _seed(path, source_count)
    app = _build_app(engine)
    route = f"/api/research/{subject_id}/evidence-links"

    # Validate the facts and coverage before any sample is accepted.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://synthetic") as client:
        check = await client.get(route)
        if check.status_code != 200:
            raise AssertionError(f"synthetic research-links route returned {check.status_code}")
        check_payload = check.json().get("data")
        if not isinstance(check_payload, dict):
            raise AssertionError("synthetic research-links route returned no data object")
        facts = _response_facts(check_payload)
        expected_ids = tuple(
            sorted(_entity("evidence", index + 1) for index in range(source_count))
        )
        expected_facts = {
            "subject_id": subject_id,
            "source_count": source_count,
            "source_ids": expected_ids,
            "coverage": "COMPLETE",
            "sources_has_more": False,
            "impact_count": 0,
            "target_count": 1,
        }
        if facts != expected_facts:
            raise AssertionError(f"synthetic response facts mismatch: {facts!r}")
        before_counts = _row_counts(engine)

        for _ in range(WARMUPS):
            response = await client.get(route)
            if response.status_code != 200:
                raise AssertionError("warmup request failed")

        samples: list[float] = []
        select_counts: list[int] = []
        write_counts: list[int] = []
        result_digests: list[str] = []
        counts = {"select": 0, "write": 0}

        def count_sql(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            token = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
            if token == "SELECT":
                counts["select"] += 1
            elif token in {"INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE"}:
                counts["write"] += 1

        event.listen(engine, "before_cursor_execute", count_sql)
        try:
            for _ in range(repeats):
                counts["select"] = 0
                counts["write"] = 0
                started = time.perf_counter_ns()
                response = await client.get(route)
                payload = response.json().get("data")
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                if response.status_code != 200:
                    raise AssertionError("timed request failed")
                if not isinstance(payload, dict) or _response_facts(payload) != expected_facts:
                    raise AssertionError("timed response facts changed")
                samples.append(elapsed)
                select_counts.append(counts["select"])
                write_counts.append(counts["write"])
                result_digests.append(hashlib.sha256(response.content).hexdigest())
        finally:
            event.remove(engine, "before_cursor_execute", count_sql)

        after_counts = _row_counts(engine)
    if before_counts != after_counts:
        raise AssertionError(f"read route mutated fixture rows: {before_counts} -> {after_counts}")
    if len(set(result_digests)) != 1:
        raise AssertionError("identical synthetic requests produced different JSON responses")
    if any(write_counts):
        raise AssertionError(f"read route emitted write SQL: {write_counts}")
    engine.dispose()
    return {
        "source_count": source_count,
        "p50_ms": statistics.median(samples),
        "p95_ms": sorted(samples)[math.ceil(0.95 * len(samples)) - 1],
        "samples_ms": samples,
        "select_counts": select_counts,
        "write_counts": write_counts,
        "result_sha256": result_digests[0],
        "facts_verified": True,
        "read_mutations": False,
    }


def _measure_workload(path: Path, source_count: int, repeats: int) -> dict[str, Any]:
    return asyncio.run(_measure_workload_async(path, source_count, repeats))


def _fingerprint() -> dict[str, str]:
    manifest = evaluation_fingerprint_manifest()
    for relative in (
        "scripts/evaluate_research_workflow.py",
        "evals/workflow-semantic-holdout.v1.json",
        "src/application/services/research_links_query_service.py",
        "src/interfaces/console/research_links_api.py",
        "src/infrastructure/persistence/repositories/research_links.py",
        "src/domain/research/evidence_impact.py",
        "uv.lock",
    ):
        path = PROJECT_ROOT / relative
        manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(manifest.items()))


def _category_summary(results: list[dict[str, Any]], categories: dict[str, str]) -> dict[str, Any]:
    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        category = categories.get(str(result.get("id")), "unknown")
        bucket = by_category.setdefault(category, {"denominator": 0, "failed_assertions": 0})
        bucket["denominator"] += 1
        if not result.get("passed", False):
            bucket["failed_assertions"] += 1
    return by_category


def _quality_report() -> dict[str, Any]:
    catalog = asyncio.run(run_catalog(live=False))
    catalog_path = PROJECT_ROOT / "evals/agent-behavior.v1.json"
    catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    categories = {
        str(item["id"]): str(item["category"])
        for item in catalog_payload.get("cases", [])
        if isinstance(item, dict) and "id" in item and "category" in item
    }
    runtime_results = [item for item in catalog.get("results", []) if isinstance(item, dict)]
    research_results = [
        item for item in catalog.get("research_results", []) if isinstance(item, dict)
    ]
    expected_gap_cases = [item for item in research_results if item.get("expected_gap") is True]
    valid_cases = [item for item in research_results if item.get("expected_gap") is False]
    surfaced_gaps = [item for item in expected_gap_cases if item.get("actual_gap") is True]
    false_acceptance = [item for item in expected_gap_cases if item.get("actual_gap") is False]
    false_rejection = [item for item in valid_cases if item.get("actual_gap") is True]
    action_results = [
        item for item in runtime_results if categories.get(str(item.get("id"))) == "action_gate"
    ]
    execution_signal_codes = {
        "pending_action_executed",
        "broker_order_path_used",
        "direct_write",
    }
    unauthorized_execution_signals = [
        error
        for item in runtime_results
        for error in item.get("errors", [])
        if error in execution_signal_codes
    ]
    return {
        "passed": bool(catalog.get("passed")) and not false_acceptance and not false_rejection,
        "catalog_passed": bool(catalog.get("passed")),
        "runtime_cases": {
            "denominator": len(runtime_results),
            "failed_assertions": sum(not bool(item.get("passed")) for item in runtime_results),
            "by_category": _category_summary(runtime_results, categories),
        },
        "research_field_cases": {
            "denominator": len(research_results),
            "failed_assertions": sum(not bool(item.get("passed")) for item in research_results),
        },
        "citation_and_missing_source": {
            "denominator": len(research_results),
            "expected_gap_denominator": len(expected_gap_cases),
            "expected_gap_surfaced": len(surfaced_gaps),
            "false_acceptance_count": len(false_acceptance),
            "valid_case_denominator": len(valid_cases),
            "false_rejection_count": len(false_rejection),
            "observed_missing_count": sum(
                int(item.get("missing_count", 0)) for item in research_results
            ),
        },
        "action_gate_regressions": {
            "denominator": len(action_results),
            "failed_assertions": sum(not bool(item.get("passed")) for item in action_results),
            "observed_unauthorized_write_execution_signals": len(unauthorized_execution_signals),
            "execution_signal_runtime_case_denominator": len(runtime_results),
            "execution_signal_codes": sorted(set(unauthorized_execution_signals)),
            "measurement_scope": (
                "explicit boundary-violation signals across runtime and safety cases; "
                "these signals and action-gate failures are not counts of executed broker writes"
            ),
        },
        "schema_repair": catalog.get("schema_repair"),
    }


def _compatible_baseline(current: dict[str, Any], baseline: Any) -> tuple[bool, list[str]]:
    if not isinstance(baseline, dict):
        return False, ["baseline_not_object"]
    differences: list[str] = []
    if baseline.get("schema_version") != current.get("schema_version"):
        differences.append("schema_version")
    for field in ("fixture", "boundary", "environment"):
        if baseline.get(field) != current.get(field):
            differences.append(field)
    return not differences, differences


def _valid_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _validate_baseline_measurements(report: dict[str, Any]) -> list[str]:
    """Reject partial reports instead of turning missing metrics into zeros."""

    differences: list[str] = []
    performance = report.get("performance")
    workloads = performance.get("workloads") if isinstance(performance, dict) else None
    if not isinstance(workloads, dict) or set(workloads) != {f"sources_{n}" for n in WORKLOADS}:
        return ["performance.workloads"]
    for key in sorted(workloads):
        sample = workloads[key]
        if not isinstance(sample, dict):
            differences.append(f"performance.workloads.{key}")
            continue
        for scalar in ("p50_ms", "p95_ms"):
            if not _valid_number(sample.get(scalar)) or sample[scalar] < 0:
                differences.append(f"performance.workloads.{key}.{scalar}")
        for sequence in ("samples_ms", "select_counts", "write_counts"):
            values = sample.get(sequence)
            if (
                not isinstance(values, list)
                or not values
                or any(not _valid_number(value) or value < 0 for value in values)
            ):
                differences.append(f"performance.workloads.{key}.{sequence}")
        if not isinstance(sample.get("result_sha256"), str) or not sample["result_sha256"]:
            differences.append(f"performance.workloads.{key}.result_sha256")
    quality = report.get("quality")
    if not isinstance(quality, dict):
        return differences + ["quality"]
    required_metrics = {
        "runtime_cases": ("denominator", "failed_assertions"),
        "research_field_cases": ("denominator", "failed_assertions"),
        "citation_and_missing_source": (
            "denominator",
            "expected_gap_denominator",
            "expected_gap_surfaced",
            "false_acceptance_count",
            "valid_case_denominator",
            "false_rejection_count",
            "observed_missing_count",
        ),
        "action_gate_regressions": (
            "denominator",
            "failed_assertions",
            "observed_unauthorized_write_execution_signals",
        ),
    }
    for group, fields in required_metrics.items():
        values = quality.get(group)
        if not isinstance(values, dict):
            differences.append(f"quality.{group}")
            continue
        for field in fields:
            value = values.get(field)
            if not _valid_number(value) or value < 0:
                differences.append(f"quality.{group}.{field}")
    return differences


def _ratio(current: Any, baseline: Any) -> float | None:
    if not isinstance(current, int | float) or not isinstance(baseline, int | float):
        return None
    if baseline == 0:
        return None
    return round(float(current) / float(baseline), 6)


def _baseline_comparison(report: dict[str, Any], baseline_path: Path | None) -> dict[str, Any]:
    if baseline_path is None:
        return {"status": "NOT_REQUESTED"}
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "INCOMPATIBLE", "reason": "baseline_unreadable"}
    compatible, differences = _compatible_baseline(report, baseline)
    differences.extend(_validate_baseline_measurements(baseline))
    if not compatible:
        return {"status": "INCOMPATIBLE", "differences": differences}
    if differences:
        return {"status": "INCOMPATIBLE", "differences": differences}
    current_workloads = report.get("performance", {}).get("workloads", {})
    baseline_workloads = baseline.get("performance", {}).get("workloads", {})
    workloads: dict[str, Any] = {}
    for key, current in current_workloads.items():
        old = baseline_workloads.get(key)
        assert isinstance(old, dict)
        response_equivalent = current.get("result_sha256") == old.get("result_sha256")
        if not response_equivalent:
            workloads[key] = {
                "status": "RESPONSE_CHANGED",
                "response_equivalent": False,
                "current_result_sha256": current.get("result_sha256"),
                "baseline_result_sha256": old.get("result_sha256"),
            }
            continue
        workloads[key] = {
            "status": "COMPARABLE",
            "response_equivalent": True,
            "p50_ratio": _ratio(current.get("p50_ms"), old.get("p50_ms")),
            "p95_ratio": _ratio(current.get("p95_ms"), old.get("p95_ms")),
            "select_count_ratio": _ratio(
                statistics.median(current.get("select_counts", [])),
                statistics.median(old.get("select_counts", [])),
            ),
            "write_count_ratio": _ratio(
                statistics.median(current.get("write_counts", [])),
                statistics.median(old.get("write_counts", [])),
            ),
            "p50_regressed": current.get("p50_ms", 0) > old.get("p50_ms", 0),
            "p95_regressed": current.get("p95_ms", 0) > old.get("p95_ms", 0),
        }
    quality = report.get("quality", {})
    old_quality = baseline.get("quality", {})
    current_missing = quality.get("citation_and_missing_source", {})
    old_missing = old_quality.get("citation_and_missing_source", {})
    current_action = quality.get("action_gate_regressions", {})
    old_action = old_quality.get("action_gate_regressions", {})
    return {
        "status": "COMPARABLE",
        "workloads": workloads,
        "quality": {
            "false_acceptance_delta": current_missing.get("false_acceptance_count", 0)
            - old_missing.get("false_acceptance_count", 0),
            "false_rejection_delta": current_missing.get("false_rejection_count", 0)
            - old_missing.get("false_rejection_count", 0),
            "action_gate_failure_delta": current_action.get("failed_assertions", 0)
            - old_action.get("failed_assertions", 0),
        },
    }


def _semantic_acceptance() -> dict[str, Any]:
    path = PROJECT_ROOT / "evals" / "workflow-semantic-holdout.v1.json"
    raw = path.read_bytes()
    catalog = json.loads(raw)
    cases = catalog["cases"]
    return {
        "status": "INDEPENDENT_HUMAN_REVIEW_REQUIRED",
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "fixture_kind": catalog["fixture_kind"],
        "denominator": len(cases),
        "evaluated_count": 0,
        "dimensions": {
            dimension: {
                "denominator": sum(case["dimension"] == dimension for case in cases),
                "evaluated_count": 0,
                "critical_failures": None,
                "passed": None,
            }
            for dimension in catalog["dimensions"]
        },
        "model_quality_passed": None,
        "active_user_time_ms": None,
        "limitation": (
            "Scripted field/schema checks do not establish semantic quality or user time savings."
        ),
    }


def evaluate(*, repeats: int, baseline_path: Path | None = None) -> dict[str, Any]:
    if not 3 <= repeats <= 100:
        raise ValueError("repeats must be in [3,100]")
    fixture = {
        "kind": "synthetic_disposable_sqlite",
        "workloads": list(WORKLOADS),
        "migration_head": MIGRATION_HEAD,
        "setup_excluded_from_timing": True,
        "source_kind": "EVIDENCE",
    }
    boundary = {
        "transport": "HTTPX AsyncClient + ASGITransport in-process",
        "route": "GET /api/research/{subject_id}/evidence-links",
        "timing": "request_to_JSON_response",
        "providers": "none",
        "model": "scripted_deterministic_catalog_only",
        "broker": "none",
        "writes": "read-only route; zero SQL DML required",
    }
    environment = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "application_version": APPLICATION_VERSION,
        "migration_head": MIGRATION_HEAD,
        "dependencies": {
            name: version(name) for name in ("SQLAlchemy", "fastapi", "starlette", "httpx")
        },
    }
    with tempfile.TemporaryDirectory(prefix="tp-research-workflow-") as directory:
        performance: dict[str, Any] = {}
        for source_count in WORKLOADS:
            path = Path(directory) / f"sources-{source_count}.db"
            measurement = _measure_workload(path, source_count, repeats)
            performance[f"sources_{source_count}"] = measurement
    report: dict[str, Any] = {
        "schema_version": 1,
        "runner": "research_workflow_quality_performance",
        "environment": environment,
        "source_fingerprint_manifest": _fingerprint(),
        "fixture": fixture,
        "boundary": boundary,
        "repeats": repeats,
        "warmups": WARMUPS,
        "performance": {"workloads": performance},
        "quality": _quality_report(),
        "semantic_acceptance": _semantic_acceptance(),
        "passed_scope": "AUTOMATED_SYNTHETIC_GATES_ONLY",
    }
    report["baseline_comparison"] = _baseline_comparison(report, baseline_path)
    report["passed"] = (
        bool(report["quality"]["passed"])
        and report["baseline_comparison"]["status"] != "INCOMPATIBLE"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    if not 3 <= args.repeats <= 100:
        parser.error("repeats must be in [3,100]")
    try:
        report = evaluate(repeats=args.repeats, baseline_path=args.baseline)
    except Exception:  # noqa: BLE001 - never emit database/config details
        report = {
            "schema_version": 1,
            "runner": "research_workflow_quality_performance",
            "passed": False,
            "failure": "EVALUATION_FAILED",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
