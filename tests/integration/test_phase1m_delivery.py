from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from domain.common.errors import DataContractError
from evaluation_support import audit_delivery
from infrastructure.persistence.sqlite_backup import SQLiteBackupService
from interfaces.mcp.server import PUBLIC_TOOL_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HEAD_REVISIONS = frozenset({"0071_external_note_reviews"})


def _migrate(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "heads")


def test_sqlite_online_backup_restore_preserves_data_and_schema_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    url = f"sqlite:///{source}"
    _migrate(url, monkeypatch)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO system_audit_log"
                "(audit_id,event_type,request_id,recorded_at,payload_json) "
                "VALUES ('audit_backup','backup.test',NULL,"
                "'2026-07-18T00:00:00+00:00','{}')"
            )
        )
    engine.dispose()

    service = SQLiteBackupService()
    backup_receipt = service.backup(url, backup)
    restore_receipt = service.restore(backup, restored)

    assert backup_receipt.alembic_revision in _HEAD_REVISIONS
    assert restore_receipt.schema_versions == backup_receipt.schema_versions
    restored_engine = create_engine(f"sqlite:///{restored}")
    with restored_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM system_audit_log WHERE audit_id='audit_backup'")
            )
            == 1
        )
    restored_engine.dispose()
    with pytest.raises(DataContractError, match="already exists"):
        service.restore(backup, restored)


def test_compact_phase1_delivery_audit_passes() -> None:
    receipt = audit_delivery(PROJECT_ROOT, PUBLIC_TOOL_NAMES)

    assert receipt.public_tool_count == len(PUBLIC_TOOL_NAMES)
    assert receipt.migration_head == "0071_external_note_reviews"
    assert receipt.dialogue_count == 89
