"""Phase 1D provider_cache / provider_health / provider_rate_limits schema tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "provider-state-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "provider-state-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


_PHASE1D_TABLES = {
    "instruments",
    "instrument_aliases",
    "provider_cache",
    "provider_health",
    "provider_rate_limits",
}


def test_provider_state_tables_exist_and_accept_rows(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "provider_state.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert _PHASE1D_TABLES.issubset(tables)

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(
            text(
                "INSERT INTO provider_cache("
                "cache_key, category, market, instrument_id, vendor, "
                "payload_json, as_of, fetched_at, expires_at, freshness, created_at"
                ") VALUES ("
                "'k1', 'market_snapshot', 'US', 'equity:US:NVDA', 'mock_us', "
                "'{}', '2026-07-17T12:00:00+00:00', '2026-07-17T12:00:00+00:00', "
                "'2026-07-17T12:05:00+00:00', 'fresh', '2026-07-17T12:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO provider_health("
                "vendor, category, state, success_count, failure_count, "
                "last_success_at, last_failure_at, last_error_code, "
                "circuit_state, updated_at"
                ") VALUES ("
                "'mock_us', 'market_snapshot', 'ok', 1, 0, "
                "'2026-07-17T12:00:00+00:00', NULL, NULL, "
                "'closed', '2026-07-17T12:00:00+00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO provider_rate_limits("
                "vendor, category, window_start, window_seconds, "
                "request_count, limit_count, updated_at"
                ") VALUES ("
                "'mock_us', 'market_snapshot', '2026-07-17T12:00:00+00:00', 60, "
                "1, 100, '2026-07-17T12:00:00+00:00')"
            )
        )

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM provider_cache")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM provider_health")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM provider_rate_limits")).scalar() == 1

    # Composite PK uniqueness for provider_health
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO provider_health("
                "vendor, category, state, success_count, failure_count, "
                "circuit_state, updated_at"
                ") VALUES ("
                "'mock_us', 'market_snapshot', 'error', 0, 1, "
                "'open', '2026-07-17T13:00:00+00:00')"
            )
        )

    # Invalid health state rejected by CHECK
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO provider_health("
                "vendor, category, state, success_count, failure_count, "
                "circuit_state, updated_at"
                ") VALUES ("
                "'mock_a_share', 'market_snapshot', 'weird', 0, 0, "
                "'closed', '2026-07-17T12:00:00+00:00')"
            )
        )

    # Invalid circuit_state rejected by CHECK
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO provider_health("
                "vendor, category, state, success_count, failure_count, "
                "circuit_state, updated_at"
                ") VALUES ("
                "'mock_a_share', 'market_snapshot', 'ok', 0, 0, "
                "'blown', '2026-07-17T12:00:00+00:00')"
            )
        )

    engine.dispose()


def test_instrument_table_checks_and_unique_symbol(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "instrument_checks.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")
    engine = create_engine(database_url)

    def _insert_instrument(
        *,
        instrument_id: str = "equity:A_SHARE:600000.SH",
        symbol: str = "600000.SH",
        market: str = "A_SHARE",
        asset_type: str = "equity",
        name: str = "浦发银行",
    ) -> None:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, symbol, name, market, exchange, currency, "
                    "timezone, asset_type, is_active, listing_status, "
                    "metadata_version, created_at, updated_at"
                    ") VALUES ("
                    ":instrument_id, :symbol, :name, :market, 'SSE', 'CNY', "
                    "'Asia/Shanghai', :asset_type, 1, 'active', "
                    "1, '2026-07-17T12:00:00+00:00', '2026-07-17T12:00:00+00:00')"
                ),
                {
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "name": name,
                    "market": market,
                    "asset_type": asset_type,
                },
            )

    with engine.connect() as conn:
        # Head seeds: 0003 minimum 8 + 0005 QQQ/IWM (including 600519.SH).
        assert conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar() == 10

    # Non-seed row for constraint checks.
    _insert_instrument()

    # UNIQUE(asset_type, market, symbol) — duplicate of non-seed insert
    with pytest.raises(IntegrityError):
        _insert_instrument(instrument_id="equity:A_SHARE:600000.SH_dup")

    # UNIQUE also blocks re-insert of a seeded instrument under a new PK.
    with pytest.raises(IntegrityError):
        _insert_instrument(
            instrument_id="equity:A_SHARE:600519.SH_dup",
            symbol="600519.SH",
            name="贵州茅台",
        )

    # Invalid market
    with pytest.raises(IntegrityError):
        _insert_instrument(
            instrument_id="equity:HK:0001",
            symbol="0001",
            market="HK",
        )

    # Invalid asset_type
    with pytest.raises(IntegrityError):
        _insert_instrument(
            instrument_id="bond:A_SHARE:019547.SH",
            symbol="019547.SH",
            asset_type="bond",
        )

    engine.dispose()
