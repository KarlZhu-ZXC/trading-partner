"""SQLAlchemy engine factory and Database port implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from domain.common.errors import PersistenceError


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
        except Exception as exc:  # noqa: BLE001 — wrap as PersistenceError
            raise PersistenceError(
                f"Database connection check failed: {type(exc).__name__}",
                details={"error_type": type(exc).__name__},
            ) from exc

    def close(self) -> None:
        self._engine.dispose()
