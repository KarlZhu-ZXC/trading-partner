"""Read-only database connection and migration-head preflight tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import AppEnvironment, LogLevel
from domain.common.errors import MigrationError
from infrastructure.composition.persistence import build_persistence_infrastructure
from infrastructure.config.settings import AppSettings
from infrastructure.persistence.database import (
    CURRENT_MIGRATION_HEAD,
    SqlAlchemyDatabase,
    create_engine_from_url,
)
from infrastructure.system.redactor import DefaultSecretRedactor


def _production_settings(database_url: str) -> AppSettings:
    return AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_name="migration-preflight-test",
        app_env=AppEnvironment.PRODUCTION,
        log_level=LogLevel.INFO,
        database_url=database_url,
        mcp_server_name="migration-preflight-test",
        default_timezone="UTC",
        provider_timeout_seconds=5.0,
    )


def _database_with_revision(tmp_path: Path, revision: str | None) -> SqlAlchemyDatabase:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'preflight.db'}")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        if revision is not None:
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
                {"revision": revision},
            )
    return SqlAlchemyDatabase(engine)


def test_check_connection_accepts_current_migration_head(tmp_path: Path) -> None:
    database = _database_with_revision(tmp_path, CURRENT_MIGRATION_HEAD)
    try:
        assert database.migration_heads() == (CURRENT_MIGRATION_HEAD,)
        database.check_connection()
    finally:
        database.close()


def test_check_connection_rejects_missing_migration_state(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'missing.db'}")
    database = SqlAlchemyDatabase(engine)
    try:
        with pytest.raises(MigrationError) as raised:
            database.check_connection()
        assert raised.value.details == {
            "expected_head": CURRENT_MIGRATION_HEAD,
            "actual_heads": (),
            "state": "missing_or_unreadable",
        }
    finally:
        database.close()


@pytest.mark.parametrize(
    "revision",
    ("0071_external_note_reviews", "0099_future_schema"),
    ids=("outdated", "newer"),
)
def test_check_connection_rejects_incompatible_migration_head(
    tmp_path: Path, revision: str
) -> None:
    database = _database_with_revision(tmp_path, revision)
    try:
        with pytest.raises(MigrationError) as raised:
            database.check_connection()
        assert raised.value.details["expected_head"] == CURRENT_MIGRATION_HEAD
        assert raised.value.details["actual_heads"] == (revision,)
        assert "incompatible" in raised.value.message
    finally:
        database.close()


def test_check_connection_rejects_multiple_migration_heads(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'multiple.db'}")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            [{"revision": CURRENT_MIGRATION_HEAD}, {"revision": "0099_future_schema"}],
        )
    database = SqlAlchemyDatabase(engine)
    try:
        with pytest.raises(MigrationError):
            database.check_connection()
    finally:
        database.close()


def test_production_persistence_build_preflights_before_repository_reads(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'startup-preflight.db'}"
    settings = _production_settings(database_url)

    with pytest.raises(MigrationError):
        build_persistence_infrastructure(
            settings,
            clock=FixedClock(),
            id_generator=SequentialIdGenerator(),
            secret_redactor=DefaultSecretRedactor(),
        )
