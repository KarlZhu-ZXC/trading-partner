"""Phase 1D InstrumentRepository integration tests (session-bound, no commit)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from conftest import FixedClock
from domain.common.enums import AliasType, AssetType, Market
from domain.common.values import build_instrument_id
from domain.instruments.models import Instrument, InstrumentAlias
from infrastructure.persistence.instrument_repository import SqlAlchemyInstrumentRepository
from infrastructure.persistence.metadata import Base

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _enable_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "instruments.db"
    eng = create_engine(f"sqlite:///{path}")
    _enable_fk(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine):  # type: ignore[no-untyped-def]
    return sessionmaker(bind=engine, expire_on_commit=False)


def _equity_a_share(*, name: str = "贵州茅台") -> Instrument:
    symbol = "600519.SH"
    return Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.A_SHARE, symbol),
        symbol=symbol,
        name=name,
        market=Market.A_SHARE,
        exchange="SSE",
        currency="CNY",
        timezone="Asia/Shanghai",
        asset_type=AssetType.EQUITY,
        is_active=True,
        listing_status="active",
        country="CN",
        mic="XSHG",
        multiplier=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("100"),
        metadata_version=1,
    )


def _equity_us() -> Instrument:
    symbol = "NVDA"
    return Instrument(
        instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, symbol),
        symbol=symbol,
        name="NVIDIA Corporation",
        market=Market.US,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        asset_type=AssetType.EQUITY,
        is_active=True,
        listing_status="active",
        country="US",
        mic="XNAS",
        metadata_version=1,
    )


def _alias(
    *,
    alias_id: str,
    instrument: Instrument,
    alias_type: AliasType,
    alias_value: str,
    alias_value_raw: str | None = None,
    is_primary: bool = False,
    source: str = "local_seed",
    created_at: datetime = NOW,
) -> InstrumentAlias:
    return InstrumentAlias(
        alias_id=alias_id,
        instrument_id=instrument.instrument_id,
        alias_type=alias_type,
        alias_value=alias_value,
        alias_value_raw=alias_value_raw or alias_value,
        market=instrument.market,
        source=source,
        is_primary=is_primary,
        created_at=created_at,
    )


def test_count_empty_and_after_upsert(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        assert repo.count() == 0
        repo.upsert_instrument(_equity_a_share())
        assert repo.count() == 1
        repo.upsert_instrument(_equity_us())
        assert repo.count() == 2
        session.commit()

    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        assert repo.count() == 2


def test_upsert_get_find_symbol_and_decimal_round_trip(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    inst = _equity_a_share()
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(inst)
        session.commit()

    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        got = repo.get_by_id(inst.instrument_id)
        assert got is not None
        assert got.instrument_id == "equity:A_SHARE:600519.SH"
        assert got.tick_size == Decimal("0.01")
        assert got.lot_size == Decimal("100")
        assert got.multiplier is None
        by_symbol = repo.find_by_symbol(Market.A_SHARE, "600519.SH")
        assert len(by_symbol) == 1
        assert by_symbol[0].instrument_id == inst.instrument_id
        filtered = repo.find_by_symbol(Market.A_SHARE, "600519.SH", asset_type=AssetType.EQUITY)
        assert len(filtered) == 1
        empty = repo.find_by_symbol(Market.A_SHARE, "600519.SH", asset_type=AssetType.ETF)
        assert empty == ()


def test_upsert_instrument_preserves_created_at_forces_updated_at(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    inst = _equity_a_share(name="贵州茅台")
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(inst)
        session.commit()
        created_at = session.execute(
            text("SELECT created_at, updated_at FROM instruments WHERE instrument_id = :id"),
            {"id": inst.instrument_id},
        ).one()
        assert created_at[0] == created_at[1] == NOW.isoformat()

    later = NOW + timedelta(hours=2)
    clock.set(later)
    updated = _equity_a_share(name="Kweichow Moutai")
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(updated)
        session.commit()
        row = session.execute(
            text("SELECT name, created_at, updated_at FROM instruments WHERE instrument_id = :id"),
            {"id": inst.instrument_id},
        ).one()
        assert row[0] == "Kweichow Moutai"
        assert row[1] == NOW.isoformat()  # preserved
        assert row[2] == later.isoformat()  # forced from clock


def test_session_bound_repo_does_not_commit(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    inst = _equity_us()
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(inst)
        # No commit — row must not be durable after session close without commit.
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        assert repo.get_by_id(inst.instrument_id) is None


def test_alias_upsert_idempotent_and_lookup(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    inst = _equity_a_share()
    alias = _alias(
        alias_id="alias_00000000-0000-7000-8000-000000000001",
        instrument=inst,
        alias_type=AliasType.NAME,
        alias_value="茅台",
        alias_value_raw="茅台",
        is_primary=True,
    )
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(inst)
        repo.upsert_alias(alias)
        repo.upsert_alias(alias)  # idempotent same payload
        session.commit()

    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        aliases = repo.list_aliases(inst.instrument_id)
        assert len(aliases) == 1
        assert aliases[0].alias_value == "茅台"
        hits = repo.find_by_alias(Market.A_SHARE, "茅台")
        assert len(hits) == 1
        assert hits[0].instrument_id == inst.instrument_id
        typed = repo.find_by_alias(Market.A_SHARE, "茅台", alias_type=AliasType.NAME)
        assert len(typed) == 1
        miss = repo.find_by_alias(Market.A_SHARE, "茅台", alias_type=AliasType.SYMBOL)
        assert miss == ()


def test_upsert_alias_preserves_created_at(
    session_factory: sessionmaker[Session],
) -> None:
    """Existing alias_id keeps original created_at; other fields may update.

    Idempotent compare ignores caller-supplied created_at once the row exists.
    """
    clock = FixedClock(NOW)
    inst = _equity_a_share()
    alias_id = "alias_00000000-0000-7000-8000-000000000021"
    original = _alias(
        alias_id=alias_id,
        instrument=inst,
        alias_type=AliasType.NAME,
        alias_value="茅台",
        alias_value_raw="茅台",
        is_primary=True,
        source="local_seed",
        created_at=NOW,
    )
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(inst)
        repo.upsert_alias(original)
        session.commit()
        stored = session.execute(
            text(
                "SELECT created_at, alias_value, source FROM instrument_aliases "
                "WHERE alias_id = :id"
            ),
            {"id": alias_id},
        ).one()
        assert stored[0] == NOW.isoformat()

    later = NOW + timedelta(days=1)
    # Same payload except created_at — must be idempotent no-op (created_at ignored).
    same_payload_new_created = _alias(
        alias_id=alias_id,
        instrument=inst,
        alias_type=AliasType.NAME,
        alias_value="茅台",
        alias_value_raw="茅台",
        is_primary=True,
        source="local_seed",
        created_at=later,
    )
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_alias(same_payload_new_created)
        session.commit()
        after_noop = session.execute(
            text(
                "SELECT created_at, alias_value, source FROM instrument_aliases "
                "WHERE alias_id = :id"
            ),
            {"id": alias_id},
        ).one()
        assert after_noop[0] == NOW.isoformat()
        assert after_noop[1] == "茅台"
        assert after_noop[2] == "local_seed"

    # Changed fields update; created_at still preserved despite caller later value.
    changed = _alias(
        alias_id=alias_id,
        instrument=inst,
        alias_type=AliasType.NAME,
        alias_value="贵州茅台",
        alias_value_raw="贵州茅台",
        is_primary=True,
        source="user",
        created_at=later,
    )
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_alias(changed)
        session.commit()
        after_update = session.execute(
            text(
                "SELECT created_at, alias_value, source FROM instrument_aliases "
                "WHERE alias_id = :id"
            ),
            {"id": alias_id},
        ).one()
        assert after_update[0] == NOW.isoformat()  # preserved
        assert after_update[1] == "贵州茅台"
        assert after_update[2] == "user"

    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        aliases = repo.list_aliases(inst.instrument_id)
        assert len(aliases) == 1
        assert aliases[0].created_at == NOW
        assert aliases[0].alias_value == "贵州茅台"
        assert aliases[0].source == "user"


def test_alias_uniqueness_enforced(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    inst = _equity_a_share()
    a1 = _alias(
        alias_id="alias_00000000-0000-7000-8000-000000000001",
        instrument=inst,
        alias_type=AliasType.LOCAL_CODE,
        alias_value="600519",
        is_primary=True,
    )
    # Same (instrument_id, alias_type, alias_value), different alias_id
    a2 = _alias(
        alias_id="alias_00000000-0000-7000-8000-000000000002",
        instrument=inst,
        alias_type=AliasType.LOCAL_CODE,
        alias_value="600519",
        is_primary=False,
    )
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(inst)
        repo.upsert_alias(a1)
        with pytest.raises(IntegrityError):
            repo.upsert_alias(a2)
        session.rollback()


def test_one_primary_alias_per_type(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    inst = _equity_a_share()
    primary1 = _alias(
        alias_id="alias_00000000-0000-7000-8000-000000000011",
        instrument=inst,
        alias_type=AliasType.NAME,
        alias_value="茅台",
        is_primary=True,
    )
    primary2 = _alias(
        alias_id="alias_00000000-0000-7000-8000-000000000012",
        instrument=inst,
        alias_type=AliasType.NAME,
        alias_value="贵州茅台",
        is_primary=True,
    )
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(inst)
        repo.upsert_alias(primary1)
        with pytest.raises(IntegrityError):
            repo.upsert_alias(primary2)
        session.rollback()


def test_search_name(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FixedClock(NOW)
    moutai = _equity_a_share(name="贵州茅台")
    nvda = _equity_us()
    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        repo.upsert_instrument(moutai)
        repo.upsert_instrument(nvda)
        session.commit()

    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        hits = repo.search_name(Market.A_SHARE, "茅台")
        assert len(hits) == 1
        assert hits[0].instrument_id == moutai.instrument_id
        us_hits = repo.search_name(Market.US, "NVIDIA")
        assert len(us_hits) == 1
        assert repo.search_name(Market.A_SHARE, "NVIDIA") == ()


def test_search_name_treats_like_metacharacters_literally(
    session_factory: sessionmaker[Session],
) -> None:
    """%, _, and the escape char in name_query must not act as LIKE wildcards."""
    clock = FixedClock(NOW)

    def _named(symbol: str, name: str) -> Instrument:
        return Instrument(
            instrument_id=build_instrument_id(AssetType.EQUITY, Market.US, symbol),
            symbol=symbol,
            name=name,
            market=Market.US,
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            asset_type=AssetType.EQUITY,
            is_active=True,
            listing_status="active",
            country="US",
            mic="XNAS",
            metadata_version=1,
        )

    # Substring names that would match if %/_ were wildcards.
    literal_pct = _named("LITPCT", "foo%bar Corp")
    literal_us = _named("LITUS", "foo_bar Inc")
    literal_esc = _named("LITESC", r"foo\bar Ltd")
    # Would be matched by unescaped "foo%" (any suffix) or "foo_" (any one char).
    decoy_any = _named("DECOY", "foozbar Holdings")
    plain = _named("PLAIN", "plain name")

    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)
        for inst in (literal_pct, literal_us, literal_esc, decoy_any, plain):
            repo.upsert_instrument(inst)
        session.commit()

    with session_factory() as session:
        repo = SqlAlchemyInstrumentRepository(session, clock)

        pct_hits = repo.search_name(Market.US, "foo%bar")
        assert [h.instrument_id for h in pct_hits] == [literal_pct.instrument_id]

        us_hits = repo.search_name(Market.US, "foo_bar")
        assert [h.instrument_id for h in us_hits] == [literal_us.instrument_id]

        esc_hits = repo.search_name(Market.US, r"foo\bar")
        assert [h.instrument_id for h in esc_hits] == [literal_esc.instrument_id]

        # Leading wildcard-looking query must still be substring-literal, not "any".
        only_pct = repo.search_name(Market.US, "%bar")
        assert [h.instrument_id for h in only_pct] == [literal_pct.instrument_id]

        only_us = repo.search_name(Market.US, "_bar")
        assert [h.instrument_id for h in only_us] == [literal_us.instrument_id]

        # Unrelated plain substring still works.
        plain_hits = repo.search_name(Market.US, "plain")
        assert [h.instrument_id for h in plain_hits] == [plain.instrument_id]
