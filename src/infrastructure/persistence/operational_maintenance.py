"""SQLite-aware local maintenance without exposing credentials or payloads."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine, make_url

from application.dto.maintenance import (
    CachePruneReceiptDTO,
    DatabaseBackupReceiptDTO,
    MaintenanceStatusDTO,
    RetentionRuleDTO,
    TableCountDTO,
)
from application.ports.clock import Clock
from domain.common.errors import DataContractError
from infrastructure.persistence.sqlite_backup import SQLiteBackupService

_SAFE_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")
_RETENTION_RULES = (
    RetentionRuleDTO(area="provider_cache", policy="expired_then_prunable", days=30),
    RetentionRuleDTO(area="reddit_sample_cache", policy="expired_then_prunable", days=30),
    RetentionRuleDTO(area="monitor_runs", policy="keep_forever"),
    RetentionRuleDTO(area="monitor_events", policy="keep_forever"),
    RetentionRuleDTO(area="research_memory", policy="keep_forever"),
    RetentionRuleDTO(area="historical_validation_artifacts", policy="keep_forever"),
    RetentionRuleDTO(area="database_backups", policy="operator_managed"),
)


class SqliteOperationalMaintenance:
    def __init__(
        self,
        *,
        engine: Engine,
        database_url: str,
        artifact_root: Path,
        backup_root: Path,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._database_url = database_url
        self._database_path = self._sqlite_path(database_url)
        self._artifact_root = artifact_root.resolve()
        self._backup_root = backup_root.resolve()
        self._clock = clock

    def status(self) -> MaintenanceStatusDTO:
        now = self._clock.now()
        tables = tuple(sorted(inspect(self._engine).get_table_names()))
        counts: list[TableCountDTO] = []
        with self._engine.connect() as connection:
            for table in tables:
                if not _SAFE_TABLE.fullmatch(table):
                    continue
                value = connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
                counts.append(TableCountDTO(table=table, rows=int(value)))
            cache_total = self._count_where(connection, "provider_cache", None)
            cache_expired = self._count_where(
                connection,
                "provider_cache",
                ("expires_at < :cutoff", {"cutoff": now.isoformat()}),
            )
        artifact_files, artifact_bytes, _ = self._files(self._artifact_root, "*")
        backup_files, _, latest_backup = self._files(self._backup_root, "*.db")
        return MaintenanceStatusDTO(
            generated_at=now,
            database_filename=self._database_path.name,
            database_bytes=(
                self._database_path.stat().st_size if self._database_path.exists() else 0
            ),
            table_counts=tuple(counts),
            provider_cache_total=cache_total,
            provider_cache_expired=cache_expired,
            validation_artifact_files=artifact_files,
            validation_artifact_bytes=artifact_bytes,
            backup_files=backup_files,
            latest_backup_at=latest_backup,
            retention_rules=_RETENTION_RULES,
        )

    def backup(self) -> DatabaseBackupReceiptDTO:
        now = self._clock.now().astimezone(UTC)
        self._backup_root.mkdir(parents=True, exist_ok=True)
        destination = self._backup_root / now.strftime("trading-partner-%Y%m%dT%H%M%S%fZ.db")
        receipt = SQLiteBackupService().backup(self._database_url, destination)
        destination.chmod(0o600)
        return DatabaseBackupReceiptDTO(
            created_at=now,
            filename=destination.name,
            bytes=destination.stat().st_size,
            alembic_revision=receipt.alembic_revision,
            schema_versions=receipt.schema_versions,
        )

    def prune_expired_cache(self, *, retention_days: int, dry_run: bool) -> CachePruneReceiptDTO:
        if not 1 <= retention_days <= 3650:
            raise DataContractError("retention_days must be in [1,3650]")
        now = self._clock.now()
        cutoff = now - timedelta(days=retention_days)
        clause = ("expires_at < :cutoff", {"cutoff": cutoff.isoformat()})
        with self._engine.begin() as connection:
            provider_rows = self._count_where(connection, "provider_cache", clause)
            reddit_rows = self._count_where(connection, "reddit_sample_cache", clause)
            if not dry_run:
                connection.execute(
                    text("DELETE FROM provider_cache WHERE expires_at < :cutoff"),
                    clause[1],
                )
                connection.execute(
                    text("DELETE FROM reddit_sample_cache WHERE expires_at < :cutoff"),
                    clause[1],
                )
        return CachePruneReceiptDTO(
            checked_at=now,
            cutoff=cutoff,
            dry_run=dry_run,
            provider_cache_rows=provider_rows,
            reddit_cache_rows=reddit_rows,
        )

    @staticmethod
    def _count_where(
        connection: Connection,
        table: str,
        clause: tuple[str, dict[str, str]] | None,
    ) -> int:
        if not _SAFE_TABLE.fullmatch(table):
            raise DataContractError("maintenance table name is invalid")
        suffix = f" WHERE {clause[0]}" if clause else ""
        params = clause[1] if clause else {}
        return int(
            connection.execute(text(f'SELECT COUNT(*) FROM "{table}"{suffix}'), params).scalar_one()
        )

    @staticmethod
    def _sqlite_path(database_url: str) -> Path:
        url = make_url(database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise DataContractError("maintenance requires a file-backed SQLite database")
        return Path(url.database).expanduser().resolve()

    @staticmethod
    def _files(root: Path, pattern: str) -> tuple[int, int, datetime | None]:
        if not root.is_dir():
            return 0, 0, None
        count = 0
        size = 0
        latest: float | None = None
        for path in root.rglob(pattern):
            if not path.is_file() or path.is_symlink():
                continue
            stat = path.stat()
            count += 1
            size += stat.st_size
            latest = stat.st_mtime if latest is None else max(latest, stat.st_mtime)
        return count, size, datetime.fromtimestamp(latest, tz=UTC) if latest else None
