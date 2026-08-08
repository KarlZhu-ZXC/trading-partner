"""SQLAlchemy WatchlistItem repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from domain.common.enums import Market, WatchlistItemStatus
from domain.common.errors import WatchlistItemNotFound
from domain.research.models import WatchlistItem
from infrastructure.persistence.orm import WatchlistItemRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: WatchlistItemRow) -> WatchlistItem:
    return WatchlistItem(
        item_id=row.item_id,
        market=Market(row.market),
        symbol=row.symbol,
        instrument_id=row.instrument_id,
        display_name=row.display_name,
        thesis_hint=row.thesis_hint,
        triggers=tuple(row.triggers_json),
        subject_id=row.subject_id,
        status=WatchlistItemStatus(row.status),
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        updated_at=dt_from_db(row.updated_at, field_name="updated_at"),
        expires_at=dt_opt_from_db(row.expires_at, field_name="expires_at"),
        promoted_to_subject_id=row.promoted_to_subject_id,
        triggered_at=dt_opt_from_db(row.triggered_at, field_name="triggered_at"),
        triggered_reason=row.triggered_reason,
        selection_reason=row.selection_reason,
    )


def _to_row(item: WatchlistItem) -> WatchlistItemRow:
    return WatchlistItemRow(
        item_id=item.item_id,
        market=item.market.value,
        symbol=item.symbol,
        instrument_id=item.instrument_id,
        display_name=item.display_name,
        thesis_hint=item.thesis_hint,
        triggers_json=item.triggers,
        subject_id=item.subject_id,
        status=item.status.value,
        created_at=dt_to_db(item.created_at),
        updated_at=dt_to_db(item.updated_at),
        expires_at=dt_opt_to_db(item.expires_at),
        promoted_to_subject_id=item.promoted_to_subject_id,
        triggered_at=dt_opt_to_db(item.triggered_at),
        triggered_reason=item.triggered_reason,
        selection_reason=item.selection_reason,
    )


class SqlAlchemyWatchlistRepository:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def get(self, item_id: str) -> WatchlistItem:
        row = self._session.get(WatchlistItemRow, item_id)
        if row is None:
            raise WatchlistItemNotFound(
                f"WatchlistItem not found: {item_id}",
                details={"item_id": item_id},
            )
        return _to_domain(row)

    def list(
        self,
        *,
        market: Market | None = None,
        status: WatchlistItemStatus | None = None,
        subject_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[WatchlistItem, ...]:
        stmt = select(WatchlistItemRow)
        if market is not None:
            stmt = stmt.where(WatchlistItemRow.market == market.value)
        if status is not None:
            stmt = stmt.where(WatchlistItemRow.status == status.value)
        if subject_id is not None:
            stmt = stmt.where(WatchlistItemRow.subject_id == subject_id)
        stmt = stmt.order_by(WatchlistItemRow.updated_at.desc()).offset(offset).limit(limit)
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def add(self, item: WatchlistItem) -> None:
        self._session.add(_to_row(item))
        self._session.flush()

    def update_status(
        self,
        item_id: str,
        *,
        new_status: WatchlistItemStatus,
        triggered_at: datetime | None,
        triggered_reason: str | None,
        promoted_to_subject_id: str | None,
        expires_at: datetime | None,
        selection_reason: str | None = None,
    ) -> None:
        row = self._session.get(WatchlistItemRow, item_id, with_for_update=True)
        if row is None:
            raise WatchlistItemNotFound(
                f"WatchlistItem not found: {item_id}",
                details={"item_id": item_id},
            )
        current = _to_domain(row)
        # Clear residual fields when leaving TRIGGERED / PROMOTED_TO_SUBJECT.
        effective_triggered_at = (
            triggered_at if new_status is WatchlistItemStatus.TRIGGERED else None
        )
        effective_triggered_reason = (
            triggered_reason if new_status is WatchlistItemStatus.TRIGGERED else None
        )
        effective_promoted = (
            promoted_to_subject_id
            if new_status is WatchlistItemStatus.PROMOTED_TO_SUBJECT
            else None
        )
        now = self._clock.now()
        next_domain = WatchlistItem(
            item_id=current.item_id,
            market=current.market,
            symbol=current.symbol,
            display_name=current.display_name,
            thesis_hint=current.thesis_hint,
            triggers=current.triggers,
            subject_id=current.subject_id,
            status=new_status,
            created_at=current.created_at,
            updated_at=now,
            expires_at=expires_at,
            promoted_to_subject_id=effective_promoted,
            triggered_at=effective_triggered_at,
            triggered_reason=effective_triggered_reason,
            instrument_id=current.instrument_id,
            selection_reason=(
                selection_reason
                if new_status in {WatchlistItemStatus.SELECTED, WatchlistItemStatus.REJECTED}
                else None
            ),
        )
        row.status = next_domain.status.value
        row.triggered_at = dt_opt_to_db(next_domain.triggered_at)
        row.triggered_reason = next_domain.triggered_reason
        row.selection_reason = next_domain.selection_reason
        row.promoted_to_subject_id = next_domain.promoted_to_subject_id
        row.expires_at = dt_opt_to_db(next_domain.expires_at)
        row.updated_at = dt_to_db(next_domain.updated_at)
