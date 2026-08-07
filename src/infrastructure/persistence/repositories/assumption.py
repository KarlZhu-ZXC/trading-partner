"""SQLAlchemy Assumption repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import AssumptionStatus
from domain.common.errors import PersistenceError
from domain.research.models import Assumption
from infrastructure.persistence.orm import AssumptionRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: AssumptionRow) -> Assumption:
    return Assumption(
        assumption_id=row.assumption_id,
        thesis_id=row.thesis_id,
        subject_id=row.subject_id,
        revision_no=row.revision_no,
        statement=row.statement,
        basis=row.basis,
        falsifiability=row.falsifiability,
        status=AssumptionStatus(row.status),
        proposed_at=dt_from_db(row.proposed_at, field_name="proposed_at"),
        confirmed_at=dt_from_db(row.confirmed_at, field_name="confirmed_at"),
        proposed_by=row.proposed_by,
        confirmed_by=row.confirmed_by,
        retired_at=dt_opt_from_db(row.retired_at, field_name="retired_at"),
        retired_reason=row.retired_reason,
    )


def _to_row(assumption: Assumption) -> AssumptionRow:
    return AssumptionRow(
        assumption_id=assumption.assumption_id,
        thesis_id=assumption.thesis_id,
        subject_id=assumption.subject_id,
        revision_no=assumption.revision_no,
        statement=assumption.statement,
        basis=assumption.basis,
        falsifiability=assumption.falsifiability,
        status=assumption.status.value,
        proposed_at=dt_to_db(assumption.proposed_at),
        confirmed_at=dt_to_db(assumption.confirmed_at),
        proposed_by=assumption.proposed_by,
        confirmed_by=assumption.confirmed_by,
        retired_at=dt_opt_to_db(assumption.retired_at),
        retired_reason=assumption.retired_reason,
    )


class SqlAlchemyAssumptionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_revision(self, thesis_id: str, revision_no: int) -> tuple[Assumption, ...]:
        stmt = (
            select(AssumptionRow)
            .where(AssumptionRow.thesis_id == thesis_id)
            .where(AssumptionRow.revision_no == revision_no)
            .order_by(AssumptionRow.assumption_id.asc())
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def get(self, assumption_id: str) -> Assumption:
        row = self._session.get(AssumptionRow, assumption_id)
        if row is None:
            raise PersistenceError(
                f"Assumption not found: {assumption_id}",
                details={"assumption_id": assumption_id},
            )
        return _to_domain(row)

    def add(self, assumption: Assumption) -> None:
        self._session.add(_to_row(assumption))
        self._session.flush()

    def retire(
        self,
        assumption_id: str,
        *,
        retired_at: datetime,
        retired_reason: str,
    ) -> None:
        row = self._session.get(AssumptionRow, assumption_id, with_for_update=True)
        if row is None:
            raise PersistenceError(
                f"Assumption not found: {assumption_id}",
                details={"assumption_id": assumption_id},
            )
        current = _to_domain(row)
        next_domain = Assumption(
            assumption_id=current.assumption_id,
            thesis_id=current.thesis_id,
            subject_id=current.subject_id,
            revision_no=current.revision_no,
            statement=current.statement,
            basis=current.basis,
            falsifiability=current.falsifiability,
            status=AssumptionStatus.RETIRED,
            proposed_at=current.proposed_at,
            confirmed_at=current.confirmed_at,
            proposed_by=current.proposed_by,
            confirmed_by=current.confirmed_by,
            retired_at=retired_at,
            retired_reason=retired_reason,
        )
        row.status = next_domain.status.value
        assert next_domain.retired_at is not None
        row.retired_at = dt_to_db(next_domain.retired_at)
        row.retired_reason = next_domain.retired_reason

    def list_active(self, thesis_id: str, revision_no: int) -> tuple[Assumption, ...]:
        stmt = (
            select(AssumptionRow)
            .where(AssumptionRow.thesis_id == thesis_id)
            .where(AssumptionRow.revision_no == revision_no)
            .where(AssumptionRow.status != AssumptionStatus.RETIRED.value)
            .order_by(AssumptionRow.assumption_id.asc())
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())
