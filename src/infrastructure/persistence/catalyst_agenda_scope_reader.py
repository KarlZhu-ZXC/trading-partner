"""Current durable Portfolio, Watchlist, and Research Subject scope projection."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.ports.catalyst_agenda_scope_reader import (
    AgendaScopeEntry,
    AgendaScopeSnapshot,
    CatalystAgendaScopeReader,
)
from domain.catalyst_agenda.enums import AgendaScopeReason
from domain.common.enums import AssetType, Market, ResearchSubjectStatus, ResearchSubjectType
from domain.instruments.models import Instrument
from infrastructure.persistence.orm import (
    AccountPositionRow,
    AccountSnapshotRow,
    InstrumentRow,
    ResearchSubjectRow,
    WatchlistGroupRow,
    WatchlistMembershipRow,
)
from infrastructure.persistence.repositories._mapping import (
    bool_from_db,
    decimal_from_db,
)


class SqlAlchemyCatalystAgendaScopeReader(CatalystAgendaScopeReader):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read_current(self) -> AgendaScopeSnapshot:
        reasons: dict[tuple[str | None, str | None], set[AgendaScopeReason]] = defaultdict(set)
        with Session(self._engine) as session:
            snapshot_rows = session.scalars(
                select(AccountSnapshotRow).order_by(
                    AccountSnapshotRow.account_ref,
                    AccountSnapshotRow.account_as_of.desc(),
                    AccountSnapshotRow.snapshot_id.desc(),
                )
            ).all()
            latest_snapshot_ids: dict[str, str] = {}
            for row in snapshot_rows:
                latest_snapshot_ids.setdefault(row.account_ref, row.snapshot_id)
            if latest_snapshot_ids:
                for portfolio_instrument_id in session.scalars(
                    select(AccountPositionRow.instrument_id)
                    .where(AccountPositionRow.snapshot_id.in_(tuple(latest_snapshot_ids.values())))
                    .distinct()
                ):
                    reasons[(portfolio_instrument_id, None)].add(
                        AgendaScopeReason.PORTFOLIO
                    )

            for watchlist_instrument_id in session.scalars(
                select(WatchlistMembershipRow.instrument_id)
                .join(
                    WatchlistGroupRow,
                    WatchlistGroupRow.group_id == WatchlistMembershipRow.group_id,
                )
                .where(
                    WatchlistMembershipRow.active == 1,
                    WatchlistMembershipRow.instrument_id.is_not(None),
                    WatchlistGroupRow.active == 1,
                )
                .distinct()
            ):
                if watchlist_instrument_id is not None:
                    reasons[(watchlist_instrument_id, None)].add(
                        AgendaScopeReason.WATCHLIST
                    )

            subject_rows = session.execute(
                select(
                    ResearchSubjectRow.subject_id,
                    ResearchSubjectRow.primary_instrument_id,
                    ResearchSubjectRow.subject_type,
                )
                .where(ResearchSubjectRow.status != ResearchSubjectStatus.ARCHIVED.value)
                .order_by(ResearchSubjectRow.subject_id)
            )
            subject_types: dict[str, ResearchSubjectType] = {}
            for subject_id, instrument_id, subject_type in subject_rows:
                reasons[(instrument_id, subject_id)].add(AgendaScopeReason.SUBJECT)
                subject_types[subject_id] = ResearchSubjectType(subject_type)

        entries = tuple(
            AgendaScopeEntry(
                instrument_id=instrument_id,
                subject_id=subject_id,
                reasons=tuple(sorted(values, key=lambda value: value.value)),
                subject_type=subject_types.get(subject_id) if subject_id else None,
            )
            for (instrument_id, subject_id), values in sorted(
                reasons.items(), key=lambda item: ((item[0][0] or ""), (item[0][1] or ""))
            )
        )
        return AgendaScopeSnapshot(entries=entries)

    def subject_exists(self, subject_id: str) -> bool:
        with Session(self._engine) as session:
            return session.get(ResearchSubjectRow, subject_id) is not None

    def instrument_exists(self, instrument_id: str) -> bool:
        with Session(self._engine) as session:
            return session.get(InstrumentRow, instrument_id) is not None

    def get_instrument(self, instrument_id: str) -> Instrument | None:
        with Session(self._engine) as session:
            row = session.get(InstrumentRow, instrument_id)
            if row is None:
                return None
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
