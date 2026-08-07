"""SQLAlchemy SubjectEvidenceLink repository (append-only, session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.errors import InvalidResearchLink, ResearchMemoryNotFound
from domain.research.models import Evidence, SubjectEvidenceLink
from infrastructure.persistence.orm import ResearchEvidenceRow, SubjectEvidenceLinkRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    require_evidence_exists,
    require_subject_exists,
    subject_evidence_link_exists,
)
from infrastructure.persistence.repositories.evidence import (
    _to_domain as evidence_to_domain,
)


def _to_domain(row: SubjectEvidenceLinkRow) -> SubjectEvidenceLink:
    return SubjectEvidenceLink(
        link_id=row.link_id,
        subject_id=row.subject_id,
        evidence_id=row.evidence_id,
        linked_at=dt_from_db(row.linked_at, field_name="linked_at"),
        linked_by=row.linked_by,
        schema_version=row.schema_version,
    )


def _to_row(link: SubjectEvidenceLink) -> SubjectEvidenceLinkRow:
    return SubjectEvidenceLinkRow(
        link_id=link.link_id,
        subject_id=link.subject_id,
        evidence_id=link.evidence_id,
        linked_at=dt_to_db(link.linked_at),
        linked_by=link.linked_by,
        schema_version=link.schema_version,
    )


class SqlAlchemySubjectEvidenceLinkRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, link: SubjectEvidenceLink) -> None:
        require_subject_exists(self._session, link.subject_id)
        evidence = require_evidence_exists(self._session, link.evidence_id)
        evidence_observed = dt_from_db(evidence.observed_at, field_name="observed_at")
        if link.linked_at < evidence_observed:
            raise InvalidResearchLink(
                "linked_at must be >= evidence observed_at",
                details={
                    "entity_type": "subject_evidence_link",
                    "subject_id": link.subject_id,
                    "evidence_id": link.evidence_id,
                },
            )
        if subject_evidence_link_exists(
            self._session, subject_id=link.subject_id, evidence_id=link.evidence_id
        ):
            raise InvalidResearchLink(
                "subject evidence link already exists",
                details={
                    "entity_type": "subject_evidence_link",
                    "subject_id": link.subject_id,
                    "evidence_id": link.evidence_id,
                },
            )
        self._session.add(_to_row(link))
        self._session.flush()

    def get(self, subject_id: str, evidence_id: str) -> SubjectEvidenceLink:
        stmt = select(SubjectEvidenceLinkRow).where(
            SubjectEvidenceLinkRow.subject_id == subject_id,
            SubjectEvidenceLinkRow.evidence_id == evidence_id,
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            raise ResearchMemoryNotFound(
                "SubjectEvidenceLink not found",
                details={
                    "entity_type": "subject_evidence_link",
                    "subject_id": subject_id,
                    "evidence_id": evidence_id,
                },
            )
        return _to_domain(row)

    def exists(self, subject_id: str, evidence_id: str) -> bool:
        return subject_evidence_link_exists(
            self._session, subject_id=subject_id, evidence_id=evidence_id
        )

    def list_evidence(
        self, subject_id: str, *, as_of: datetime | None = None
    ) -> tuple[Evidence, ...]:
        stmt = (
            select(ResearchEvidenceRow, SubjectEvidenceLinkRow)
            .join(
                SubjectEvidenceLinkRow,
                SubjectEvidenceLinkRow.evidence_id == ResearchEvidenceRow.evidence_id,
            )
            .where(SubjectEvidenceLinkRow.subject_id == subject_id)
        )
        if as_of is not None:
            as_of_text = dt_to_db(as_of)
            stmt = stmt.where(
                SubjectEvidenceLinkRow.linked_at <= as_of_text,
                ResearchEvidenceRow.observed_at <= as_of_text,
            )
        stmt = stmt.order_by(
            SubjectEvidenceLinkRow.linked_at.asc(),
            SubjectEvidenceLinkRow.link_id.asc(),
        )
        return tuple(
            evidence_to_domain(evidence_row)
            for evidence_row, _link_row in self._session.execute(stmt).all()
        )

    def list_subjects(self, evidence_id: str) -> tuple[str, ...]:
        stmt = (
            select(SubjectEvidenceLinkRow.subject_id)
            .where(SubjectEvidenceLinkRow.evidence_id == evidence_id)
            .order_by(SubjectEvidenceLinkRow.subject_id.asc())
        )
        return tuple(self._session.scalars(stmt).all())
