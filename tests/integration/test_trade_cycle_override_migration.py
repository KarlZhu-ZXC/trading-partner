"""Phase 4B manual Trade Cycle override migration checks."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config(database_url: str, project_root: Path) -> Config:
    value = Config(str(project_root / "alembic.ini"))
    value.set_main_option("script_location", str(project_root / "migrations"))
    value.set_main_option("sqlalchemy.url", database_url)
    return value


def _env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "trade-cycle-override-migration-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "trade-cycle-override-migration-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def test_trade_cycle_override_migration_round_trip(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'trade-cycle-overrides.db'}"
    _env(monkeypatch, database_url)
    config = _config(database_url, project_root)

    command.upgrade(config, "0058_trade_cycle_overrides")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "trade_cycle_override_revisions" in inspector.get_table_names()
    columns = {item["name"] for item in inspector.get_columns("trade_cycle_override_revisions")}
    assert {
        "override_id",
        "root_cycle_id",
        "version",
        "operation",
        "cycle_ids_json",
        "activity_ids_json",
        "split_groups_json",
        "algorithm_version",
        "actor",
        "expected_version",
    }.issubset(columns)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            "0058_trade_cycle_overrides"
        )

    command.downgrade(config, "0057_unlinked_activity_annotations")
    assert "trade_cycle_override_revisions" not in inspect(engine).get_table_names()
    command.upgrade(config, "0058_trade_cycle_overrides")
    assert "trade_cycle_override_revisions" in inspect(engine).get_table_names()
    engine.dispose()
