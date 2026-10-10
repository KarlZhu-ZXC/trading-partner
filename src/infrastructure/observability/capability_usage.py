"""Owner-only SQLite aggregate store for capability invocation diagnostics."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import time
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from application.ports.capability_usage import (
    CAPABILITY_USAGE_MAX_ROWS,
    CAPABILITY_USAGE_NAME_SENTINELS,
    CAPABILITY_USAGE_OUTCOMES,
    CAPABILITY_USAGE_RETENTION_DAYS,
    CAPABILITY_USAGE_SOURCES,
    CapabilityUsageRecord,
    CapabilityUsageStore,
)

_BUSY_TIMEOUT_MS = 50
_BUSY_TIMEOUT_SECONDS = _BUSY_TIMEOUT_MS / 1000
_TABLE = "capability_usage_daily"
_META_TABLE = "capability_usage_meta"
_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    day TEXT NOT NULL,
    source TEXT NOT NULL,
    capability TEXT NOT NULL,
    operation TEXT NOT NULL,
    outcome TEXT NOT NULL,
    calls INTEGER NOT NULL CHECK (calls > 0),
    total_elapsed_ms INTEGER NOT NULL CHECK (total_elapsed_ms >= 0),
    max_elapsed_ms INTEGER NOT NULL CHECK (max_elapsed_ms >= 0),
    PRIMARY KEY (day, source, capability, operation, outcome)
)
"""
_CREATE_META_TABLE = f"""
CREATE TABLE IF NOT EXISTS {_META_TABLE} (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    aggregate_rows INTEGER NOT NULL CHECK (aggregate_rows >= 0)
)
"""
_INITIALIZE_META = f"""
INSERT OR IGNORE INTO {_META_TABLE} (id, aggregate_rows)
SELECT 1, COUNT(*) FROM {_TABLE}
"""
_REPAIR_META = f"""
UPDATE {_META_TABLE}
SET aggregate_rows = (SELECT COUNT(*) FROM {_TABLE})
WHERE id = 1
"""
_CREATE_INSERT_TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS capability_usage_meta_after_insert
AFTER INSERT ON {_TABLE}
BEGIN
    UPDATE {_META_TABLE}
    SET aggregate_rows = aggregate_rows + 1
    WHERE id = 1;
END
"""
_CREATE_DELETE_TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS capability_usage_meta_after_delete
AFTER DELETE ON {_TABLE}
BEGIN
    UPDATE {_META_TABLE}
    SET aggregate_rows = aggregate_rows - 1
    WHERE id = 1;
END
"""
_UPSERT = f"""
INSERT INTO {_TABLE} (
    day, source, capability, operation, outcome,
    calls, total_elapsed_ms, max_elapsed_ms
) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
ON CONFLICT(day, source, capability, operation, outcome)
DO UPDATE SET
    calls = calls + 1,
    total_elapsed_ms = total_elapsed_ms + excluded.total_elapsed_ms,
    max_elapsed_ms = MAX(max_elapsed_ms, excluded.max_elapsed_ms)
"""
_DELETE_BEFORE = f"DELETE FROM {_TABLE} WHERE day < ?"
_META_COUNT = f"SELECT aggregate_rows FROM {_META_TABLE} WHERE id = 1"
_META_TRIGGER = "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?"
_TRIM_ROWS = f"""
DELETE FROM {_TABLE}
WHERE rowid IN (
    SELECT rowid FROM {_TABLE}
    ORDER BY day DESC, source DESC, capability DESC, operation DESC, outcome DESC
    LIMIT -1 OFFSET ?
)
"""
_SELECT_ROWS = f"""
SELECT day, source, capability, operation, outcome,
       calls, total_elapsed_ms, max_elapsed_ms
FROM {_TABLE}
WHERE day >= ? AND day <= ?
ORDER BY day DESC, source DESC, capability DESC, operation DESC, outcome DESC
LIMIT ?
"""
_SELECT_TABLE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
_SAFE_NAME = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")
_SAFE_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_DEDICATED_PARENT_NAMES = frozenset(
    {"diagnostics", "observability", "capability_usage", "capability-usage"}
)


