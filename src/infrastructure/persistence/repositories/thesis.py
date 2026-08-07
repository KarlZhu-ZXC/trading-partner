"""SQLAlchemy Thesis repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from domain.common.enums import ThesisRole, ThesisStatus
from domain.common.errors import DataContractError, ThesisNotFound
from domain.research.models import Thesis
from infrastructure.persistence.orm import ThesisRevisionRow, ThesisRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: ThesisRow) -> Thesis:
    return Thesis(
        thesis_id=row.thesis_id,
        subject_id=row.subject_id,
        title=row.title,
        role=ThesisRole(row.role),
        status=ThesisStatus(row.status),
        current_revision_no=row.current_revision_no,
        latest_revision_id=row.latest_revision_id,
        parent_thesis_id=row.parent_thesis_id,
        rival_thesis_ids=tuple(row.rival_thesis_ids_json),
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        updated_at=dt_from_db(row.updated_at, field_name="updated_at"),
        archived_at=dt_opt_from_db(row.archived_at, field_name="archived_at"),
    )


def _to_row(thesis: Thesis) -> ThesisRow:
    return ThesisRow(
        thesis_id=thesis.thesis_id,
        subject_id=thesis.subject_id,
        title=thesis.title,
        role=thesis.role.value,
        status=thesis.status.value,
        current_revision_no=thesis.current_revision_no,
        latest_revision_id=thesis.latest_revision_id,
        parent_thesis_id=thesis.parent_thesis_id,
        rival_thesis_ids_json=thesis.rival_thesis_ids,
        created_at=dt_to_db(thesis.created_at),
        updated_at=dt_to_db(thesis.updated_at),
        archived_at=dt_opt_to_db(thesis.archived_at),
    )


class SqlAlchemyThesisRepository:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def get(self, thesis_id: str) -> Thesis:
        row = self._session.get(ThesisRow, thesis_id)
        if row is None:
            raise ThesisNotFound(
                f"Thesis not found: {thesis_id}",
                details={"thesis_id": thesis_id},
            )
        return _to_domain(row)

    def list_by_subject(self, subject_id: str) -> tuple[Thesis, ...]:
        stmt = (
            select(ThesisRow)
            .where(ThesisRow.subject_id == subject_id)
            .order_by(ThesisRow.created_at.asc())
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def add(self, thesis: Thesis) -> None:
        self._session.add(_to_row(thesis))
        self._session.flush()

    def update_status(
        self,
        thesis_id: str,
        *,
        new_status: ThesisStatus,
        archived_at: datetime | None,
    ) -> None:
        row = self._session.get(ThesisRow, thesis_id, with_for_update=True)
        if row is None:
            raise ThesisNotFound(
                f"Thesis not found: {thesis_id}",
                details={"thesis_id": thesis_id},
            )
        current = _to_domain(row)
        # Non-ARCHIVED must clear residual archived_at before domain validation.
        effective_archived_at = archived_at if new_status is ThesisStatus.ARCHIVED else None
        now = self._clock.now()
        next_domain = Thesis(
            thesis_id=current.thesis_id,
            subject_id=current.subject_id,
            title=current.title,
            role=current.role,
            status=new_status,
            current_revision_no=current.current_revision_no,
            latest_revision_id=current.latest_revision_id,
            parent_thesis_id=current.parent_thesis_id,
            rival_thesis_ids=current.rival_thesis_ids,
            created_at=current.created_at,
            updated_at=now,
            archived_at=effective_archived_at,
        )
        row.status = next_domain.status.value
        row.archived_at = dt_opt_to_db(next_domain.archived_at)
        row.updated_at = dt_to_db(next_domain.updated_at)

    def advance_current_revision(
        self,
        thesis_id: str,
        *,
        new_revision_no: int,
        new_latest_revision_id: str,
    ) -> None:
        row = self._session.get(ThesisRow, thesis_id, with_for_update=True)
        if row is None:
            raise ThesisNotFound(
                f"Thesis not found: {thesis_id}",
                details={"thesis_id": thesis_id},
            )
        expected = row.current_revision_no + 1
        if new_revision_no != expected:
            raise DataContractError(
                "new_revision_no must equal current_revision_no + 1",
                details={
                    "thesis_id": thesis_id,
                    "current_revision_no": row.current_revision_no,
                    "new_revision_no": new_revision_no,
                    "expected": expected,
                },
            )
        rev_row = self._session.get(ThesisRevisionRow, new_latest_revision_id)
        if rev_row is None:
            raise DataContractError(
                "advance_current_revision requires an existing revision_id",
                details={
                    "thesis_id": thesis_id,
                    "revision_id": new_latest_revision_id,
                },
            )
        if rev_row.thesis_id != thesis_id:
            raise DataContractError(
                "revision_id does not belong to thesis",
                details={
                    "thesis_id": thesis_id,
                    "revision_id": new_latest_revision_id,
                    "revision_thesis_id": rev_row.thesis_id,
                },
            )
        if rev_row.revision_no != new_revision_no:
            raise DataContractError(
                "revision_id revision_no does not match new_revision_no",
                details={
                    "thesis_id": thesis_id,
                    "revision_id": new_latest_revision_id,
                    "revision_no": rev_row.revision_no,
                    "new_revision_no": new_revision_no,
                },
            )
        row.current_revision_no = new_revision_no
        row.latest_revision_id = new_latest_revision_id
        row.updated_at = dt_to_db(self._clock.now())
