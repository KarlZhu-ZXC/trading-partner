"""Unit tests for InstrumentResolveService (Phase 1D D3b MCP use-case)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from application.dto.instrument import InstrumentDTO, InstrumentResolveResultDTO
from application.services.instrument_master_service import InstrumentMasterService
from application.services.instrument_resolve_service import InstrumentResolveService
from conftest import FixedClock, SequentialIdGenerator
from domain.common.enums import (
    AssetType,
    Freshness,
    Market,
    ResolveMatchType,
    SourceRole,
    VendorId,
)
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument
from infrastructure.persistence.instrument_seed_loader import InstrumentSeedLoader
from infrastructure.persistence.instrument_unit_of_work import (
    SqlAlchemyInstrumentUnitOfWork,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.seeds import default_instruments_seed_path
from infrastructure.system.redactor import DefaultSecretRedactor

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
SEED_PATH = default_instruments_seed_path()


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def resolve_service(tmp_path: Path) -> InstrumentResolveService:
    path = tmp_path / "resolve.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(eng, clock)

    master = InstrumentMasterService(factory)
    loader = InstrumentSeedLoader()
    with factory() as uow:
        assert loader.load_if_empty(uow.instruments, SEED_PATH) == 10
        uow.commit()

    return InstrumentResolveService(
        master=master,
        clock=clock,
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
    )


def test_success_envelope_local_master_primary(
    resolve_service: InstrumentResolveService,
) -> None:
    env = resolve_service.resolve(market=Market.US, query="NVDA")
    assert env.ok is True
    assert env.degraded is False
    assert env.freshness == Freshness.FRESH or env.freshness == "fresh"
    assert env.sources[0].name == VendorId.LOCAL_MASTER.value
    assert env.sources[0].role == SourceRole.PRIMARY or env.sources[0].role == "primary"
    assert env.request_id.startswith("req_")
    assert env.data is not None
    assert isinstance(env.data, InstrumentResolveResultDTO)
    assert env.data.match_type in {
        ResolveMatchType.EXACT_SYMBOL,
        "exact_symbol",
    }
    assert env.data.instrument is not None
    assert env.data.instrument.instrument_id == "equity:US:NVDA"
    assert env.data.queried == "NVDA"


def test_a_share_option_canonical_exact_symbol_unhinted(
    resolve_service: InstrumentResolveService,
) -> None:
    env = resolve_service.resolve(market=Market.A_SHARE, query="10007601.SH")
    assert env.ok is True
    assert env.degraded is False
    assert env.data is not None
    assert env.data.match_type in {
        ResolveMatchType.EXACT_SYMBOL,
        "exact_symbol",
    }
    assert env.data.instrument is not None
    assert env.data.instrument.instrument_id == "option:A_SHARE:10007601.SH"
    assert env.data.instrument.asset_type in {AssetType.OPTION, "option"}


def test_as_of_aware_enforced(resolve_service: InstrumentResolveService) -> None:
    naive = datetime(2026, 7, 17, 12, 0, 0)
    env = resolve_service.resolve(market=Market.US, query="NVDA", as_of=naive)
    assert env.ok is False
    assert env.errors[0].code in {"DATA_CONTRACT_ERROR", "INVALID_INSTRUMENT"}


def test_not_found_invalid_instrument(
    resolve_service: InstrumentResolveService,
) -> None:
    env = resolve_service.resolve(market=Market.US, query="NOPE123")
    assert env.ok is False
    assert env.degraded is True
    assert env.errors[0].code == "INVALID_INSTRUMENT"
    assert env.data is None


def test_missing_instrument_id_invalid(
    resolve_service: InstrumentResolveService,
) -> None:
    env = resolve_service.resolve(
        market=Market.US,
        query="equity:US:DOESNOTEXIST",
    )
    assert env.ok is False
    assert env.errors[0].code == "INVALID_INSTRUMENT"


def test_wrong_market_instrument_id_invalid(
    resolve_service: InstrumentResolveService,
) -> None:
    env = resolve_service.resolve(
        market=Market.A_SHARE,
        query="equity:US:NVDA",
    )
    assert env.ok is False
    assert env.errors[0].code == "INVALID_INSTRUMENT"


def test_ambiguous_includes_candidates_preview_no_instrument(
    tmp_path: Path,
) -> None:
    path = tmp_path / "amb.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    clock = FixedClock(NOW)

    def factory() -> SqlAlchemyInstrumentUnitOfWork:
        return SqlAlchemyInstrumentUnitOfWork(eng, clock)

    master = InstrumentMasterService(factory)
    # Two instruments with identical name for exact-name ambiguity
    a = Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "FOO1"),
        symbol="FOO1",
        name="Shared Name Co",
        market=Market.US,
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    b = Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, "FOO2"),
        symbol="FOO2",
        name="Shared Name Co",
        market=Market.US,
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    master.upsert(a)
    master.upsert(b)

    service = InstrumentResolveService(
        master=master,
        clock=clock,
        id_generator=SequentialIdGenerator(),
        secret_redactor=DefaultSecretRedactor(),
    )
    env = service.resolve(market=Market.US, query="Shared Name Co")
    assert env.ok is False
    assert env.errors[0].code == "INVALID_INSTRUMENT"
    details = env.errors[0].details
    assert "candidates_preview" in details
    preview = details["candidates_preview"]
    assert isinstance(preview, list)
    assert len(preview) == 2
    ids = [p["instrument_id"] for p in preview]  # type: ignore[index]
    assert ids == sorted(ids)
    assert env.data is None
    assert env.warnings == ()


def test_instrument_dto_optional_fields_and_phase1a_snapshot_shape() -> None:
    """InstrumentDTO has 1D defaults; Phase 1A snapshot still carries core fields."""
    dto = InstrumentDTO(
        instrument_id="equity:US:NVDA",
        symbol="NVDA",
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
    )
    wire = dto.model_dump(mode="json")
    for key in (
        "instrument_id",
        "symbol",
        "name",
        "market",
        "exchange",
        "currency",
        "timezone",
        "asset_type",
    ):
        assert key in wire
    assert wire["is_active"] is True
    assert wire["listing_status"] == "active"
    assert wire["underlying_instrument_id"] is None
