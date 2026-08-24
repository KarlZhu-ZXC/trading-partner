"""Phase 1D D3a: InstrumentUnitOfWork, count(), seed loader, migration seed."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from conftest import FixedClock
from domain.common.enums import AliasType, AssetType, Market
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument, InstrumentAlias
from infrastructure.persistence.instrument_seed_loader import InstrumentSeedLoader
from infrastructure.persistence.instrument_unit_of_work import (
    SqlAlchemyInstrumentUnitOfWork,
)
from infrastructure.persistence.seeds import (
    default_instruments_seed_path,
    read_instruments_seed_text,
)

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SEED_PATH = default_instruments_seed_path()

# Phase 1D migration 0003 embedded minimum seed (8 instruments / 6 aliases).
PHASE1D_MINIMUM_SEED_INSTRUMENT_IDS = frozenset(
    {
        "equity:A_SHARE:600519.SH",
        "equity:US:NVDA",
        "etf:A_SHARE:510300.SH",
        "etf:US:SPY",
        "index:A_SHARE:000300.SH",
        "index:US:SPX",
        "option:A_SHARE:10007601.SH",
        "option:US:NVDA260717C00150000",
    }
)

# Packaged runtime seed + head migrations (0003 + 0005 QQQ/IWM).
EXPECTED_SEED_INSTRUMENT_IDS = PHASE1D_MINIMUM_SEED_INSTRUMENT_IDS | frozenset(
    {
        "etf:US:QQQ",
        "etf:US:IWM",
    }
)
RUNTIME_SEED_INSTRUMENT_COUNT = 10
RUNTIME_SEED_ALIAS_COUNT = 8
PHASE1D_MINIMUM_SEED_INSTRUMENT_COUNT = 8
PHASE1D_MINIMUM_SEED_ALIAS_COUNT = 6
PHASE1F_SEED_TS = "2026-07-18T00:00:00+00:00"
_HEADS = frozenset({"0063_agent_image_attachments"})


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine(orm_sqlite_url: str) -> Engine:
    eng = create_engine(orm_sqlite_url)
    _enable_fk(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def uow_factory(engine: Engine):  # type: ignore[no-untyped-def]
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    factory.clock = clock  # type: ignore[attr-defined]
    return factory


def _equity_nvda() -> Instrument:
    return Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "NVDA"),
        symbol="NVDA",
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
        country="US",
        mic="XNAS",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("1"),
    )


def _alias_for(
    instrument: Instrument,
    *,
    alias_id: str,
    alias_type: AliasType,
    alias_value: str,
) -> InstrumentAlias:
    return InstrumentAlias(
        alias_id=alias_id,
        instrument_id=instrument.instrument_id,
        alias_type=alias_type,
        alias_value=alias_value,
        alias_value_raw=alias_value,
        market=instrument.market,
        source="local_seed",
        is_primary=True,
        created_at=NOW,
    )


# --- count() -----------------------------------------------------------------


def test_repository_count_empty_and_after_upsert(uow_factory) -> None:  # type: ignore[no-untyped-def]
    with uow_factory() as uow:
        assert uow.instruments.count() == 0
        uow.instruments.upsert_instrument(_equity_nvda())
        assert uow.instruments.count() == 1
        uow.commit()

    with uow_factory() as uow:
        assert uow.instruments.count() == 1


# --- Unit of Work ------------------------------------------------------------


def test_uow_commit_persists_instrument_and_aliases(
    uow_factory,  # type: ignore[no-untyped-def]
) -> None:
    inst = _equity_nvda()
    alias = _alias_for(
        inst,
        alias_id="alias_00000000-0000-7000-8000-000000000099",
        alias_type=AliasType.NAME_EN,
        alias_value="nvidia",
    )
    with uow_factory() as uow:
        uow.instruments.upsert_instrument(inst)
        uow.instruments.upsert_alias(alias)
        uow.commit()

    with uow_factory() as uow:
        assert uow.instruments.get_by_id(inst.instrument_id) is not None
        aliases = uow.instruments.list_aliases(inst.instrument_id)
        assert len(aliases) == 1
        assert aliases[0].alias_value == "nvidia"


def test_uow_exit_without_commit_rolls_back(uow_factory) -> None:  # type: ignore[no-untyped-def]
    inst = _equity_nvda()
    with uow_factory() as uow:
        uow.instruments.upsert_instrument(inst)
        assert uow.instruments.count() == 1
        # no commit

    with uow_factory() as uow:
        assert uow.instruments.count() == 0
        assert uow.instruments.get_by_id(inst.instrument_id) is None


def test_uow_exception_atomicity_instrument_and_aliases(
    uow_factory,  # type: ignore[no-untyped-def]
) -> None:
    inst = _equity_nvda()
    alias = _alias_for(
        inst,
        alias_id="alias_00000000-0000-7000-8000-000000000098",
        alias_type=AliasType.NAME_EN,
        alias_value="nvidia",
    )
    with pytest.raises(RuntimeError, match="force fail"), uow_factory() as uow:
        uow.instruments.upsert_instrument(inst)
        uow.instruments.upsert_alias(alias)
        raise RuntimeError("force fail")

    with uow_factory() as uow:
        assert uow.instruments.count() == 0
        assert uow.instruments.list_aliases(inst.instrument_id) == ()


def test_uow_explicit_rollback(uow_factory) -> None:  # type: ignore[no-untyped-def]
    inst = _equity_nvda()
    with uow_factory() as uow:
        uow.instruments.upsert_instrument(inst)
        uow.rollback()
        assert uow.instruments.count() == 0
        uow.commit()  # empty commit ok

    with uow_factory() as uow:
        assert uow.instruments.count() == 0


# --- Seed loader -------------------------------------------------------------


def test_seed_loader_empty_loads_minimum(uow_factory) -> None:  # type: ignore[no-untyped-def]
    loader = InstrumentSeedLoader()
    with uow_factory() as uow:
        loaded = loader.load_if_empty(uow.instruments, RUNTIME_SEED_PATH)
        assert loaded == RUNTIME_SEED_INSTRUMENT_COUNT
        assert uow.instruments.count() == RUNTIME_SEED_INSTRUMENT_COUNT
        uow.commit()

    with uow_factory() as uow:
        assert uow.instruments.count() == RUNTIME_SEED_INSTRUMENT_COUNT
        ids = {
            row.instrument_id
            for row in (uow.instruments.get_by_id(i) for i in EXPECTED_SEED_INSTRUMENT_IDS)
            if row is not None
        }
        assert ids == EXPECTED_SEED_INSTRUMENT_IDS
        maotai = uow.instruments.get_by_id("equity:A_SHARE:600519.SH")
        assert maotai is not None
        assert maotai.name == "贵州茅台"
        aliases = uow.instruments.list_aliases("equity:A_SHARE:600519.SH")
        values = {a.alias_value for a in aliases}
        assert "600519" in values
        assert "茅台" in values
        spx_aliases = uow.instruments.list_aliases("index:US:SPX")
        assert any(a.alias_value == "^GSPC" for a in spx_aliases)
        nvda_opt = uow.instruments.get_by_id("option:US:NVDA260717C00150000")
        assert nvda_opt is not None
        assert nvda_opt.underlying_instrument_id == "equity:US:NVDA"
        assert nvda_opt.multiplier == Decimal("100")
        spy = uow.instruments.get_by_id("etf:US:SPY")
        assert spy is not None
        assert spy.underlying_instrument_id == "index:US:SPX"
        qqq = uow.instruments.get_by_id("etf:US:QQQ")
        assert qqq is not None
        assert qqq.name == "Invesco QQQ Trust"
        iwm = uow.instruments.get_by_id("etf:US:IWM")
        assert iwm is not None
        assert iwm.name == "iShares Russell 2000 ETF"


def test_seed_loader_nonempty_skips_entire_load(uow_factory) -> None:  # type: ignore[no-untyped-def]
    loader = InstrumentSeedLoader()
    with uow_factory() as uow:
        uow.instruments.upsert_instrument(_equity_nvda())
        uow.commit()

    with uow_factory() as uow:
        loaded = loader.load_if_empty(uow.instruments, RUNTIME_SEED_PATH)
        assert loaded == 0
        assert uow.instruments.count() == 1
        # Seed instruments not partially inserted
        assert uow.instruments.get_by_id("equity:A_SHARE:600519.SH") is None


def test_seed_loader_idempotent_when_already_seeded(
    uow_factory,  # type: ignore[no-untyped-def]
) -> None:
    loader = InstrumentSeedLoader()
    with uow_factory() as uow:
        assert (
            loader.load_if_empty(uow.instruments, RUNTIME_SEED_PATH)
            == RUNTIME_SEED_INSTRUMENT_COUNT
        )
        uow.commit()

    with uow_factory() as uow:
        assert loader.load_if_empty(uow.instruments, RUNTIME_SEED_PATH) == 0
        assert uow.instruments.count() == RUNTIME_SEED_INSTRUMENT_COUNT


def test_seed_loader_does_not_commit(uow_factory) -> None:  # type: ignore[no-untyped-def]
    loader = InstrumentSeedLoader()
    with uow_factory() as uow:
        assert (
            loader.load_if_empty(uow.instruments, RUNTIME_SEED_PATH)
            == RUNTIME_SEED_INSTRUMENT_COUNT
        )
        # exit without commit → rollback

    with uow_factory() as uow:
        assert uow.instruments.count() == 0


# --- Migration seed + parity -------------------------------------------------


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _set_migration_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    import os

    for key in list(os.environ):
        if key in __import__("conftest").APP_SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_NAME", "migration-seed-test")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MCP_SERVER_NAME", "migration-seed-test")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "5")


def _load_migration_module(revision_filename: str, module_name: str):  # type: ignore[no-untyped-def]
    path = PROJECT_ROOT / "migrations" / "versions" / revision_filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fresh_0003_migration_seeds_minimum_instruments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin upgrade target to 0003 so later heads (0005 QQQ/IWM) do not change meaning."""
    db_path = tmp_path / "seeded.db"
    database_url = f"sqlite:///{db_path}"
    _set_migration_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url)

    command.upgrade(cfg, "0003_phase1d_instrument_provider")
    engine = create_engine(database_url)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        inst_count = conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar()
        alias_count = conn.execute(text("SELECT COUNT(*) FROM instrument_aliases")).scalar()
        assert inst_count == PHASE1D_MINIMUM_SEED_INSTRUMENT_COUNT
        assert alias_count == PHASE1D_MINIMUM_SEED_ALIAS_COUNT
        ids = {row[0] for row in conn.execute(text("SELECT instrument_id FROM instruments")).all()}
        assert ids == PHASE1D_MINIMUM_SEED_INSTRUMENT_IDS
        gspc = conn.execute(
            text("SELECT instrument_id FROM instrument_aliases WHERE alias_value = :v"),
            {"v": "^GSPC"},
        ).one()
        assert gspc[0] == "index:US:SPX"
        under = conn.execute(
            text("SELECT underlying_instrument_id FROM instruments WHERE instrument_id = :id"),
            {"id": "option:US:NVDA260717C00150000"},
        ).one()
        assert under[0] == "equity:US:NVDA"
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] == "0003_phase1d_instrument_provider"
    engine.dispose()


