"""SQLAlchemy InvalidationCondition repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import InvalidationSeverity, InvalidationStatus
from domain.common.errors import PersistenceError
from domain.research.models import InvalidationCondition
from infrastructure.persistence.orm import InvalidationConditionRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: InvalidationConditionRow) -> InvalidationCondition:
    return InvalidationCondition(
        invalidation_id=row.invalidation_id,
        thesis_id=row.thesis_id,
        case_id=row.case_id,
        revision_no=row.revision_no,
        description=row.description,
        observable=row.observable,
        severity=InvalidationSeverity(row.severity),
        status=InvalidationStatus(row.status),
        proposed_at=dt_from_db(row.proposed_at, field_name="proposed_at"),
        confirmed_at=dt_from_db(row.confirmed_at, field_name="confirmed_at"),
        last_checked_at=dt_opt_from_db(row.last_checked_at, field_name="last_checked_at"),
        triggered_at=dt_opt_from_db(row.triggered_at, field_name="triggered_at"),
        triggered_reason=row.triggered_reason,
        proposed_by=row.proposed_by,
        confirmed_by=row.confirmed_by,
    )


def _to_row(invalidation: InvalidationCondition) -> InvalidationConditionRow:
    return InvalidationConditionRow(
        invalidation_id=invalidation.invalidation_id,
        thesis_id=invalidation.thesis_id,
        case_id=invalidation.case_id,
        revision_no=invalidation.revision_no,
        description=invalidation.description,
        observable=invalidation.observable,
        severity=invalidation.severity.value,
        status=invalidation.status.value,
        proposed_at=dt_to_db(invalidation.proposed_at),
        confirmed_at=dt_to_db(invalidation.confirmed_at),
        last_checked_at=dt_opt_to_db(invalidation.last_checked_at),
        triggered_at=dt_opt_to_db(invalidation.triggered_at),
        triggered_reason=invalidation.triggered_reason,
        proposed_by=invalidation.proposed_by,
        confirmed_by=invalidation.confirmed_by,
    )


class SqlAlchemyInvalidationConditionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_revision(
        self, thesis_id: str, revision_no: int
    ) -> tuple[InvalidationCondition, ...]:
        stmt = (
            select(InvalidationConditionRow)
            .where(InvalidationConditionRow.thesis_id == thesis_id)
            .where(InvalidationConditionRow.revision_no == revision_no)
            .order_by(InvalidationConditionRow.invalidation_id.asc())
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def get(self, invalidation_id: str) -> InvalidationCondition:
        row = self._session.get(InvalidationConditionRow, invalidation_id)
        if row is None:
            raise PersistenceError(
                f"InvalidationCondition not found: {invalidation_id}",
                details={"invalidation_id": invalidation_id},
            )
        return _to_domain(row)

    def add(self, invalidation: InvalidationCondition) -> None:
        self._session.add(_to_row(invalidation))
        self._session.flush()

    def transition_status(
        self,
        invalidation_id: str,
        *,
        new_status: InvalidationStatus,
        triggered_at: datetime | None,
        triggered_reason: str | None,
        last_checked_at: datetime | None,
    ) -> None:
        row = self._session.get(
            InvalidationConditionRow, invalidation_id, with_for_update=True
        )
        if row is None:
            raise PersistenceError(
                f"InvalidationCondition not found: {invalidation_id}",
                details={"invalidation_id": invalidation_id},
            )
        current = _to_domain(row)
        # Clear residual triggered_* when leaving TRIGGERED.
        effective_triggered_at = (
            triggered_at if new_status is InvalidationStatus.TRIGGERED else None
        )
        effective_triggered_reason = (
            triggered_reason if new_status is InvalidationStatus.TRIGGERED else None
        )
        next_domain = InvalidationCondition(
            invalidation_id=current.invalidation_id,
            thesis_id=current.thesis_id,
            case_id=current.case_id,
            revision_no=current.revision_no,
            description=current.description,
            observable=current.observable,
            severity=current.severity,
            status=new_status,
            proposed_at=current.proposed_at,
            confirmed_at=current.confirmed_at,
            last_checked_at=last_checked_at,
            triggered_at=effective_triggered_at,
            triggered_reason=effective_triggered_reason,
            proposed_by=current.proposed_by,
            confirmed_by=current.confirmed_by,
        )
        row.status = next_domain.status.value
        row.triggered_at = dt_opt_to_db(next_domain.triggered_at)
        row.triggered_reason = next_domain.triggered_reason
        row.last_checked_at = dt_opt_to_db(next_domain.last_checked_at)

    def list_armed(
        self, thesis_id: str, revision_no: int
    ) -> tuple[InvalidationCondition, ...]:
        stmt = (
            select(InvalidationConditionRow)
            .where(InvalidationConditionRow.thesis_id == thesis_id)
            .where(InvalidationConditionRow.revision_no == revision_no)
            .where(InvalidationConditionRow.status == InvalidationStatus.ARMED.value)
            .order_by(InvalidationConditionRow.invalidation_id.asc())
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())
