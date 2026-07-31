from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from infrastructure.persistence.operational_maintenance import SqliteOperationalMaintenance

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migrate(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "heads")


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def test_status_backup_and_expired_cache_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "source.db"
    url = f"sqlite:///{database}"
    _migrate(url, monkeypatch)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO provider_cache "
                "(cache_key,category,market,instrument_id,vendor,payload_json,as_of,"
                "fetched_at,expires_at,freshness,created_at) VALUES "
                "('old','quote','US',NULL,'yahoo','{}','2026-01-01T00:00:00+00:00',"
                "'2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00','stale',"
                "'2026-01-01T00:00:00+00:00'),"
                "('new','quote','US',NULL,'yahoo','{}','2026-07-30T00:00:00+00:00',"
                "'2026-07-30T00:00:00+00:00','2026-08-01T00:00:00+00:00','fresh',"
                "'2026-07-30T00:00:00+00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO reddit_sample_cache "
                "(instrument_id,config_key,payload_json,fetched_at,expires_at,updated_at) "
                "VALUES ('equity:US:NVDA','old','{}','2026-01-01T00:00:00+00:00',"
                "'2026-01-02T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
            )
        )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "manifest.json").write_text("{}", encoding="utf-8")
    service = SqliteOperationalMaintenance(
        engine=engine,
        database_url=url,
        artifact_root=artifact_root,
        backup_root=tmp_path / "backups",
        clock=_Clock(),
    )

    status = service.status()
    assert status.provider_cache_total == 2
    assert status.provider_cache_expired == 1
    assert status.validation_artifact_files == 1
    assert any(item.table == "provider_cache" and item.rows == 2 for item in status.table_counts)

    preview = service.prune_expired_cache(retention_days=30, dry_run=True)
    assert preview.provider_cache_rows == 1
    assert preview.reddit_cache_rows == 1
    assert service.status().provider_cache_total == 2

    applied = service.prune_expired_cache(retention_days=30, dry_run=False)
    assert applied.provider_cache_rows == 1
    assert service.status().provider_cache_total == 1

    backup = service.backup()
    assert backup.filename.endswith(".db")
    assert backup.bytes > 0
    assert (tmp_path / "backups" / backup.filename).stat().st_mode & 0o777 == 0o600
    engine.dispose()
