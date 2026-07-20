"""SQLAlchemy WatchlistMembership repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.errors import WatchlistMembershipNotFound
from domain.watchlist.enums import WatchlistSource
from domain.watchlist.models import WatchlistMembership
from infrastructure.persistence.models import WatchlistMembershipRow
from infrastructure.persistence.repositories._mapping import (
    bool_from_db,
    bool_to_db,
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: WatchlistMembershipRow) -> WatchlistMembership:
    return WatchlistMembership(
        membership_id=row.membership_id,
        group_id=row.group_id,
        source=WatchlistSource(row.source),
        provider_code=row.provider_code,
        instrument_id=row.instrument_id,
        display_name=row.display_name,
        provider_asset_type=row.provider_asset_type,
        research_supported=bool_from_db(row.research_supported),
        active=bool_from_db(row.active),
        first_seen_at=dt_from_db(row.first_seen_at, field_name="first_seen_at"),
        last_seen_at=dt_from_db(row.last_seen_at, field_name="last_seen_at"),
        removed_at=dt_opt_from_db(row.removed_at, field_name="removed_at"),
        last_synced_at=dt_from_db(row.last_synced_at, field_name="last_synced_at"),
    )


def _to_row(membership: WatchlistMembership) -> WatchlistMembershipRow:
    return WatchlistMembershipRow(
        membership_id=membership.membership_id,
        group_id=membership.group_id,
        source=membership.source.value,
        provider_code=membership.provider_code,
        instrument_id=membership.instrument_id,
        display_name=membership.display_name,
        provider_asset_type=membership.provider_asset_type,
        research_supported=bool_to_db(membership.research_supported),
        active=bool_to_db(membership.active),
        first_seen_at=dt_to_db(membership.first_seen_at),
        last_seen_at=dt_to_db(membership.last_seen_at),
        removed_at=dt_opt_to_db(membership.removed_at),
        last_synced_at=dt_to_db(membership.last_synced_at),
    )


class SqlAlchemyWatchlistMembershipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, membership_id: str) -> WatchlistMembership:
        row = self._session.get(WatchlistMembershipRow, membership_id)
        if row is None:
            raise WatchlistMembershipNotFound(
                f"WatchlistMembership not found: {membership_id}",
                details={"membership_id": membership_id},
            )
        return _to_domain(row)

    def get_by_code(
        self,
        group_id: str,
        provider_code: str,
    ) -> WatchlistMembership | None:
        stmt = select(WatchlistMembershipRow).where(
            WatchlistMembershipRow.group_id == group_id,
            WatchlistMembershipRow.provider_code == provider_code,
        )
        row = self._session.scalar(stmt)
        if row is None:
            return None
        return _to_domain(row)

    def list(
        self,
        *,
        group_id: str,
        include_inactive: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[WatchlistMembership, ...]:
        stmt = select(WatchlistMembershipRow).where(
            WatchlistMembershipRow.group_id == group_id
        )
        if not include_inactive:
            stmt = stmt.where(WatchlistMembershipRow.active == 1)
        stmt = (
            stmt.order_by(WatchlistMembershipRow.last_synced_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def upsert(self, membership: WatchlistMembership) -> WatchlistMembership:
        row = self._session.get(
            WatchlistMembershipRow, membership.membership_id, with_for_update=True
        )
        if row is None:
            row = self._session.scalar(
                select(WatchlistMembershipRow)
                .where(WatchlistMembershipRow.group_id == membership.group_id)
                .where(
                    WatchlistMembershipRow.provider_code == membership.provider_code
                )
                .with_for_update()
            )
            if row is not None:
                # Preserve the original primary key so durable references survive
                # refresh/reactivation of the same upstream membership.
                row.source = membership.source.value
                row.instrument_id = membership.instrument_id
                row.display_name = membership.display_name
                row.provider_asset_type = membership.provider_asset_type
                row.research_supported = bool_to_db(membership.research_supported)
                row.active = bool_to_db(membership.active)
                row.first_seen_at = dt_to_db(membership.first_seen_at)
                row.last_seen_at = dt_to_db(membership.last_seen_at)
                row.removed_at = dt_opt_to_db(membership.removed_at)
                row.last_synced_at = dt_to_db(membership.last_synced_at)
                self._session.flush()
                return _to_domain(row)
            row = _to_row(membership)
            self._session.add(row)
            self._session.flush()
            return _to_domain(row)
        row.source = membership.source.value
        row.instrument_id = membership.instrument_id
        row.display_name = membership.display_name
        row.provider_asset_type = membership.provider_asset_type
        row.research_supported = bool_to_db(membership.research_supported)
        row.active = bool_to_db(membership.active)
        row.first_seen_at = dt_to_db(membership.first_seen_at)
        row.last_seen_at = dt_to_db(membership.last_seen_at)
        row.removed_at = dt_opt_to_db(membership.removed_at)
        row.last_synced_at = dt_to_db(membership.last_synced_at)
        self._session.flush()
        return _to_domain(row)

    def mark_inactive(
        self,
        membership_id: str,
        *,
        removed_at: datetime,
    ) -> None:
        row = self._session.get(WatchlistMembershipRow, membership_id, with_for_update=True)
        if row is None:
            raise WatchlistMembershipNotFound(
                f"WatchlistMembership not found: {membership_id}",
                details={"membership_id": membership_id},
            )
        row.active = 0
        row.removed_at = dt_to_db(removed_at)
        self._session.flush()

    def mark_inactive_not_seen(
        self,
        *,
        group_id: str,
        seen_provider_codes: tuple[str, ...],
        removed_at: datetime,
    ) -> int:
        stmt = select(WatchlistMembershipRow).where(
            WatchlistMembershipRow.group_id == group_id,
            WatchlistMembershipRow.active == 1,
        )
        if seen_provider_codes:
            stmt = stmt.where(
                WatchlistMembershipRow.provider_code.not_in(seen_provider_codes)
            )
        rows = self._session.scalars(stmt.with_for_update()).all()
        for row in rows:
            row.active = 0
            row.removed_at = dt_to_db(removed_at)
        if rows:
            self._session.flush()
        return len(rows)
