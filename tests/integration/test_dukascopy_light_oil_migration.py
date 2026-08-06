"""Focused checks for the Dukascopy light-oil instrument seed migration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from application.services.instrument_master_service import InstrumentMasterService
from conftest import FixedClock
from domain.common.enums import AssetType, Market, ResolveMatchType
from infrastructure.persistence.instrument_unit_of_work import (
    SqlAlchemyInstrumentUnitOfWork,
)


def _alembic_config(database_url: str, project_root: Path) -> Config:
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_test_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "dukascopy-light-oil-migration-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "dukascopy-light-oil-migration-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def test_light_oil_seed_alias_resolves_and_downgrades_selectively(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "dukascopy-light-oil.db"
    database_url = f"sqlite:///{db_path}"
    _set_test_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url, project_root)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        instrument = connection.execute(
            text(
                "SELECT instrument_id, symbol, name, market, exchange, currency, "
                "timezone, asset_type, tick_size FROM instruments "
                "WHERE instrument_id = 'cfd:OTC:LIGHT_CMD_USD'"
            )
        ).one()
        assert instrument == (
            "cfd:OTC:LIGHT_CMD_USD",
            "LIGHT_CMD_USD",
            "Dukascopy Light Oil Rolling CFD (not WTI spot, not a NYMEX future)",
            "OTC",
            "DUKASCOPY_SWFX",
            "USD",
            "UTC",
            "cfd",
            "0.001",
        )
        aliases = connection.execute(
            text(
                "SELECT alias_value, is_primary FROM instrument_aliases "
                "WHERE instrument_id = 'cfd:OTC:LIGHT_CMD_USD' "
                "ORDER BY alias_id"
            )
        ).all()
        assert aliases == [
            ("LIGHT_CMD_USD", 1),
            ("USOIL", 0),
            ("LIGHT.CMD/USD", 0),
            ("LIGHT.CMD-USD", 0),
        ]

    service = InstrumentMasterService(
        lambda: SqlAlchemyInstrumentUnitOfWork(engine, FixedClock())
    )
    outcome = service.resolve(
        market=Market.OTC,
        query="USOIL",
        asset_type_hint=AssetType.CFD,
    )
    assert outcome.match_type is ResolveMatchType.ALIAS
    assert outcome.instrument is not None
    assert outcome.instrument.instrument_id == "cfd:OTC:LIGHT_CMD_USD"

    command.downgrade(cfg, "0028_provider_route_history")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM instruments "
                    "WHERE instrument_id = 'cfd:OTC:LIGHT_CMD_USD'"
                )
            ).scalar_one()
            == 0
        )
        # Downgrade must not remove the prior Phase 3A copper seed.
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM instruments "
                    "WHERE instrument_id = 'cfd:OTC:COPPER_CMD_USD'"
                )
            ).scalar_one()
            == 1
        )
    engine.dispose()
