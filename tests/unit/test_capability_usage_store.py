from __future__ import annotations

import inspect
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

import pytest

from application.ports.capability_usage import (
    CAPABILITY_USAGE_MAX_ROWS,
    CapabilityUsageRecord,
)
from infrastructure.observability.capability_usage import SqliteCapabilityUsageStore


def _record(
    *,
    source: str = "MCP",
    capability: str = "research_get",
    operation: str = "read",
    outcome: str = "OK",
    elapsed_ms: int = 4,
) -> CapabilityUsageRecord:
    return CapabilityUsageRecord(source, capability, operation, outcome, elapsed_ms)


def test_record_aggregates_repeated_rows_and_exposes_coverage(tmp_path: Path) -> None:
    store = SqliteCapabilityUsageStore(tmp_path / "diagnostics" / "capability.sqlite3")

    store.record(_record(elapsed_ms=4))
    store.record(_record(elapsed_ms=9))
    store.record(_record(operation="write", outcome="TOOL_FAILURE", elapsed_ms=2))

    snapshot = store.snapshot()
    assert snapshot["scope"] == "registry_invocations"
    assert snapshot["completeness"] == "best_effort"
    assert snapshot["retention_days"] == 30
    assert snapshot["duration_resolution_ms"] == 1
    assert snapshot["initialized"] is True
    assert (
        snapshot["observed_from"] == snapshot["observed_to"] == datetime.now(UTC).date().isoformat()
    )
    assert snapshot["rows"] == [
        {
            "day": datetime.now(UTC).date().isoformat(),
            "source": "MCP",
            "capability": "research_get",
            "operation": "read",
            "outcome": "OK",
            "calls": 2,
            "total_elapsed_ms": 13,
            "max_elapsed_ms": 9,
        },
        {
            "day": datetime.now(UTC).date().isoformat(),
            "source": "MCP",
            "capability": "research_get",
            "operation": "write",
            "outcome": "TOOL_FAILURE",
            "calls": 1,
            "total_elapsed_ms": 2,
            "max_elapsed_ms": 2,
        },
    ]


