"""Synthetic, disposable SQLite comparison; never loads AppSettings or owner data.

Run: uv run python scripts/benchmark_review_queries.py --output /tmp/review-benchmark.json
The baseline reproduces the old overview's subject-list + full-state reads. This
measures that database/application hot path, not whole-page or network latency.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.services.research_state_query_service import ResearchStateQueryService
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm.research import CandidateThesisRevisionRow, ResearchSubjectRow
from infrastructure.persistence.research_attention_reader import SqlAlchemyResearchAttentionReader
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator
from infrastructure.system.redactor import DefaultSecretRedactor


def seed(engine: Engine, count: int) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for index in range(count):
            subject_id = f"case_synthetic_{index:06d}"
            session.add(
                ResearchSubjectRow(
                    subject_id=subject_id,
                    subject_type="theme",
                    title=f"Synthetic {index}",
                    summary="Synthetic benchmark scope",
                    status="active",
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                    created_by="user",
                )
            )
            session.add(
                CandidateThesisRevisionRow(
                    candidate_id=f"run_00000000-0000-7000-8000-{index:012x}",
                    subject_id=subject_id,
                    payload_json='{"kind":"case_status_change","action":"update","title":"Synthetic"}',
                    kind="case_status_change",
                    confirmation_mode="normal",
                    status="proposed",
                    proposed_at="2026-01-01T00:00:00+00:00",
                    expires_at="2027-01-01T00:00:00+00:00",
                    proposed_by="user",
                    proposed_by_rationale="Synthetic benchmark",
                    idempotency_key=subject_id,
                )
            )


def query_paths(engine: Engine) -> tuple[Callable[[], object], Callable[[], object]]:
    clock, ids, redactor = SystemClock(), Uuid7IdGenerator(), DefaultSecretRedactor()

    def uow() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    state = ResearchStateQueryService(uow, clock, ids, redactor)

    def baseline() -> object:
        with uow() as transaction:
            subjects = transaction.subjects.list(limit=200)
        result = []
        for subject in subjects:
            envelope = state.get_state(subject.subject_id, include_watchlist=False)
            if not envelope.ok or envelope.data is None:
                raise AssertionError("Synthetic baseline state failed")
            if envelope.data.pending_candidates:
                result.append(
                    (subject.subject_id, subject.title, len(envelope.data.pending_candidates))
                )
        return sorted(result)

    reader = SqlAlchemyResearchAttentionReader(engine)

    def aggregate() -> object:
        return [
            (row.subject_id, row.title, row.pending_count) for row in reader.pending_by_subject()
        ]

    return baseline, aggregate


def measure(engine: Engine, run: Callable[[], object], repeats: int) -> dict[str, Any]:
    for _ in range(2):
        run()
    statements = 0

    def count_sql(*args: Any) -> None:
        nonlocal statements
        if args[2].lstrip().upper().startswith("SELECT"):
            statements += 1

    durations = []
    counts = []
    event.listen(engine, "before_cursor_execute", count_sql)
    try:
        for _ in range(repeats):
            statements = 0
            started = time.perf_counter_ns()
            run()
            durations.append((time.perf_counter_ns() - started) / 1_000_000)
            counts.append(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count_sql)
    # Memory instrumentation is deliberately separate from latency samples.
    tracemalloc.start()
    run()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "p50_ms": statistics.median(durations),
        "p95_ms": sorted(durations)[math.ceil(0.95 * len(durations)) - 1],
        "samples_ms": durations,
        "select_counts": counts,
        "peak_python_bytes": peak,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=15)
    args = parser.parse_args()
    if not 3 <= args.repeats <= 100:
        parser.error("repeats must be in [3,100]")
    results = []
    with tempfile.TemporaryDirectory(prefix="tp-query-benchmark-") as directory:
        for size in (10, 100, 200):
            engine = create_engine(f"sqlite:///{directory}/{size}.db")
            try:
                seed(engine, size)
                baseline, aggregate = query_paths(engine)
                assert baseline() == aggregate(), "Query results must match before timing"
                results.append(
                    {
                        "subjects": size,
                        "results_equal": True,
                        "baseline": measure(engine, baseline, args.repeats),
                        "aggregate": measure(engine, aggregate, args.repeats),
                    }
                )
            finally:
                engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "scope": "pending summary; warm disposable SQLite; no HTTP/providers/models",
                "fixture": "one proposed candidate per active theme; no theses; baseline cap 200",
                "python": platform.python_version(),
                "platform": platform.platform(),
                "repeats": args.repeats,
                "warmups": 2,
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )
    print(args.output)


if __name__ == "__main__":
    main()
