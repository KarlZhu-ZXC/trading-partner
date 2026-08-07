"""SQLAlchemy EvidenceAssessment repository (append-only, session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import EvidenceStance
from domain.common.errors import DataContractError, InvalidResearchLink
from domain.research.models import EvidenceAssessment
from infrastructure.persistence.orm import EvidenceAssessmentRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    decimal_from_db,
    decimal_to_db,
    dt_from_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    require_evidence_exists,
    require_subject_evidence_link,
    require_thesis_optional,
)


def _to_domain(row: EvidenceAssessmentRow) -> EvidenceAssessment:
    materiality = decimal_from_db(row.materiality_decimal)
    if materiality is None:
        raise DataContractError(
            "materiality_decimal must not be null",
            details={"field": "materiality_decimal"},
        )
    return EvidenceAssessment(
        assessment_id=row.assessment_id,
        evidence_id=row.evidence_id,
        subject_id=row.subject_id,
        thesis_id=row.thesis_id,
        thesis_revision_id=row.thesis_revision_id,
        stance=EvidenceStance(row.stance),
        materiality=materiality,
        rationale=row.rationale,
        assessed_at=dt_from_db(row.assessed_at, field_name="assessed_at"),
        assessed_by=row.assessed_by,
        confirmed_by=row.confirmed_by,
        schema_version=row.schema_version,
    )


def _to_row(assessment: EvidenceAssessment) -> EvidenceAssessmentRow:
    return EvidenceAssessmentRow(
        assessment_id=assessment.assessment_id,
        evidence_id=assessment.evidence_id,
        subject_id=assessment.subject_id,
        thesis_id=assessment.thesis_id,
        thesis_revision_id=assessment.thesis_revision_id,
        stance=assessment.stance.value,
        materiality_decimal=decimal_to_db(assessment.materiality),
        rationale=assessment.rationale,
        assessed_at=dt_to_db(assessment.assessed_at),
        assessed_by=assessment.assessed_by,
        confirmed_by=assessment.confirmed_by,
        schema_version=assessment.schema_version,
    )


class SqlAlchemyEvidenceAssessmentRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, assessment: EvidenceAssessment) -> None:
        evidence = require_evidence_exists(self._session, assessment.evidence_id)
        link = require_subject_evidence_link(
            self._session,
            subject_id=assessment.subject_id,
            evidence_id=assessment.evidence_id,
        )
        evidence_observed = dt_from_db(evidence.observed_at, field_name="observed_at")
        link_linked_at = dt_from_db(link.linked_at, field_name="linked_at")
        if assessment.assessed_at < evidence_observed:
            raise InvalidResearchLink(
                "assessed_at must be >= evidence observed_at",
                details={
                    "entity_type": "evidence_assessment",
                    "assessment_id": assessment.assessment_id,
                },
            )
        if assessment.assessed_at < link_linked_at:
            raise InvalidResearchLink(
                "assessed_at must be >= subject evidence link linked_at",
                details={
                    "entity_type": "evidence_assessment",
                    "assessment_id": assessment.assessment_id,
                },
            )
        require_thesis_optional(
            self._session,
            subject_id=assessment.subject_id,
            thesis_id=assessment.thesis_id,
            thesis_revision_id=assessment.thesis_revision_id,
            assessed_at=assessment.assessed_at,
        )
        self._session.add(_to_row(assessment))
        self._session.flush()

    def list_for_evidence(
        self, evidence_id: str, *, as_of: datetime | None = None
    ) -> tuple[EvidenceAssessment, ...]:
        stmt = select(EvidenceAssessmentRow).where(EvidenceAssessmentRow.evidence_id == evidence_id)
        if as_of is not None:
            stmt = stmt.where(EvidenceAssessmentRow.assessed_at <= dt_to_db(as_of))
        stmt = stmt.order_by(
            EvidenceAssessmentRow.assessed_at.asc(),
            EvidenceAssessmentRow.assessment_id.asc(),
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def list_for_thesis(
        self,
        thesis_id: str,
        *,
        stance: EvidenceStance | None = None,
        as_of: datetime | None = None,
    ) -> tuple[EvidenceAssessment, ...]:
        stmt = select(EvidenceAssessmentRow).where(EvidenceAssessmentRow.thesis_id == thesis_id)
        if stance is not None:
            stmt = stmt.where(EvidenceAssessmentRow.stance == stance.value)
        if as_of is not None:
            stmt = stmt.where(EvidenceAssessmentRow.assessed_at <= dt_to_db(as_of))
        stmt = stmt.order_by(
            EvidenceAssessmentRow.assessed_at.asc(),
            EvidenceAssessmentRow.assessment_id.asc(),
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())
