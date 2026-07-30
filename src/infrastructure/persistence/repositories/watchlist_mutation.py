"""SQLAlchemy WatchlistMutation repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.errors import DataContractError, WatchlistMutationNotFound
from domain.watchlist.enums import (
    WatchlistMutationAction,
    WatchlistMutationStatus,
    WatchlistSource,
)
from domain.watchlist.models import WatchlistMutation
from infrastructure.persistence.orm import WatchlistMutationRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: WatchlistMutationRow) -> WatchlistMutation:
    return WatchlistMutation(
        mutation_id=row.mutation_id,
        idempotency_key=row.idempotency_key,
        action=WatchlistMutationAction(row.action),
        source=WatchlistSource(row.source),
        group_name=row.group_name,
        provider_code=row.provider_code,
        requested_by=row.requested_by,
        status=WatchlistMutationStatus(row.status),
        requested_at=dt_from_db(row.requested_at, field_name="requested_at"),
        completed_at=dt_opt_from_db(row.completed_at, field_name="completed_at"),
        error_code=row.error_code,
    )


def _to_row(mutation: WatchlistMutation) -> WatchlistMutationRow:
    return WatchlistMutationRow(
        mutation_id=mutation.mutation_id,
        idempotency_key=mutation.idempotency_key,
        action=mutation.action.value,
        source=mutation.source.value,
        group_name=mutation.group_name,
        provider_code=mutation.provider_code,
        requested_by=mutation.requested_by,
        status=mutation.status.value,
        requested_at=dt_to_db(mutation.requested_at),
        completed_at=dt_opt_to_db(mutation.completed_at),
        error_code=mutation.error_code,
    )


def _validate_status_transition(
    *,
    status: WatchlistMutationStatus,
    completed_at: datetime | None,
    error_code: str | None,
) -> None:
    if status is WatchlistMutationStatus.PENDING:
        if completed_at is not None or error_code is not None:
            raise DataContractError(
                "PENDING mutation requires completed_at and error_code be null",
                details={
                    "status": status.value,
                    "completed_at": completed_at.isoformat() if completed_at else None,
                    "error_code": error_code,
                },
            )
        return
    if status is WatchlistMutationStatus.SUCCEEDED:
        if completed_at is None:
            raise DataContractError(
                "completed mutation requires completed_at",
                details={"status": status.value},
            )
        if error_code is not None:
            raise DataContractError(
                "completed mutation must not carry error_code",
                details={
                    "status": status.value,
                    "error_code": error_code,
                },
            )
        return
    if status is WatchlistMutationStatus.PARTIAL:
        if completed_at is None or error_code is None:
            raise DataContractError(
                "PARTIAL mutation requires completed_at and error_code",
                details={"status": status.value},
            )
        return
    if status is WatchlistMutationStatus.FAILED and (
        completed_at is None or error_code is None
    ):
        raise DataContractError(
            "FAILED mutation requires completed_at and error_code",
            details={"status": status.value},
        )


class SqlAlchemyWatchlistMutationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, mutation_id: str) -> WatchlistMutation:
        row = self._session.get(WatchlistMutationRow, mutation_id)
        if row is None:
            raise WatchlistMutationNotFound(
                f"WatchlistMutation not found: {mutation_id}",
                details={"mutation_id": mutation_id},
            )
        return _to_domain(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> WatchlistMutation | None:
        row = self._session.scalar(
            select(WatchlistMutationRow).where(
                WatchlistMutationRow.idempotency_key == idempotency_key
            )
        )
        if row is None:
            return None
        return _to_domain(row)

    def add(self, mutation: WatchlistMutation) -> None:
        self._session.add(_to_row(mutation))
        self._session.flush()

    def update_status(
        self,
        mutation_id: str,
        *,
        status: WatchlistMutationStatus,
        completed_at: datetime,
        error_code: str | None,
    ) -> None:
        row = self._session.get(WatchlistMutationRow, mutation_id, with_for_update=True)
        if row is None:
            raise WatchlistMutationNotFound(
                f"WatchlistMutation not found: {mutation_id}",
                details={"mutation_id": mutation_id},
            )
        _validate_status_transition(
            status=status,
            completed_at=completed_at,
            error_code=error_code,
        )
        row.status = status.value
        row.completed_at = dt_to_db(completed_at)
        row.error_code = error_code
        self._session.flush()
