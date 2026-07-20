"""Unit/integration tests for InstrumentMasterService (Phase 1D D3b)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.services.instrument_master_service import (
    InstrumentMasterService,
)
from conftest import FixedClock
from domain.common.enums import AliasType, AssetType, Market, ResolveMatchType
from domain.common.errors import DataContractError, InvalidInstrument, PersistenceError
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument, InstrumentAlias
from infrastructure.persistence.instrument_seed_loader import InstrumentSeedLoader
from infrastructure.persistence.instrument_unit_of_work import (
    SqlAlchemyInstrumentUnitOfWork,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.seeds import default_instruments_seed_path

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
SEED_PATH = default_instruments_seed_path()


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    path = tmp_path / "master.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def master(engine: Engine) -> InstrumentMasterService:
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    service = InstrumentMasterService(factory)
    loader = InstrumentSeedLoader()
    with factory() as uow:
        assert loader.load_if_empty(uow.instruments, SEED_PATH) == 10
        uow.commit()
    return service


def _alias(
    *,
    alias_id: str,
    instrument_id: str,
    alias_type: AliasType,
    alias_value: str,
    market: Market,
    is_primary: bool = False,
) -> InstrumentAlias:
    return InstrumentAlias(
        alias_id=alias_id,
        instrument_id=instrument_id,
        alias_type=alias_type,
        alias_value=alias_value,
        alias_value_raw=alias_value,
        market=market,
        source="user",
        is_primary=is_primary,
        created_at=NOW,
    )


# --- instrument_id -----------------------------------------------------------


def test_exact_instrument_id(master: InstrumentMasterService) -> None:
    out = master.resolve(
        market=Market.US,
        query="equity:US:NVDA",
    )
    assert out.match_type is ResolveMatchType.EXACT_INSTRUMENT_ID
    assert out.instrument is not None
    assert out.instrument.instrument_id == "equity:US:NVDA"


def test_wrong_market_instrument_id_fails(master: InstrumentMasterService) -> None:
    with pytest.raises(InvalidInstrument, match="market"):
        master.resolve(
            market=Market.A_SHARE,
            query="equity:US:NVDA",
        )


def test_missing_instrument_id_fails(master: InstrumentMasterService) -> None:
    with pytest.raises(InvalidInstrument, match="not found"):
        master.resolve(
            market=Market.US,
            query="equity:US:NOTREAL",
        )


def test_invalid_instrument_id_shape_fails(master: InstrumentMasterService) -> None:
    with pytest.raises(InvalidInstrument, match="invalid"):
        master.resolve(
            market=Market.US,
            query="nope:US:NVDA",
        )


def test_get_missing_raises(master: InstrumentMasterService) -> None:
    with pytest.raises(InvalidInstrument):
        master.get("equity:US:MISSING")


def test_get_existing(master: InstrumentMasterService) -> None:
    inst = master.get("equity:A_SHARE:600519.SH")
    assert inst.name == "贵州茅台"


# --- symbols / aliases / names -----------------------------------------------


def test_exact_symbol_nvda(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.US, query="NVDA")
    assert out.match_type is ResolveMatchType.EXACT_SYMBOL
    assert out.instrument is not None
    assert out.instrument.symbol == "NVDA"


def test_normalized_a_share_600519(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.A_SHARE, query="600519")
    assert out.match_type is ResolveMatchType.NORMALIZED_SYMBOL
    assert out.instrument is not None
    assert out.instrument.instrument_id == "equity:A_SHARE:600519.SH"
    assert out.normalized is not None
    assert out.normalized.canonical_candidate == "600519.SH"


def test_normalized_a_share_sh600519(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.A_SHARE, query="SH600519")
    assert out.match_type is ResolveMatchType.NORMALIZED_SYMBOL
    assert out.instrument is not None
    assert out.instrument.instrument_id == "equity:A_SHARE:600519.SH"


def test_gspc_alias(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.US, query="^GSPC")
    assert out.match_type is ResolveMatchType.ALIAS
    assert out.instrument is not None
    assert out.instrument.instrument_id == "index:US:SPX"
    assert out.alias_hit is not None
    assert out.alias_hit.alias_value == "^GSPC"


def test_nvidia_alias_case_insensitive(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.US, query="NVIDIA")
    assert out.match_type is ResolveMatchType.ALIAS
    assert out.instrument is not None
    assert out.instrument.instrument_id == "equity:US:NVDA"
    assert out.alias_hit is not None
    assert out.alias_hit.alias_value == "nvidia"


def test_maotai_alias(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.A_SHARE, query="茅台")
    assert out.match_type is ResolveMatchType.ALIAS
    assert out.instrument is not None
    assert out.instrument.instrument_id == "equity:A_SHARE:600519.SH"


def test_exact_full_instrument_name(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.A_SHARE, query="贵州茅台")
    assert out.match_type is ResolveMatchType.ALIAS
    assert out.instrument is not None
    assert out.instrument.instrument_id == "equity:A_SHARE:600519.SH"
    assert out.alias_hit is None  # name search path, not alias row


# --- seeded asset types ------------------------------------------------------


@pytest.mark.parametrize(
    ("market", "query", "expected_id", "asset_type_hint"),
    [
        (Market.A_SHARE, "510300.SH", "etf:A_SHARE:510300.SH", None),
        (Market.A_SHARE, "000300.SH", "index:A_SHARE:000300.SH", None),
        # A-share option: bare local code via alias; canonical symbol via exact step 3.
        (Market.A_SHARE, "10007601", "option:A_SHARE:10007601.SH", None),
        (Market.A_SHARE, "10007601.SH", "option:A_SHARE:10007601.SH", None),
        (Market.US, "SPY", "etf:US:SPY", None),
        (Market.US, "SPX", "index:US:SPX", None),
        (Market.US, "NVDA260717C00150000", "option:US:NVDA260717C00150000", None),
    ],
)
def test_seeded_asset_types_resolve(
    master: InstrumentMasterService,
    market: Market,
    query: str,
    expected_id: str,
    asset_type_hint: AssetType | None,
) -> None:
    out = master.resolve(
        market=market,
        query=query,
        asset_type_hint=asset_type_hint,
    )
    assert out.instrument is not None
    assert out.instrument.instrument_id == expected_id
    assert out.match_type in {
        ResolveMatchType.EXACT_SYMBOL,
        ResolveMatchType.NORMALIZED_SYMBOL,
        ResolveMatchType.ALIAS,
    }


def test_a_share_option_canonical_exact_symbol_unhinted(
    master: InstrumentMasterService,
) -> None:
    """v1.5 step 3: cleaned symbol match before normalize, no asset_type hint."""
    out = master.resolve(market=Market.A_SHARE, query="10007601.SH")
    assert out.match_type is ResolveMatchType.EXACT_SYMBOL
    assert out.instrument is not None
    assert out.instrument.instrument_id == "option:A_SHARE:10007601.SH"
    assert out.instrument.asset_type is AssetType.OPTION
    assert out.normalized is None


# --- asset_type hint ---------------------------------------------------------


def test_asset_type_hint_filters_to_equity(master: InstrumentMasterService) -> None:
    out = master.resolve(
        market=Market.US,
        query="NVDA",
        asset_type_hint=AssetType.EQUITY,
    )
    assert out.instrument is not None
    assert out.instrument.asset_type is AssetType.EQUITY


def test_asset_type_hint_excludes_wrong_type(master: InstrumentMasterService) -> None:
    out = master.resolve(
        market=Market.US,
        query="NVDA",
        asset_type_hint=AssetType.ETF,
    )
    assert out.match_type is ResolveMatchType.NOT_FOUND
    assert out.instrument is None


def test_asset_type_hint_on_option_occ(master: InstrumentMasterService) -> None:
    out = master.resolve(
        market=Market.US,
        query="NVDA260717C00150000",
        asset_type_hint=AssetType.OPTION,
    )
    assert out.instrument is not None
    assert out.instrument.asset_type is AssetType.OPTION


# --- not found / ambiguous ---------------------------------------------------


def test_not_found_outcome(master: InstrumentMasterService) -> None:
    out = master.resolve(market=Market.US, query="ZZZZNOTFOUND")
    assert out.match_type is ResolveMatchType.NOT_FOUND
    assert out.instrument is None


def test_ambiguous_never_silent_first(
    engine: Engine,
    master: InstrumentMasterService,
) -> None:
    """Two instruments sharing an alias value → AMBIGUOUS, no arbitrary pick."""
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    twin = Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "NVDAX"),
        symbol="NVDAX",
        name="NVDA Twin",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    # Seed already has alias_value "nvidia" → NVDA; second instrument with same value.
    twin_alias = _alias(
        alias_id="alias_00000000-0000-7000-8000-000000009998",
        instrument_id=twin.instrument_id,
        alias_type=AliasType.SYMBOL,
        alias_value="nvidia",
        market=Market.US,
    )
    service = InstrumentMasterService(factory)
    service.upsert(twin, (twin_alias,))

    out = service.resolve(market=Market.US, query="nvidia")
    assert out.match_type is ResolveMatchType.AMBIGUOUS
    assert out.instrument is None
    assert len(out.candidates) >= 2
    ids = [c.instrument_id for c in out.candidates]
    assert ids == sorted(ids)
    assert "equity:US:NVDA" in ids
    assert twin.instrument_id in ids


# --- atomic upsert -----------------------------------------------------------


def test_upsert_atomic_commit(engine: Engine) -> None:
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    service = InstrumentMasterService(factory)
    inst = Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "AAPL"),
        symbol="AAPL",
        name="Apple Inc.",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    alias = _alias(
        alias_id="alias_00000000-0000-7000-8000-00000000a001",
        instrument_id=inst.instrument_id,
        alias_type=AliasType.NAME_EN,
        alias_value="apple",
        market=Market.US,
    )
    service.upsert(inst, (alias,))
    assert service.get(inst.instrument_id).symbol == "AAPL"
    out = service.resolve(market=Market.US, query="apple")
    assert out.instrument is not None
    assert out.instrument.instrument_id == inst.instrument_id


def test_upsert_mismatched_alias_raises_data_contract_error_no_write(
    engine: Engine,
) -> None:
    """Prevalidation: alias not in aggregate → DataContractError, no writes."""
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    service = InstrumentMasterService(factory)
    inst = Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "MSFT"),
        symbol="MSFT",
        name="Microsoft",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    mismatched = _alias(
        alias_id="alias_00000000-0000-7000-8000-00000000c001",
        instrument_id="equity:US:GHOST",
        alias_type=AliasType.SYMBOL,
        alias_value="msft-alias",
        market=Market.US,
    )

    with pytest.raises(DataContractError, match="alias instrument_id") as exc_info:
        service.upsert(inst, (mismatched,))

    assert exc_info.value.details["instrument_id"] == inst.instrument_id
    assert exc_info.value.details["alias_instrument_id"] == "equity:US:GHOST"
    with pytest.raises(InvalidInstrument):
        service.get(inst.instrument_id)


def test_upsert_rollback_on_same_aggregate_primary_constraint(
    engine: Engine,
) -> None:
    """Two primary aliases of same type → uq_instrument_aliases_one_primary; full rollback."""
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(engine, clock)

    service = InstrumentMasterService(factory)
    inst = Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "BBB"),
        symbol="BBB",
        name="BBB Corp",
        market=Market.US,
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    primary1 = _alias(
        alias_id="alias_00000000-0000-7000-8000-00000000b001",
        instrument_id=inst.instrument_id,
        alias_type=AliasType.NAME,
        alias_value="bbb-one",
        market=Market.US,
        is_primary=True,
    )
    primary2 = _alias(
        alias_id="alias_00000000-0000-7000-8000-00000000b002",
        instrument_id=inst.instrument_id,
        alias_type=AliasType.NAME,
        alias_value="bbb-two",
        market=Market.US,
        is_primary=True,
    )

    with pytest.raises(PersistenceError) as exc_info:
        service.upsert(inst, (primary1, primary2))

    assert type(exc_info.value) is PersistenceError
    with pytest.raises(InvalidInstrument):
        service.get(inst.instrument_id)
