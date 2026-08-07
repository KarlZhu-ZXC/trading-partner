"""SQLAlchemy ThesisRevision repository (append-only, session-bound)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.common.enums import ConfidenceBand, ConfirmationMode, InvestmentRating
from domain.common.errors import DataContractError, ThesisRevisionNotFound
from domain.research.models import ThesisRevision
from infrastructure.persistence.orm import ThesisRevisionRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    date_from_db,
    date_to_db,
    dt_from_db,
    dt_to_db,
)


def _to_domain(row: ThesisRevisionRow) -> ThesisRevision:
    return ThesisRevision(
        revision_id=row.revision_id,
        thesis_id=row.thesis_id,
        subject_id=row.subject_id,
        revision_no=row.revision_no,
        supersedes_revision_no=row.supersedes_revision_no,
        statement=row.statement,
        rationale=row.rationale,
        confidence_band=ConfidenceBand(row.confidence_band),
        rating=InvestmentRating(row.rating),
        confirmation_mode=ConfirmationMode(row.confirmation_mode),
        proposed_by=row.proposed_by,
        confirmed_by=row.confirmed_by,
        proposed_at=dt_from_db(row.proposed_at, field_name="proposed_at"),
        confirmed_at=dt_from_db(row.confirmed_at, field_name="confirmed_at"),
        observation_window_start=date_from_db(row.observation_window_start),
        observation_window_end=date_from_db(row.observation_window_end),
        invalidation_check_note=row.invalidation_check_note,
        schema_version=row.schema_version,
    )


def _to_row(revision: ThesisRevision) -> ThesisRevisionRow:
    return ThesisRevisionRow(
        revision_id=revision.revision_id,
        thesis_id=revision.thesis_id,
        subject_id=revision.subject_id,
        revision_no=revision.revision_no,
        supersedes_revision_no=revision.supersedes_revision_no,
        statement=revision.statement,
        rationale=revision.rationale,
        confidence_band=revision.confidence_band.value,
        rating=revision.rating.value,
        confirmation_mode=revision.confirmation_mode.value,
        proposed_by=revision.proposed_by,
        confirmed_by=revision.confirmed_by,
        proposed_at=dt_to_db(revision.proposed_at),
        confirmed_at=dt_to_db(revision.confirmed_at),
        observation_window_start=date_to_db(revision.observation_window_start),
        observation_window_end=date_to_db(revision.observation_window_end),
        invalidation_check_note=revision.invalidation_check_note,
        schema_version=revision.schema_version,
    )


class SqlAlchemyThesisRevisionRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, revision_id: str) -> ThesisRevision:
        row = self._session.get(ThesisRevisionRow, revision_id)
        if row is None:
            raise ThesisRevisionNotFound(
                f"ThesisRevision not found: {revision_id}",
                details={"revision_id": revision_id},
            )
        return _to_domain(row)

    def list_by_thesis(self, thesis_id: str) -> tuple[ThesisRevision, ...]:
        stmt = (
            select(ThesisRevisionRow)
            .where(ThesisRevisionRow.thesis_id == thesis_id)
            .order_by(ThesisRevisionRow.revision_no.asc())
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def append(self, revision: ThesisRevision) -> None:
        expected = self.next_revision_no(revision.thesis_id)
        if revision.revision_no != expected:
            raise DataContractError(
                "revision_no must equal next_revision_no (no gaps)",
                details={
                    "thesis_id": revision.thesis_id,
                    "revision_no": revision.revision_no,
                    "expected": expected,
                },
            )
        # Freeze rules (also enforced by domain + SQL CHECK):
        # revision_no=1 ⇒ supersedes is None; >1 ⇒ supersedes is not None and < revision_no.
        if revision.revision_no == 1:
            if revision.supersedes_revision_no is not None:
                raise DataContractError(
                    "revision_no=1 must have supersedes_revision_no=None",
                    details={
                        "thesis_id": revision.thesis_id,
                        "supersedes_revision_no": revision.supersedes_revision_no,
                    },
                )
        else:
            if revision.supersedes_revision_no is None:
                raise DataContractError(
                    "revision_no>1 requires supersedes_revision_no",
                    details={
                        "thesis_id": revision.thesis_id,
                        "revision_no": revision.revision_no,
                    },
                )
            if revision.supersedes_revision_no >= revision.revision_no:
                raise DataContractError(
                    "supersedes_revision_no must be < revision_no",
                    details={
                        "thesis_id": revision.thesis_id,
                        "revision_no": revision.revision_no,
                        "supersedes_revision_no": revision.supersedes_revision_no,
                    },
                )
            # Superseded revision must already exist for this thesis (no dangling edge).
            exists_stmt = (
                select(ThesisRevisionRow.revision_id)
                .where(ThesisRevisionRow.thesis_id == revision.thesis_id)
                .where(ThesisRevisionRow.revision_no == revision.supersedes_revision_no)
                .limit(1)
            )
            if self._session.scalar(exists_stmt) is None:
                raise DataContractError(
                    "supersedes_revision_no must reference an existing revision",
                    details={
                        "thesis_id": revision.thesis_id,
                        "supersedes_revision_no": revision.supersedes_revision_no,
                    },
                )
        self._session.add(_to_row(revision))
        self._session.flush()

    def next_revision_no(self, thesis_id: str) -> int:
        stmt = select(func.max(ThesisRevisionRow.revision_no)).where(
            ThesisRevisionRow.thesis_id == thesis_id
        )
        current = self._session.scalar(stmt)
        if current is None:
            return 1
        return int(current) + 1
