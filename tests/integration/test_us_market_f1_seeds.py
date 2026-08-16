"""Phase 1F F1: QQQ/IWM proxy seed upgrade and selective downgrade."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

_HEADS = frozenset({"0051_moomoo_margin_semantics"})
_PREV = "0004_phase1c_research_memory"


def _alembic_config(database_url: str, project_root: Path) -> Config:
    # Mirrors tests/integration/test_database_migrations.py helpers.
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "us-f1-seed-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "us-f1-seed-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


_QQQ = "etf:US:QQQ"
_IWM = "etf:US:IWM"
_NVDA = "equity:US:NVDA"
_SPY = "etf:US:SPY"

_QQQ_ALIAS = "alias_00000000-0000-7000-8000-000000000007"
_IWM_ALIAS = "alias_00000000-0000-7000-8000-000000000008"

_EXPECTED_INSTRUMENTS: dict[str, dict[str, object]] = {
    _QQQ: {
        "symbol": "QQQ",
        "name": "Invesco QQQ Trust",
        "market": "US",
        "exchange": "NASDAQ",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "etf",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": "XNAS",
        "underlying_instrument_id": None,
        "tick_size": "0.01",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": "2026-07-18T00:00:00+00:00",
        "updated_at": "2026-07-18T00:00:00+00:00",
    },
    _IWM: {
        "symbol": "IWM",
        "name": "iShares Russell 2000 ETF",
        "market": "US",
        "exchange": "ARCA",
        "currency": "USD",
        "timezone": "America/New_York",
        "asset_type": "etf",
        "is_active": 1,
        "listing_status": "active",
        "country": "US",
        "mic": "ARCX",
        "underlying_instrument_id": None,
        "tick_size": "0.01",
        "lot_size": "1",
        "metadata_version": 1,
        "created_at": "2026-07-18T00:00:00+00:00",
        "updated_at": "2026-07-18T00:00:00+00:00",
    },
}

_EXPECTED_ALIASES: dict[str, dict[str, object]] = {
    _QQQ_ALIAS: {
        "instrument_id": _QQQ,
        "alias_type": "symbol",
        "alias_value": "QQQ",
        "alias_value_raw": "QQQ",
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": "2026-07-18T00:00:00+00:00",
    },
    _IWM_ALIAS: {
        "instrument_id": _IWM,
        "alias_type": "symbol",
        "alias_value": "IWM",
        "alias_value_raw": "IWM",
        "market": "US",
        "source": "local_seed",
        "is_primary": 1,
        "created_at": "2026-07-18T00:00:00+00:00",
    },
}


def test_qqq_iwm_seed_identity_and_downgrade_preserves_prior(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "us_f1_seeds.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)

    command.upgrade(cfg, "heads")
    engine = create_engine(database_url)

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()[0]
        assert rev in _HEADS

        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert "phase1f_us_proxy_seeds" in versions

        for instrument_id, expected in _EXPECTED_INSTRUMENTS.items():
            row = (
                conn.execute(
                    text(
                        "SELECT symbol, name, market, exchange, currency, timezone, "
                        "asset_type, is_active, listing_status, country, mic, "
                        "underlying_instrument_id, tick_size, lot_size, "
                        "metadata_version, created_at, updated_at "
                        "FROM instruments WHERE instrument_id = :id"
                    ),
                    {"id": instrument_id},
                )
                .mappings()
                .one()
            )
            for key, value in expected.items():
                assert row[key] == value, f"{instrument_id}.{key}"

        for alias_id, expected in _EXPECTED_ALIASES.items():
            row = (
                conn.execute(
                    text(
                        "SELECT instrument_id, alias_type, alias_value, alias_value_raw, "
                        "market, source, is_primary, created_at "
                        "FROM instrument_aliases WHERE alias_id = :id"
                    ),
                    {"id": alias_id},
                )
                .mappings()
                .one()
            )
            for key, value in expected.items():
                assert row[key] == value, f"{alias_id}.{key}"

        # Symbol alias resolves to exact instrument identity.
        for symbol, instrument_id in (("QQQ", _QQQ), ("IWM", _IWM)):
            resolved = conn.execute(
                text(
                    "SELECT instrument_id FROM instrument_aliases "
                    "WHERE alias_type = 'symbol' AND alias_value = :sym "
                    "AND market = 'US'"
                ),
                {"sym": symbol},
            ).scalar_one()
            assert resolved == instrument_id

        prior = {
            row[0]
            for row in conn.execute(
                text("SELECT instrument_id FROM instruments WHERE instrument_id IN (:nvda, :spy)"),
                {"nvda": _NVDA, "spy": _SPY},
            ).all()
        }
        assert prior == {_NVDA, _SPY}

    command.downgrade(cfg, _PREV)

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()[0]
        assert rev == _PREV

        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert "phase1f_us_proxy_seeds" not in versions

        removed = conn.execute(
            text("SELECT instrument_id FROM instruments WHERE instrument_id IN (:qqq, :iwm)"),
            {"qqq": _QQQ, "iwm": _IWM},
        ).all()
        assert removed == []

        removed_aliases = conn.execute(
            text("SELECT alias_id FROM instrument_aliases WHERE alias_id IN (:a7, :a8)"),
            {"a7": _QQQ_ALIAS, "a8": _IWM_ALIAS},
        ).all()
        assert removed_aliases == []

        prior = {
            row[0]
            for row in conn.execute(
                text("SELECT instrument_id FROM instruments WHERE instrument_id IN (:nvda, :spy)"),
                {"nvda": _NVDA, "spy": _SPY},
            ).all()
        }
        assert prior == {_NVDA, _SPY}

    engine.dispose()
