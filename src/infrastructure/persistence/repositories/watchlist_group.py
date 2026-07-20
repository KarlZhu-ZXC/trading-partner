"""SQLAlchemy WatchlistGroup repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.errors import WatchlistGroupNotFound
from domain.watchlist.enums import WatchlistGroupType, WatchlistSource
from domain.watchlist.models import WatchlistGroup
from infrastructure.persistence.models import WatchlistGroupRow
from infrastructure.persistence.repositories._mapping import (
    bool_from_db,
    bool_to_db,
    dt_from_db,
    dt_to_db,
)


def _to_domain(row: WatchlistGroupRow) -> WatchlistGroup:
    return WatchlistGroup(
        group_id=row.group_id,
        source=WatchlistSource(row.source),
        source_group_key=row.source_group_key,
        name=row.name,
        group_type=WatchlistGroupType(row.group_type),
        writable=bool_from_db(row.writable),
        active=bool_from_db(row.active),
        first_seen_at=dt_from_db(row.first_seen_at, field_name="first_seen_at"),
        last_seen_at=dt_from_db(row.last_seen_at, field_name="last_seen_at"),
        removed_at=(
            None
            if row.removed_at is None
            else dt_from_db(row.removed_at, field_name="removed_at")
        ),
        last_synced_at=dt_from_db(row.last_synced_at, field_name="last_synced_at"),
    )


def _to_row(group: WatchlistGroup) -> WatchlistGroupRow:
    return WatchlistGroupRow(
        group_id=group.group_id,
        source=group.source.value,
        source_group_key=group.source_group_key,
        name=group.name,
        group_type=group.group_type.value,
        writable=bool_to_db(group.writable),
        active=bool_to_db(group.active),
        first_seen_at=dt_to_db(group.first_seen_at),
        last_seen_at=dt_to_db(group.last_seen_at),
        removed_at=None if group.removed_at is None else dt_to_db(group.removed_at),
        last_synced_at=dt_to_db(group.last_synced_at),
    )


class SqlAlchemyWatchlistGroupRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, group_id: str) -> WatchlistGroup:
        row = self._session.get(WatchlistGroupRow, group_id)
        if row is None:
            raise WatchlistGroupNotFound(
                f"WatchlistGroup not found: {group_id}",
                details={"group_id": group_id},
            )
        return _to_domain(row)

    def get_by_source_key(
        self,
        source: WatchlistSource,
        source_group_key: str,
    ) -> WatchlistGroup | None:
        stmt = select(WatchlistGroupRow).where(
            WatchlistGroupRow.source == source.value,
            WatchlistGroupRow.source_group_key == source_group_key,
        )
        row = self._session.scalar(stmt)
        if row is None:
            return None
        return _to_domain(row)

    def list(
        self,
        *,
        source: WatchlistSource | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WatchlistGroup, ...]:
        stmt = select(WatchlistGroupRow)
        if source is not None:
            stmt = stmt.where(WatchlistGroupRow.source == source.value)
        if not include_inactive:
            stmt = stmt.where(WatchlistGroupRow.active == 1)
        stmt = (
            stmt.order_by(WatchlistGroupRow.last_synced_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def upsert(self, group: WatchlistGroup) -> WatchlistGroup:
        row = self._session.get(WatchlistGroupRow, group.group_id, with_for_update=True)
        if row is None:
            row = self._session.scalar(
                select(WatchlistGroupRow).where(
                    WatchlistGroupRow.source == group.source.value,
                    WatchlistGroupRow.source_group_key == group.source_group_key,
                ).with_for_update()
            )
            if row is not None:
                # Preserve existing identity for this source+key pairing.
                row.source = group.source.value
                row.source_group_key = group.source_group_key
                row.name = group.name
                row.group_type = group.group_type.value
                row.writable = bool_to_db(group.writable)
                row.active = bool_to_db(group.active)
                row.first_seen_at = dt_to_db(group.first_seen_at)
                row.last_seen_at = dt_to_db(group.last_seen_at)
                row.removed_at = (
                    None if group.removed_at is None else dt_to_db(group.removed_at)
                )
                row.last_synced_at = dt_to_db(group.last_synced_at)
                self._session.flush()
                return _to_domain(row)
            row = _to_row(group)
            self._session.add(row)
            self._session.flush()
            return _to_domain(row)
        row.source = group.source.value
        row.source_group_key = group.source_group_key
        row.name = group.name
        row.group_type = group.group_type.value
        row.writable = bool_to_db(group.writable)
        row.active = bool_to_db(group.active)
        row.first_seen_at = dt_to_db(group.first_seen_at)
        row.last_seen_at = dt_to_db(group.last_seen_at)
        row.removed_at = None if group.removed_at is None else dt_to_db(group.removed_at)
        row.last_synced_at = dt_to_db(group.last_synced_at)
        self._session.flush()
        return _to_domain(row)

    def mark_inactive(
        self,
        group_id: str,
        *,
        removed_at: datetime,
    ) -> None:
        row = self._session.get(WatchlistGroupRow, group_id, with_for_update=True)
        if row is None:
            raise WatchlistGroupNotFound(
                f"WatchlistGroup not found: {group_id}",
                details={"group_id": group_id},
            )
        row.active = 0
        row.removed_at = dt_to_db(removed_at)
        self._session.flush()
