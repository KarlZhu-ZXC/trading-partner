"""SQLAlchemy engine factory and Database port implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from application.ports.database import CURRENT_MIGRATION_HEAD
from domain.common.errors import MigrationError, PersistenceError


def ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create the parent directory for a SQLite file URL if needed."""
    if not database_url.startswith("sqlite:///"):
        return
    path_part = database_url.removeprefix("sqlite:///")
    # sqlite:///:memory: or empty
    if path_part in {":memory:", ""} or path_part.startswith("file:"):
        return
    db_path = Path(path_part)
    if db_path.parent and str(db_path.parent) not in {".", ""}:
        db_path.parent.mkdir(parents=True, exist_ok=True)


def create_engine_from_url(database_url: str) -> Engine:
    """Create a synchronous SQLAlchemy engine for the given URL."""
    ensure_sqlite_parent_dir(database_url)
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # Agent message appends and cursor CAS updates can briefly contend
        # across Console/Telegram processes.  Let SQLite wait for the writer
        # instead of surfacing an avoidable ``database is locked`` error.
        connect_args["timeout"] = 30.0
    engine = create_engine(database_url, future=True, connect_args=connect_args)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    """Make relational integrity deterministic on every pooled SQLite connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        # WAL lets Console, Telegram, and scheduled readers continue while one
        # short writer transaction commits. SQLite remains the local-first
        # database; this is not a move toward a remote server dependency.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA wal_autocheckpoint=1000")
    finally:
        cursor.close()


class SqlAlchemyDatabase:
    """Phase 1A Database port — connection health only."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    def check_connection(self) -> None:
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                self._check_migration_head(conn)
        except MigrationError:
            raise
        except Exception as exc:  # noqa: BLE001 — wrap as PersistenceError
            raise PersistenceError(
                f"Database connection check failed: {type(exc).__name__}",
                details={"error_type": type(exc).__name__},
            ) from exc

    def migration_heads(self) -> tuple[str, ...]:
        """Read the persisted Alembic heads without validating or mutating them."""

        try:
            with self._engine.connect() as conn:
                return self._read_migration_heads(conn)
        except MigrationError:
            raise
        except Exception as exc:  # noqa: BLE001 — wrap as PersistenceError
            raise PersistenceError(
                f"Database migration state read failed: {type(exc).__name__}",
                details={"error_type": type(exc).__name__},
            ) from exc

    @staticmethod
    def _check_migration_head(conn: Any) -> None:
        """Require the database to be at the one application migration head.

        This is deliberately a read-only check. Database initialization and
        upgrades belong to the explicit initialize/maintenance commands; a
        health read must never mutate the schema or infer that an upgrade is
        safe. The error details contain only opaque revision identifiers and a
        stable state label, so a database URL or driver exception cannot leak.
        """

        raw_heads = SqlAlchemyDatabase._read_migration_heads(conn)

        actual_heads = tuple(sorted(raw_heads))
        expected_heads = (CURRENT_MIGRATION_HEAD,)
        if actual_heads != expected_heads:
            state = "outdated" if not actual_heads else "incompatible"
            if any(value not in expected_heads for value in actual_heads):
                state = "outdated_or_newer"
            raise MigrationError(
                "Database migration head is incompatible with this runtime",
                details={
                    "expected_head": CURRENT_MIGRATION_HEAD,
                    "actual_heads": actual_heads,
                    "state": state,
                },
            )

    @staticmethod
    def _read_migration_heads(conn: Any) -> tuple[str, ...]:
        """Read and minimally validate Alembic head values from one connection."""

        try:
            raw_heads = tuple(
                conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            )
        except SQLAlchemyError as exc:
            raise MigrationError(
                "Database migration state is unavailable",
                details={
                    "expected_head": CURRENT_MIGRATION_HEAD,
                    "actual_heads": (),
                    "state": "missing_or_unreadable",
                },
            ) from exc

        if any(not isinstance(value, str) or not value.strip() for value in raw_heads):
            actual_heads = tuple(
                value.strip()
                for value in raw_heads
                if isinstance(value, str) and value.strip()
            )
            raise MigrationError(
                "Database migration state is invalid",
                details={
                    "expected_head": CURRENT_MIGRATION_HEAD,
                    "actual_heads": actual_heads,
                    "state": "invalid",
                },
            )
        return tuple(value.strip() for value in raw_heads)

    def close(self) -> None:
        self._engine.dispose()