def _reject_symlink_components(path: Path) -> None:
    """Reject existing symlink components before opening the sidecar.

    ``Path.resolve`` would follow a symlink and make the check ineffective.
    Checking the sidecar and its immediate parent catches the mutable objects
    this adapter creates without rejecting standard system aliases such as a
    platform's ``/tmp`` symlink.
    """

    for current in (path, path.parent):
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        else:
            if stat.S_ISLNK(mode):
                raise PermissionError("capability usage sidecar cannot use symlinks")


def _secure_mode(path: Path, mode: int) -> None:
    with suppress(FileNotFoundError):
        os.chmod(path, mode)


class SqliteCapabilityUsageStore(CapabilityUsageStore):
    """Persist bounded daily usage aggregates in a private SQLite sidecar.

    The sidecar is intentionally independent from the application database:
    diagnostics must remain disposable and must never require a migration.
    Every write uses a fresh connection so separate processes can safely record
    observations without sharing application state.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path).expanduser().absolute()

    def record(self, record: CapabilityUsageRecord) -> None:
        if not isinstance(record, CapabilityUsageRecord):
            raise TypeError("record must be a CapabilityUsageRecord")

        self._prepare_storage()
        connection: sqlite3.Connection | None = None
        today = datetime.now(UTC).date()
        day = today.isoformat()
        try:
            connection = self._connect()
            _secure_mode(self._path, 0o600)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_CREATE_TABLE)
            connection.execute(_CREATE_META_TABLE)
            connection.execute(_INITIALIZE_META)
            insert_trigger = connection.execute(
                _META_TRIGGER,
                ("capability_usage_meta_after_insert",),
            ).fetchone()
            delete_trigger = connection.execute(
                _META_TRIGGER,
                ("capability_usage_meta_after_delete",),
            ).fetchone()
            if insert_trigger is None or delete_trigger is None:
                # Repair databases created before the metadata triggers, then
                # let the triggers maintain the count without a table scan on
                # subsequent records.
                connection.execute(_REPAIR_META)
                connection.execute(_CREATE_INSERT_TRIGGER)
                connection.execute(_CREATE_DELETE_TRIGGER)
            connection.execute(
                _UPSERT,
                (
                    day,
                    record.source,
                    record.capability,
                    record.operation,
                    record.outcome,
                    record.elapsed_ms,
                    record.elapsed_ms,
                ),
            )
            cutoff = (today - timedelta(days=CAPABILITY_USAGE_RETENTION_DAYS - 1)).isoformat()
            connection.execute(_DELETE_BEFORE, (cutoff,))
            aggregate_rows = connection.execute(_META_COUNT).fetchone()[0]
            if aggregate_rows > CAPABILITY_USAGE_MAX_ROWS:
                connection.execute(_TRIM_ROWS, (CAPABILITY_USAGE_MAX_ROWS,))
            connection.commit()
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def snapshot(self) -> dict[str, Any]:
        """Read aggregates without creating or mutating the sidecar."""

        _reject_symlink_components(self._path)
        if not self._path.exists():
            return self._empty_snapshot(initialized=False)
        if not self._path.is_file():
            raise IsADirectoryError("capability usage sidecar is not a file")

        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect(read_only=True)
            table = connection.execute(_SELECT_TABLE, (_TABLE,)).fetchone()
            if table is None:
                return self._empty_snapshot(initialized=False)
            observations = []
            today = datetime.now(UTC).date()
            cutoff = (today - timedelta(days=CAPABILITY_USAGE_RETENTION_DAYS - 1)).isoformat()
            for row in connection.execute(
                _SELECT_ROWS,
                (cutoff, today.isoformat(), CAPABILITY_USAGE_MAX_ROWS),
            ):
                safe = self._safe_row(row)
                if safe is not None:
                    observations.append(safe)
            observations.sort(
                key=lambda item: (
                    item["day"],
                    item["source"],
                    item["capability"],
                    item["operation"],
                    item["outcome"],
                )
            )
            observed_from = observations[0]["day"] if observations else None
            observed_to = observations[-1]["day"] if observations else None
            return {
                "rows": observations,
                "observed_from": observed_from,
                "observed_to": observed_to,
                "retention_days": CAPABILITY_USAGE_RETENTION_DAYS,
                "duration_resolution_ms": 1,
                "scope": "registry_invocations",
                "completeness": "best_effort",
                "initialized": True,
            }
        except sqlite3.OperationalError:
            # A concurrent cleanup can remove a just-observed sidecar.  Treat
            # that exact missing-file case as no observations; preserve all
            # other database errors for the caller to diagnose.
            if not self._path.exists():
                return self._empty_snapshot(initialized=False)
            raise
        finally:
            if connection is not None:
                connection.close()

    def _prepare_storage(self) -> None:
        _reject_symlink_components(self._path)
        parent = self._path.parent
        parent_was_missing = not parent.exists()
        if parent.exists():
            if not parent.is_dir():
                raise NotADirectoryError("capability usage sidecar parent is not a directory")
        else:
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        _reject_symlink_components(parent)
        # This directory is the explicitly supplied sidecar parent.  Tighten
        # only a newly-created or clearly dedicated leaf; never chmod the
        # runtime root or a broad data tree supplied by the caller.
        if parent_was_missing or parent.name in _DEDICATED_PARENT_NAMES:
            _secure_mode(parent, 0o700)
        if not self._path.exists():
            try:
                descriptor = os.open(
                    self._path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError:
                # Another process won the sidecar creation race.  Re-check
                # the target before allowing SQLite to open it.
                _reject_symlink_components(self._path)
            else:
                os.close(descriptor)
        _reject_symlink_components(self._path)
        if not self._path.is_file():
            raise IsADirectoryError("capability usage sidecar is not a file")
        _secure_mode(self._path, 0o600)

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        # Concurrent first opens can receive SQLITE_BUSY immediately while WAL
        # is being initialized, before SQLite's busy handler can wait. Retry
        # only connection setup, within the existing diagnostic latency bound.
        deadline = time.monotonic() + _BUSY_TIMEOUT_SECONDS
        while True:
            connection: sqlite3.Connection | None = None
            try:
                if read_only:
                    uri = f"file:{quote(str(self._path), safe='/')}?mode=ro"
                    connection = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_SECONDS)
                else:
                    connection = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_SECONDS)
                connection.row_factory = sqlite3.Row
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                connection.execute(f"PRAGMA busy_timeout = {remaining_ms}")
                if not read_only:
                    if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                        connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA synchronous = NORMAL")
                connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
                return connection
            except sqlite3.OperationalError as error:
                if connection is not None:
                    connection.close()
                code = getattr(error, "sqlite_errorcode", 0)
                remaining = deadline - time.monotonic()
                if (
                    code & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                    or remaining <= 0
                ):
                    raise
                time.sleep(min(0.005, remaining))
            except BaseException:
                if connection is not None:
                    connection.close()
                raise

    @staticmethod
    def _safe_row(row: sqlite3.Row) -> dict[str, Any] | None:
        source = row["source"]
        capability = row["capability"]
        operation = row["operation"]
        outcome = row["outcome"]
        calls = row["calls"]
        total_elapsed_ms = row["total_elapsed_ms"]
        max_elapsed_ms = row["max_elapsed_ms"]
        day = row["day"]
        if (
            not isinstance(day, str)
            or _SAFE_DAY.fullmatch(day) is None
            or _date_from_isoformat(day) is None
        ):
            return None
        if source not in CAPABILITY_USAGE_SOURCES:
            return None
        if outcome not in CAPABILITY_USAGE_OUTCOMES:
            return None
        if not _safe_name(capability) or not _safe_name(operation):
            return None
        if (
            type(calls) is not int
            or calls <= 0
            or type(total_elapsed_ms) is not int
            or total_elapsed_ms < 0
            or type(max_elapsed_ms) is not int
            or max_elapsed_ms < 0
        ):
            return None
        return {
            "day": day,
            "source": source,
            "capability": capability,
            "operation": operation,
            "outcome": outcome,
            "calls": calls,
            "total_elapsed_ms": total_elapsed_ms,
            "max_elapsed_ms": max_elapsed_ms,
        }

    @staticmethod
    def _empty_snapshot(*, initialized: bool) -> dict[str, Any]:
        return {
            "rows": [],
            "observed_from": None,
            "observed_to": None,
            "retention_days": CAPABILITY_USAGE_RETENTION_DAYS,
            "duration_resolution_ms": 1,
            "scope": "registry_invocations",
            "completeness": "best_effort",
            "initialized": initialized,
        }


def _safe_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value in CAPABILITY_USAGE_NAME_SENTINELS or _SAFE_NAME.fullmatch(value) is not None


def _date_from_isoformat(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["SqliteCapabilityUsageStore"]
