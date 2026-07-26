"""Proportionate Phase 3A-0 migration checks for definition tables."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "migration-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "migration-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def test_phase3a_definition_tables_and_constraints(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "phase3a.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    expected = {
        "futures_products",
        "futures_product_versions",
        "futures_contracts",
        "futures_contract_versions",
        "futures_contract_statistics",
        "continuous_series_definitions",
        "continuous_contract_mappings",
    }
    assert expected.issubset(tables)

    with engine.begin() as conn:
        # Legacy Yahoo continuous proxy identity remains representable.
        count = conn.execute(
            text("SELECT COUNT(*) FROM instruments WHERE instrument_id = 'future:US:GC=F'")
        ).scalar()
        assert count == 1

        # New market/asset wire values can be stored.
        conn.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, symbol, name, market, exchange, currency, "
                "timezone, asset_type, is_active, listing_status, "
                "metadata_version, created_at, updated_at"
                ") VALUES ("
                "'future:CME:GCZ26', 'GCZ26', 'COMEX Gold Dec 2026', 'CME', "
                "'COMEX', 'USD', 'America/New_York', 'future', 1, 'active', "
                "1, '2026-07-25T00:00:00+00:00', '2026-07-25T00:00:00+00:00')"
            )
        )
        seeded_spot = conn.execute(
            text(
                "SELECT COUNT(*) FROM instruments "
                "WHERE instrument_id = 'commodity_spot:OTC:XAUUSD'"
            )
        ).scalar()
        assert seeded_spot == 1
        conn.execute(
            text(
                "INSERT INTO futures_products("
                "product_id, product_key, market, root, created_at"
                ") VALUES ("
                "'futures_product_01901945-7f5d-7cc3-98c4-dc0c0c07398f', "
                "'CME:GC', 'CME', 'GC', '2026-07-25T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO futures_contracts("
                "instrument_id, product_id, contract_month, created_at"
                ") VALUES ("
                "'future:CME:GCZ26', "
                "'futures_product_01901945-7f5d-7cc3-98c4-dc0c0c07398f', "
                "'2026-12', '2026-07-25T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO continuous_series_definitions("
                "instrument_id, product_id, roll_rule, rank, adjustment, "
                "provider_methodology_version, valid_from, created_at"
                ") VALUES ("
                "'future:CME:GC.v.0', "
                "'futures_product_01901945-7f5d-7cc3-98c4-dc0c0c07398f', "
                "'volume', 0, 'none', 'tp_roll_v1', "
                "'2026-01-01T00:00:00+00:00', '2026-07-25T00:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO continuous_contract_mappings("
                "continuous_instrument_id, contract_instrument_id, "
                "effective_from, mapping_source"
                ") VALUES ("
                "'future:CME:GC.v.0', 'future:CME:GCZ26', "
                "'2026-07-01T00:00:00+00:00', 'fixture')"
            )
        )

    with engine.connect() as conn:
        mapped = conn.execute(
            text(
                "SELECT contract_instrument_id FROM continuous_contract_mappings "
                "WHERE continuous_instrument_id = 'future:CME:GC.v.0'"
            )
        ).scalar()
        assert mapped == "future:CME:GCZ26"

    engine.dispose()
