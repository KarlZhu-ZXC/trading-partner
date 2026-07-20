"""SQLAlchemy Evidence repository (append-only, session-bound)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import (
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceType,
    ReliabilityLevel,
)
from domain.common.errors import ResearchMemoryNotFound
from domain.research.models import Evidence
from infrastructure.persistence.models import ResearchEvidenceRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    decimal_from_db,
    decimal_to_db,
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    require_evidence_supersedes,
    require_instruments_exist,
)


def _to_domain(row: ResearchEvidenceRow) -> Evidence:
    return Evidence(
        evidence_id=row.evidence_id,
        evidence_type=EvidenceType(row.evidence_type),
        origin=EvidenceOrigin(row.origin),
        title=row.title,
        summary=row.summary,
        content_text=row.content_text,
        structured_data_json=row.structured_data_json,
        source_name=row.source_name,
        source_vendor=row.source_vendor,
        source_record_id=row.source_record_id,
        source_url=row.source_url,
        published_at=dt_opt_from_db(row.published_at, field_name="published_at"),
        observed_at=dt_from_db(row.observed_at, field_name="observed_at"),
        effective_from=dt_opt_from_db(row.effective_from, field_name="effective_from"),
        effective_to=dt_opt_from_db(row.effective_to, field_name="effective_to"),
        instrument_ids=tuple(row.instrument_ids_json),
        topic_tags=tuple(row.topic_tags_json),
        quality=EvidenceQuality(row.quality),
        reliability=ReliabilityLevel(row.reliability),
        confidence=decimal_from_db(row.confidence_decimal),
        content_sha256=row.content_sha256,
        supersedes_evidence_id=row.supersedes_evidence_id,
        recorded_by=row.recorded_by,
        schema_version=row.schema_version,
    )


def _to_row(evidence: Evidence) -> ResearchEvidenceRow:
    return ResearchEvidenceRow(
        evidence_id=evidence.evidence_id,
        evidence_type=evidence.evidence_type.value,
        origin=evidence.origin.value,
        title=evidence.title,
        summary=evidence.summary,
        content_text=evidence.content_text,
        structured_data_json=evidence.structured_data_json,
        source_name=evidence.source_name,
        source_vendor=evidence.source_vendor,
        source_record_id=evidence.source_record_id,
        source_url=evidence.source_url,
        published_at=dt_opt_to_db(evidence.published_at),
        observed_at=dt_to_db(evidence.observed_at),
        effective_from=dt_opt_to_db(evidence.effective_from),
        effective_to=dt_opt_to_db(evidence.effective_to),
        instrument_ids_json=evidence.instrument_ids,
        topic_tags_json=evidence.topic_tags,
        quality=evidence.quality.value,
        reliability=evidence.reliability.value,
        confidence_decimal=decimal_to_db(evidence.confidence),
        content_sha256=evidence.content_sha256,
        supersedes_evidence_id=evidence.supersedes_evidence_id,
        recorded_by=evidence.recorded_by,
        schema_version=evidence.schema_version,
    )


class SqlAlchemyEvidenceRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, evidence: Evidence) -> None:
        require_instruments_exist(self._session, evidence.instrument_ids)
        require_evidence_supersedes(
            self._session,
            supersedes_evidence_id=evidence.supersedes_evidence_id,
            new_observed_at=evidence.observed_at,
        )
        self._session.add(_to_row(evidence))
        self._session.flush()

    def get(self, evidence_id: str) -> Evidence:
        row = self._session.get(ResearchEvidenceRow, evidence_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "Evidence not found",
                details={"entity_type": "evidence", "evidence_id": evidence_id},
            )
        return _to_domain(row)

    def get_by_content_sha256(self, content_sha256: str) -> Evidence | None:
        stmt = select(ResearchEvidenceRow).where(
            ResearchEvidenceRow.content_sha256 == content_sha256
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            return None
        return _to_domain(row)
