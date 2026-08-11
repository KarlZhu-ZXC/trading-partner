"""Verified online SQLite backup and non-overwriting restore."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

from domain.common.errors import DataContractError


@dataclass(frozen=True, slots=True)
class SQLiteBackupReceipt:
    destination: Path
    alembic_revision: str
    schema_versions: tuple[str, ...]


class SQLiteBackupService:
    def backup(self, database_url: str, destination: Path) -> SQLiteBackupReceipt:
        source = self._database_path(database_url)
        target = destination.expanduser().resolve()
        if not source.is_file():
            raise DataContractError("SQLite source database does not exist")
        self._require_new_target(source, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._read_only(source)) as source_db, closing(
                sqlite3.connect(target)
            ) as target_db:
                source_db.backup(target_db)
                target_db.commit()
            return self._receipt(target)
        except Exception:
            if target.exists():
                target.unlink()
            raise

    def restore(self, backup: Path, destination: Path) -> SQLiteBackupReceipt:
        source = backup.expanduser().resolve()
        target = destination.expanduser().resolve()
        if not source.is_file():
            raise DataContractError("SQLite backup does not exist")
        source_receipt = self._receipt(source)
        self._require_new_target(source, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._read_only(source)) as source_db, closing(
                sqlite3.connect(target)
            ) as target_db:
                source_db.backup(target_db)
                target_db.commit()
            restored = self._receipt(target)
        except Exception:
            if target.exists():
                target.unlink()
            raise
        if (
            restored.alembic_revision != source_receipt.alembic_revision
            or restored.schema_versions != source_receipt.schema_versions
        ):
            target.unlink(missing_ok=True)
            raise DataContractError("Restored SQLite schema identity does not match backup")
        return restored

    @staticmethod
    def _database_path(database_url: str) -> Path:
        try:
            url = make_url(database_url)
        except Exception:
            raise DataContractError("database_url is invalid") from None
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise DataContractError("online backup requires a file-backed SQLite database")
        return Path(url.database).expanduser().resolve()

    @staticmethod
    def _require_new_target(source: Path, target: Path) -> None:
        if source == target:
            raise DataContractError("SQLite source and destination must differ")
        if target.exists():
            raise DataContractError("SQLite backup/restore destination already exists")

    @staticmethod
    def _read_only(path: Path) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    @staticmethod
    def _receipt(path: Path) -> SQLiteBackupReceipt:
        with closing(SQLiteBackupService._read_only(path)) as database:
            integrity = database.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise DataContractError("SQLite integrity_check failed")
            try:
                revision_row = database.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()
                versions = tuple(
                    row[0]
                    for row in database.execute(
                        "SELECT version FROM schema_versions ORDER BY version"
                    )
                )
            except sqlite3.DatabaseError:
                raise DataContractError("SQLite schema identity is unavailable") from None
        if revision_row is None or not isinstance(revision_row[0], str) or not versions:
            raise DataContractError("SQLite schema identity is incomplete")
        return SQLiteBackupReceipt(path, revision_row[0], versions)
