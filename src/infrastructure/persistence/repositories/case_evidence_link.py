"""SQLAlchemy CaseEvidenceLink repository (append-only, session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.errors import InvalidResearchLink, ResearchMemoryNotFound
from domain.research.models import CaseEvidenceLink, Evidence
from infrastructure.persistence.orm import CaseEvidenceLinkRow, ResearchEvidenceRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    case_evidence_link_exists,
    require_case_exists,
    require_evidence_exists,
)
from infrastructure.persistence.repositories.evidence import (
    _to_domain as evidence_to_domain,
)


def _to_domain(row: CaseEvidenceLinkRow) -> CaseEvidenceLink:
    return CaseEvidenceLink(
        link_id=row.link_id,
        case_id=row.case_id,
        evidence_id=row.evidence_id,
        linked_at=dt_from_db(row.linked_at, field_name="linked_at"),
        linked_by=row.linked_by,
        schema_version=row.schema_version,
    )


def _to_row(link: CaseEvidenceLink) -> CaseEvidenceLinkRow:
    return CaseEvidenceLinkRow(
        link_id=link.link_id,
        case_id=link.case_id,
        evidence_id=link.evidence_id,
        linked_at=dt_to_db(link.linked_at),
        linked_by=link.linked_by,
        schema_version=link.schema_version,
    )


class SqlAlchemyCaseEvidenceLinkRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, link: CaseEvidenceLink) -> None:
        require_case_exists(self._session, link.case_id)
        evidence = require_evidence_exists(self._session, link.evidence_id)
        evidence_observed = dt_from_db(evidence.observed_at, field_name="observed_at")
        if link.linked_at < evidence_observed:
            raise InvalidResearchLink(
                "linked_at must be >= evidence observed_at",
                details={
                    "entity_type": "case_evidence_link",
                    "case_id": link.case_id,
                    "evidence_id": link.evidence_id,
                },
            )
        if case_evidence_link_exists(
            self._session, case_id=link.case_id, evidence_id=link.evidence_id
        ):
            raise InvalidResearchLink(
                "case evidence link already exists",
                details={
                    "entity_type": "case_evidence_link",
                    "case_id": link.case_id,
                    "evidence_id": link.evidence_id,
                },
            )
        self._session.add(_to_row(link))
        self._session.flush()

    def get(self, case_id: str, evidence_id: str) -> CaseEvidenceLink:
        stmt = select(CaseEvidenceLinkRow).where(
            CaseEvidenceLinkRow.case_id == case_id,
            CaseEvidenceLinkRow.evidence_id == evidence_id,
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            raise ResearchMemoryNotFound(
                "CaseEvidenceLink not found",
                details={
                    "entity_type": "case_evidence_link",
                    "case_id": case_id,
                    "evidence_id": evidence_id,
                },
            )
        return _to_domain(row)

    def exists(self, case_id: str, evidence_id: str) -> bool:
        return case_evidence_link_exists(
            self._session, case_id=case_id, evidence_id=evidence_id
        )

    def list_evidence(
        self, case_id: str, *, as_of: datetime | None = None
    ) -> tuple[Evidence, ...]:
        stmt = (
            select(ResearchEvidenceRow, CaseEvidenceLinkRow)
            .join(
                CaseEvidenceLinkRow,
                CaseEvidenceLinkRow.evidence_id == ResearchEvidenceRow.evidence_id,
            )
            .where(CaseEvidenceLinkRow.case_id == case_id)
        )
        if as_of is not None:
            as_of_text = dt_to_db(as_of)
            stmt = stmt.where(
                CaseEvidenceLinkRow.linked_at <= as_of_text,
                ResearchEvidenceRow.observed_at <= as_of_text,
            )
        stmt = stmt.order_by(
            CaseEvidenceLinkRow.linked_at.asc(),
            CaseEvidenceLinkRow.link_id.asc(),
        )
        return tuple(
            evidence_to_domain(evidence_row)
            for evidence_row, _link_row in self._session.execute(stmt).all()
        )

    def list_cases(self, evidence_id: str) -> tuple[str, ...]:
        stmt = (
            select(CaseEvidenceLinkRow.case_id)
            .where(CaseEvidenceLinkRow.evidence_id == evidence_id)
            .order_by(CaseEvidenceLinkRow.case_id.asc())
        )
        return tuple(self._session.scalars(stmt).all())