def test_downgrade_to_0002_and_reupgrade_reseeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed rebuild: explicit downgrade to 0002, then re-upgrade to current head.

    Uses absolute revision ``0002_phase1b_research_state`` (not relative ``-1``)
    so intermediate steps stay stable; current migration head is
    0063_agent_image_attachments.
    """
    db_path = tmp_path / "reseed.db"
    database_url = f"sqlite:///{db_path}"
    _set_migration_env(monkeypatch, database_url)
    cfg = _alembic_config(database_url)

    command.upgrade(cfg, "heads")
    command.downgrade(cfg, "0002_phase1b_research_state")
    engine = create_engine(database_url)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
        }
        assert "instruments" not in tables
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] == "0002_phase1b_research_state"
    engine.dispose()

    command.upgrade(cfg, "heads")
    engine = create_engine(database_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM instruments")).scalar() == (
            RUNTIME_SEED_INSTRUMENT_COUNT + 10
        )
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM instruments WHERE asset_type = 'future'")
            ).scalar()
            == 6
        )
        assert (
            conn.execute(text("SELECT COUNT(*) FROM instrument_aliases")).scalar()
            == RUNTIME_SEED_ALIAS_COUNT + 16
        )
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).one()
        assert rev[0] in _HEADS
        versions = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_versions")).all()
        }
        assert "phase1d_instrument_provider" in versions
        assert "phase1c_research_memory" in versions
        assert "phase1f_us_proxy_seeds" in versions
    engine.dispose()


def test_runtime_json_and_migration_seed_parity() -> None:
    """Detect drift between packaged JSON seed and embedded seeds (0003 + 0005)."""
    runtime = json.loads(RUNTIME_SEED_PATH.read_text(encoding="utf-8"))
    assert runtime["schema_version"] == 1
    assert runtime["seeded_at"] == "2026-07-16T00:00:00+00:00"
    runtime_instruments = runtime["instruments"]
    assert len(runtime_instruments) == RUNTIME_SEED_INSTRUMENT_COUNT

    mod_0003 = _load_migration_module(
        "0003_phase1d_instrument_provider.py",
        "phase1d_migration_0003",
    )
    mod_0005 = _load_migration_module(
        "0005_phase1f_us_proxy_seeds.py",
        "phase1f_migration_0005",
    )
    embedded_inst = list(mod_0003._EMBEDDED_SEED_INSTRUMENTS) + list(
        mod_0005._EMBEDDED_SEED_INSTRUMENTS
    )
    embedded_aliases = list(mod_0003._EMBEDDED_SEED_ALIASES) + list(mod_0005._EMBEDDED_SEED_ALIASES)

    # Instrument field parity (bool in JSON ↔ 0/1 in migration)
    runtime_by_id = {i["instrument_id"]: i for i in runtime_instruments}
    embedded_by_id = {i["instrument_id"]: i for i in embedded_inst}
    assert set(runtime_by_id) == set(embedded_by_id) == EXPECTED_SEED_INSTRUMENT_IDS

    instrument_compare_keys = (
        "instrument_id",
        "symbol",
        "name",
        "market",
        "exchange",
        "currency",
        "timezone",
        "asset_type",
        "listing_status",
        "country",
        "mic",
        "underlying_instrument_id",
        "multiplier",
        "tick_size",
        "lot_size",
        "metadata_version",
    )
    for iid in EXPECTED_SEED_INSTRUMENT_IDS:
        rt = runtime_by_id[iid]
        emb = embedded_by_id[iid]
        for key in instrument_compare_keys:
            assert rt.get(key) == emb.get(key), f"{iid}.{key}: {rt.get(key)!r} != {emb.get(key)!r}"
        rt_active = 1 if rt["is_active"] else 0
        assert emb["is_active"] == rt_active
        # 0003 rows use package seeded_at; 0005 rows use Phase 1F fixed seed ts.
        expected_ts = (
            runtime["seeded_at"] if iid in PHASE1D_MINIMUM_SEED_INSTRUMENT_IDS else PHASE1F_SEED_TS
        )
        assert emb["created_at"] == expected_ts
        assert emb["updated_at"] == expected_ts

    # Alias parity (timestamps follow the migration that introduced the row)
    runtime_aliases: list[dict] = []
    for inst in runtime_instruments:
        seed_ts = (
            runtime["seeded_at"]
            if inst["instrument_id"] in PHASE1D_MINIMUM_SEED_INSTRUMENT_IDS
            else PHASE1F_SEED_TS
        )
        for alias in inst.get("aliases", []):
            runtime_aliases.append(
                {
                    "alias_id": alias["alias_id"],
                    "instrument_id": inst["instrument_id"],
                    "alias_type": alias["alias_type"],
                    "alias_value": alias["alias_value"],
                    "alias_value_raw": alias["alias_value_raw"],
                    "market": inst["market"],
                    "source": alias["source"],
                    "is_primary": 1 if alias["is_primary"] else 0,
                    "created_at": seed_ts,
                }
            )

    def _alias_key(a: dict) -> str:
        return a["alias_id"]

    runtime_aliases_sorted = sorted(runtime_aliases, key=_alias_key)
    embedded_aliases_sorted = sorted(embedded_aliases, key=_alias_key)
    assert len(runtime_aliases_sorted) == len(embedded_aliases_sorted) == RUNTIME_SEED_ALIAS_COUNT
    for rt_a, emb_a in zip(runtime_aliases_sorted, embedded_aliases_sorted, strict=True):
        assert rt_a == emb_a


def test_seed_resource_accessible_via_importlib() -> None:
    text = read_instruments_seed_text()
    payload = json.loads(text)
    assert len(payload["instruments"]) == RUNTIME_SEED_INSTRUMENT_COUNT
    assert RUNTIME_SEED_PATH.is_file()
    assert RUNTIME_SEED_PATH.read_text(encoding="utf-8") == text


def test_migration_module_does_not_read_runtime_json() -> None:
    """Smoke: migration module must stay self-contained (no runtime seed I/O)."""
    src = (
        PROJECT_ROOT / "migrations" / "versions" / "0003_phase1d_instrument_provider.py"
    ).read_text(encoding="utf-8")
    # Docstring may name the parity file; executable code must not load it.
    assert "json.load" not in src
    assert "json.loads" not in src
    assert "read_text" not in src
    assert "Path(" not in src
    assert "open(" not in src
    assert "importlib" not in src
    assert "_EMBEDDED_SEED_INSTRUMENTS" in src
