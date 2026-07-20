"""SQLAlchemy InstrumentRepository (session-bound; caller owns the transaction)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from domain.common.enums import AliasType, AssetType, Market
from domain.instruments.models import Instrument, InstrumentAlias
from infrastructure.persistence.models import InstrumentAliasRow, InstrumentRow
from infrastructure.persistence.repositories._mapping import (
    bool_from_db,
    bool_to_db,
    decimal_from_db,
    decimal_to_db,
    dt_from_db,
    dt_to_db,
)


def _instrument_to_domain(row: InstrumentRow) -> Instrument:
    return Instrument(
        instrument_id=row.instrument_id,
        symbol=row.symbol,
        name=row.name,
        market=Market(row.market),
        exchange=row.exchange,
        currency=row.currency,
        timezone=row.timezone,
        asset_type=AssetType(row.asset_type),
        is_active=bool_from_db(row.is_active),
        listing_status=row.listing_status,
        country=row.country,
        mic=row.mic,
        underlying_instrument_id=row.underlying_instrument_id,
        multiplier=decimal_from_db(row.multiplier),
        tick_size=decimal_from_db(row.tick_size),
        lot_size=decimal_from_db(row.lot_size),
        metadata_version=row.metadata_version,
    )


def _alias_to_domain(row: InstrumentAliasRow) -> InstrumentAlias:
    return InstrumentAlias(
        alias_id=row.alias_id,
        instrument_id=row.instrument_id,
        alias_type=AliasType(row.alias_type),
        alias_value=row.alias_value,
        alias_value_raw=row.alias_value_raw,
        market=Market(row.market),
        source=row.source,
        is_primary=bool_from_db(row.is_primary),
        created_at=dt_from_db(row.created_at, field_name="created_at"),
    )


def _alias_payload_matches(row: InstrumentAliasRow, alias: InstrumentAlias) -> bool:
    """Compare mutable alias fields for idempotent upsert.

    Once alias_id exists, caller-supplied created_at is ignored — the stored
    original is always preserved on update.
    """
    return (
        row.instrument_id == alias.instrument_id
        and row.alias_type == alias.alias_type.value
        and row.alias_value == alias.alias_value
        and row.alias_value_raw == alias.alias_value_raw
        and row.market == alias.market.value
        and row.source == alias.source
        and bool_from_db(row.is_primary) is alias.is_primary
    )


_LIKE_ESCAPE = "\\"


def _escape_like(value: str, *, escape: str = _LIKE_ESCAPE) -> str:
    """Escape SQL LIKE wildcards and the escape character for literal match."""
    return (
        value.replace(escape, escape + escape)
        .replace("%", escape + "%")
        .replace("_", escape + "_")
    )


class SqlAlchemyInstrumentRepository:
    """Session-bound instrument master repository.

    Never commits or rolls back — the caller controls the transaction.
    """

    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def count(self) -> int:
        result = self._session.scalar(select(func.count()).select_from(InstrumentRow))
        return int(result or 0)

    def get_by_id(self, instrument_id: str) -> Instrument | None:
        row = self._session.get(InstrumentRow, instrument_id)
        if row is None:
            return None
        return _instrument_to_domain(row)

    def find_by_symbol(
        self,
        market: Market,
        symbol: str,
        *,
        asset_type: AssetType | None = None,
    ) -> tuple[Instrument, ...]:
        stmt = (
            select(InstrumentRow)
            .where(InstrumentRow.market == market.value)
            .where(InstrumentRow.symbol == symbol)
        )
        if asset_type is not None:
            stmt = stmt.where(InstrumentRow.asset_type == asset_type.value)
        stmt = stmt.order_by(InstrumentRow.instrument_id)
        return tuple(
            _instrument_to_domain(row) for row in self._session.scalars(stmt).all()
        )

    def find_by_alias(
        self,
        market: Market,
        alias_value: str,
        *,
        alias_type: AliasType | None = None,
    ) -> tuple[Instrument, ...]:
        stmt = (
            select(InstrumentRow)
            .join(
                InstrumentAliasRow,
                InstrumentAliasRow.instrument_id == InstrumentRow.instrument_id,
            )
            .where(InstrumentAliasRow.market == market.value)
            .where(InstrumentAliasRow.alias_value == alias_value)
        )
        if alias_type is not None:
            stmt = stmt.where(InstrumentAliasRow.alias_type == alias_type.value)
        stmt = stmt.order_by(InstrumentRow.instrument_id).distinct()
        return tuple(
            _instrument_to_domain(row) for row in self._session.scalars(stmt).all()
        )

    def list_aliases(self, instrument_id: str) -> tuple[InstrumentAlias, ...]:
        stmt = (
            select(InstrumentAliasRow)
            .where(InstrumentAliasRow.instrument_id == instrument_id)
            .order_by(InstrumentAliasRow.alias_id)
        )
        return tuple(
            _alias_to_domain(row) for row in self._session.scalars(stmt).all()
        )

    def upsert_instrument(self, instrument: Instrument) -> None:
        now = self._clock.now()
        now_db = dt_to_db(now)
        row = self._session.get(InstrumentRow, instrument.instrument_id)
        if row is None:
            self._session.add(
                InstrumentRow(
                    instrument_id=instrument.instrument_id,
                    symbol=instrument.symbol,
                    name=instrument.name,
                    market=instrument.market.value,
                    exchange=instrument.exchange,
                    currency=instrument.currency,
                    timezone=instrument.timezone,
                    asset_type=instrument.asset_type.value,
                    is_active=bool_to_db(instrument.is_active),
                    listing_status=instrument.listing_status,
                    country=instrument.country,
                    mic=instrument.mic,
                    underlying_instrument_id=instrument.underlying_instrument_id,
                    multiplier=decimal_to_db(instrument.multiplier),
                    tick_size=decimal_to_db(instrument.tick_size),
                    lot_size=decimal_to_db(instrument.lot_size),
                    metadata_version=instrument.metadata_version,
                    created_at=now_db,
                    updated_at=now_db,
                )
            )
        else:
            # Preserve created_at; force updated_at from Clock.
            row.symbol = instrument.symbol
            row.name = instrument.name
            row.market = instrument.market.value
            row.exchange = instrument.exchange
            row.currency = instrument.currency
            row.timezone = instrument.timezone
            row.asset_type = instrument.asset_type.value
            row.is_active = bool_to_db(instrument.is_active)
            row.listing_status = instrument.listing_status
            row.country = instrument.country
            row.mic = instrument.mic
            row.underlying_instrument_id = instrument.underlying_instrument_id
            row.multiplier = decimal_to_db(instrument.multiplier)
            row.tick_size = decimal_to_db(instrument.tick_size)
            row.lot_size = decimal_to_db(instrument.lot_size)
            row.metadata_version = instrument.metadata_version
            row.updated_at = now_db
        self._session.flush()

    def upsert_alias(self, alias: InstrumentAlias) -> None:
        row = self._session.get(InstrumentAliasRow, alias.alias_id)
        if row is not None and _alias_payload_matches(row, alias):
            # Idempotent no-op for same alias_id + payload (created_at ignored).
            return
        if row is None:
            self._session.add(
                InstrumentAliasRow(
                    alias_id=alias.alias_id,
                    instrument_id=alias.instrument_id,
                    alias_type=alias.alias_type.value,
                    alias_value=alias.alias_value,
                    alias_value_raw=alias.alias_value_raw,
                    market=alias.market.value,
                    source=alias.source,
                    is_primary=bool_to_db(alias.is_primary),
                    created_at=dt_to_db(alias.created_at),
                )
            )
        else:
            # Preserve original created_at; update other changed fields.
            row.instrument_id = alias.instrument_id
            row.alias_type = alias.alias_type.value
            row.alias_value = alias.alias_value
            row.alias_value_raw = alias.alias_value_raw
            row.market = alias.market.value
            row.source = alias.source
            row.is_primary = bool_to_db(alias.is_primary)
        self._session.flush()

    def search_name(
        self,
        market: Market,
        name_query: str,
        *,
        limit: int = 10,
    ) -> tuple[Instrument, ...]:
        needle = name_query.strip()
        if not needle or limit <= 0:
            return ()
        # Treat %, _, and the escape char as literals; wrap with wildcards.
        # SQLite LIKE is case-insensitive for ASCII by default.
        pattern = f"%{_escape_like(needle)}%"
        stmt = (
            select(InstrumentRow)
            .where(InstrumentRow.market == market.value)
            .where(InstrumentRow.name.like(pattern, escape=_LIKE_ESCAPE))
            .order_by(InstrumentRow.name, InstrumentRow.instrument_id)
            .limit(limit)
        )
        return tuple(
            _instrument_to_domain(row) for row in self._session.scalars(stmt).all()
        )