def test_distinct_connections_can_record_concurrently(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "capability.sqlite3"

    def record_one(index: int) -> None:
        SqliteCapabilityUsageStore(path).record(
            _record(operation=f"read_{index}", elapsed_ms=index)
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(record_one, range(8)))

    rows = SqliteCapabilityUsageStore(path).snapshot()["rows"]
    assert len(rows) == 8
    assert sum(row["calls"] for row in rows) == 8


def test_failed_connection_setup_is_closed_before_bounded_retry(
    tmp_path: Path, monkeypatch
) -> None:
    original_connect = sqlite3.connect
    failures = []

    class BusyConnection:
        row_factory = None
        closed = False

        def execute(self, statement):
            error = sqlite3.OperationalError("Synthetic WAL initialization busy")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error

        def close(self):
            self.closed = True

    def connect(*args, **kwargs):
        if not failures:
            failed = BusyConnection()
            failures.append(failed)
            return failed
        assert failures[0].closed
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    store = SqliteCapabilityUsageStore(tmp_path / "diagnostics" / "capability.sqlite3")
    store.record(_record())
    assert failures[0].closed
    assert store.snapshot()["rows"][0]["calls"] == 1


def test_nonbusy_connection_failure_closes_without_retry(tmp_path: Path, monkeypatch) -> None:
    closed = []
    attempts = []

    class BrokenConnection:
        row_factory = None

        def execute(self, statement):
            raise sqlite3.OperationalError("Synthetic setup failure")

        def close(self):
            closed.append(True)

    def connect(*args, **kwargs):
        attempts.append(True)
        return BrokenConnection()

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        SqliteCapabilityUsageStore(tmp_path / "unused.sqlite3")._connect()
    assert len(attempts) == 1
    assert closed == [True]


def test_missing_snapshot_is_explicit_and_does_not_initialize(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "capability.sqlite3"
    snapshot = SqliteCapabilityUsageStore(path).snapshot()

    assert snapshot == {
        "rows": [],
        "observed_from": None,
        "observed_to": None,
        "retention_days": 30,
        "duration_resolution_ms": 1,
        "scope": "registry_invocations",
        "completeness": "best_effort",
        "initialized": False,
    }
    assert not path.exists()
    assert not path.parent.exists()


def test_snapshot_filters_expired_rows_and_record_prunes_them(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "capability.sqlite3"
    store = SqliteCapabilityUsageStore(path)
    store.record(_record())
    old_day = (datetime.now(UTC).date() - timedelta(days=31)).isoformat()
    edge_day = (datetime.now(UTC).date() - timedelta(days=29)).isoformat()

    connection = sqlite3.connect(path)
    connection.executemany(
        """
        INSERT INTO capability_usage_daily
            (day, source, capability, operation, outcome,
             calls, total_elapsed_ms, max_elapsed_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (old_day, "MCP", "expired", "read", "OK", 1, 1, 1),
            (edge_day, "MCP", "edge", "read", "OK", 1, 1, 1),
        ],
    )
    connection.commit()
    connection.close()

    before_write = store.snapshot()
    assert [row["capability"] for row in before_write["rows"]] == ["edge", "research_get"]

    store.record(_record(elapsed_ms=5))
    connection = sqlite3.connect(path)
    days = connection.execute("SELECT day FROM capability_usage_daily ORDER BY day").fetchall()
    connection.close()
    assert days == [(edge_day,), (datetime.now(UTC).date().isoformat(),)]


def test_snapshot_excludes_future_observations_without_rewriting_storage(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "capability.sqlite3"
    store = SqliteCapabilityUsageStore(path)
    store.record(_record())
    future = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE capability_usage_daily SET day = ?", (future,))
        connection.commit()
        assert store.snapshot()["rows"] == []
        assert connection.execute("SELECT day FROM capability_usage_daily").fetchall() == [
            (future,)
        ]
    finally:
        connection.close()


def test_max_rows_keeps_newest_aggregates(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "capability.sqlite3"
    store = SqliteCapabilityUsageStore(path)
    store.record(_record(operation="op_seed"))

    connection = sqlite3.connect(path)
    connection.executemany(
        """
        INSERT INTO capability_usage_daily
            (day, source, capability, operation, outcome,
             calls, total_elapsed_ms, max_elapsed_ms)
        VALUES (?, ?, ?, ?, ?, 1, 1, 1)
        """,
        [
            (datetime.now(UTC).date().isoformat(), "MCP", "research_get", f"op_{index:05d}", "OK")
            for index in range(CAPABILITY_USAGE_MAX_ROWS)
        ],
    )
    connection.commit()
    connection.close()

    store.record(_record(operation="op_final"))
    rows = store.snapshot()["rows"]
    operations = {row["operation"] for row in rows}
    assert len(rows) == CAPABILITY_USAGE_MAX_ROWS
    assert "op_00000" not in operations
    assert "op_19999" in operations
    assert "op_final" in operations


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "mcp"),
        ("source", "PRIVATE"),
        ("capability", "Research"),
        ("capability", "read-payload"),
        ("capability", "x" * 101),
        ("operation", ""),
        ("operation", "read.payload"),
        ("outcome", "PRIVATE_ERROR"),
        ("elapsed_ms", -1),
        ("elapsed_ms", 86_400_001),
        ("elapsed_ms", True),
        ("elapsed_ms", 1.5),
    ],
)
def test_malformed_records_are_rejected(field: str, value: object) -> None:
    values: dict[str, object] = {
        "source": "MCP",
        "capability": "research_get",
        "operation": "read",
        "outcome": "OK",
        "elapsed_ms": 1,
    }
    values[field] = value

    with pytest.raises(ValueError):
        CapabilityUsageRecord(**values)  # type: ignore[arg-type]


def test_record_has_only_the_fixed_secret_safe_fields() -> None:
    assert [field.name for field in fields(CapabilityUsageRecord)] == [
        "source",
        "capability",
        "operation",
        "outcome",
        "elapsed_ms",
    ]
    assert list(inspect.signature(CapabilityUsageRecord).parameters) == [
        "source",
        "capability",
        "operation",
        "outcome",
        "elapsed_ms",
    ]

    sentinel = CapabilityUsageRecord("INTERNAL", "__unknown__", "__invalid__", "ERROR", 0)
    assert sentinel.capability == "__unknown__"
    assert sentinel.operation == "__invalid__"


def test_sidecar_file_and_parent_are_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "capability.sqlite3"
    SqliteCapabilityUsageStore(path).record(_record())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_locked_sidecar_failure_is_bounded_and_raises(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "capability.sqlite3"
    SqliteCapabilityUsageStore(path).record(_record())
    locker = sqlite3.connect(path, timeout=0.01)
    locker.execute("BEGIN IMMEDIATE")
    started = monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError):
            SqliteCapabilityUsageStore(path).record(_record(elapsed_ms=7))
    finally:
        locker.rollback()
        locker.close()
    assert monotonic() - started < 0.5


def test_symlinked_sidecar_parent_is_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PermissionError):
        SqliteCapabilityUsageStore(linked_parent / "capability.sqlite3").record(_record())
