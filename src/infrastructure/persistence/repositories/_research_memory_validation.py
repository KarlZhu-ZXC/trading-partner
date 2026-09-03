"""Shared cross-record validation for Phase 1C research-memory repositories.

All checks run inside the caller's Session before flush. Failures raise
``InvalidResearchLink`` (or ``DataContractError`` for local storage-shape
rules). Repositories never recompute content hashes, never compare
idempotency payloads, and never mutate Phase 1B Research Subject JSON caches.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.errors import DataContractError, InvalidResearchLink
from domain.common.time import require_aware_datetime
from infrastructure.persistence.orm import (
    ExternalNoteIdentityRow,
    ExternalNoteRevisionRow,
    InstrumentRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    ResearchSubjectRow,
    SubjectEvidenceLinkRow,
    ThesisRevisionRow,
    ThesisRow,
)
from infrastructure.persistence.repositories._mapping import dt_to_db

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_IDEM_KEY_MAX = 128


def require_idempotency_storage(
    *,
    idempotency_key: str,
    idempotency_payload_sha256: str,
) -> None:
    """Accept only pre-normalized key + 64-char lowercase hex payload hash."""
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise DataContractError(
            "idempotency_key must be a non-empty string",
            details={"field": "idempotency_key"},
        )
    if idempotency_key != idempotency_key.strip().lower():
        raise DataContractError(
            "idempotency_key must already be strip+lowercase",
            details={"field": "idempotency_key"},
        )
    if len(idempotency_key) > _IDEM_KEY_MAX:
        raise DataContractError(
            "idempotency_key length must be <= 128",
            details={"field": "idempotency_key", "length": len(idempotency_key)},
        )
    if not isinstance(idempotency_payload_sha256, str) or not _SHA256_HEX_RE.fullmatch(
        idempotency_payload_sha256
    ):
        raise DataContractError(
            "idempotency_payload_sha256 must be a 64-character lowercase hex digest",
            details={"field": "idempotency_payload_sha256"},
        )


def require_subject_exists(session: Session, subject_id: str) -> None:
    if session.get(ResearchSubjectRow, subject_id) is None:
        raise InvalidResearchLink(
            "referenced research subject does not exist",
            details={"entity_type": "subject", "subject_id": subject_id},
        )


def require_instruments_exist(session: Session, instrument_ids: tuple[str, ...]) -> None:
    for instrument_id in instrument_ids:
        if session.get(InstrumentRow, instrument_id) is None:
            raise InvalidResearchLink(
                "referenced instrument does not exist",
                details={
                    "entity_type": "instrument",
                    "instrument_id": instrument_id,
                },
            )


def require_evidence_exists(session: Session, evidence_id: str) -> ResearchEvidenceRow:
    row = session.get(ResearchEvidenceRow, evidence_id)
    if row is None:
        raise InvalidResearchLink(
            "referenced evidence does not exist",
            details={"entity_type": "evidence", "evidence_id": evidence_id},
        )
    return row


def require_evidence_supersedes(
    session: Session,
    *,
    supersedes_evidence_id: str | None,
    new_observed_at: datetime,
) -> None:
    if supersedes_evidence_id is None:
        return
    old = require_evidence_exists(session, supersedes_evidence_id)
    require_aware_datetime(new_observed_at, field_name="observed_at")
    old_observed = require_aware_datetime(
        datetime.fromisoformat(old.observed_at), field_name="observed_at"
    )
    if old_observed > new_observed_at:
        raise InvalidResearchLink(
            "superseded evidence observed_at must be <= new observed_at",
            details={
                "entity_type": "evidence",
                "supersedes_evidence_id": supersedes_evidence_id,
            },
        )


def subject_evidence_link_exists(session: Session, *, subject_id: str, evidence_id: str) -> bool:
    stmt = (
        select(SubjectEvidenceLinkRow.link_id)
        .where(
            SubjectEvidenceLinkRow.subject_id == subject_id,
            SubjectEvidenceLinkRow.evidence_id == evidence_id,
        )
        .limit(1)
    )
    return session.scalars(stmt).first() is not None


def require_subject_evidence_link(
    session: Session, *, subject_id: str, evidence_id: str
) -> SubjectEvidenceLinkRow:
    stmt = select(SubjectEvidenceLinkRow).where(
        SubjectEvidenceLinkRow.subject_id == subject_id,
        SubjectEvidenceLinkRow.evidence_id == evidence_id,
    )
    row = session.scalars(stmt).first()
    if row is None:
        raise InvalidResearchLink(
            "subject evidence link does not exist",
            details={
                "entity_type": "subject_evidence_link",
                "subject_id": subject_id,
                "evidence_id": evidence_id,
            },
        )
    return row


def require_evidence_ids_linked_and_visible(
    session: Session,
    *,
    subject_id: str,
    evidence_ids: tuple[str, ...],
    observed_at_not_after: datetime,
    linked_at_not_after: datetime,
) -> None:
    """Evidence must exist, be subject-linked, and pass hindsight time bounds.

    Event/Decision pass the record visible time for both bounds. Report is
    stricter: Evidence.observed_at <= report.as_of and Link.linked_at <=
    report.created_at.
    """
    require_aware_datetime(observed_at_not_after, field_name="observed_at_not_after")
    require_aware_datetime(linked_at_not_after, field_name="linked_at_not_after")
    observed_text = dt_to_db(observed_at_not_after)
    linked_text = dt_to_db(linked_at_not_after)
    for evidence_id in evidence_ids:
        evidence = require_evidence_exists(session, evidence_id)
        if evidence.observed_at > observed_text:
            raise InvalidResearchLink(
                "evidence observed_at is not yet visible at record time",
                details={
                    "entity_type": "evidence",
                    "subject_id": subject_id,
                    "evidence_id": evidence_id,
                },
            )
        stmt = (
            select(SubjectEvidenceLinkRow.link_id)
            .where(
                SubjectEvidenceLinkRow.subject_id == subject_id,
                SubjectEvidenceLinkRow.evidence_id == evidence_id,
                SubjectEvidenceLinkRow.linked_at <= linked_text,
            )
            .limit(1)
        )
        if session.scalars(stmt).first() is None:
            raise InvalidResearchLink(
                "evidence must be linked to the research subject and visible at record time",
                details={
                    "entity_type": "evidence",
                    "subject_id": subject_id,
                    "evidence_id": evidence_id,
                },
            )


def require_thesis_optional(
    session: Session,
    *,
    subject_id: str,
    thesis_id: str | None,
    thesis_revision_id: str | None,
    assessed_at: datetime,
) -> None:
    require_aware_datetime(assessed_at, field_name="assessed_at")
    assessed_text = dt_to_db(assessed_at)
    if thesis_id is not None:
        thesis = session.get(ThesisRow, thesis_id)
        if thesis is None:
            raise InvalidResearchLink(
                "referenced thesis does not exist",
                details={"entity_type": "thesis", "thesis_id": thesis_id},
            )
        if thesis.subject_id != subject_id:
            raise InvalidResearchLink(
                "thesis does not belong to the same research subject",
                details={
                    "entity_type": "thesis",
                    "thesis_id": thesis_id,
                    "subject_id": subject_id,
                },
            )
    if thesis_revision_id is not None:
        rev = session.get(ThesisRevisionRow, thesis_revision_id)
        if rev is None:
            raise InvalidResearchLink(
                "referenced thesis revision does not exist",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": thesis_revision_id,
                },
            )
        if rev.subject_id != subject_id:
            raise InvalidResearchLink(
                "thesis revision does not belong to the same research subject",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": thesis_revision_id,
                    "subject_id": subject_id,
                },
            )
        if thesis_id is not None and rev.thesis_id != thesis_id:
            raise InvalidResearchLink(
                "thesis revision does not belong to the referenced thesis",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_id": thesis_id,
                    "thesis_revision_id": thesis_revision_id,
                },
            )
        if rev.confirmed_at > assessed_text:
            raise InvalidResearchLink(
                "thesis revision confirmed_at must be <= assessed_at",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": thesis_revision_id,
                },
            )


def require_thesis_revision_ids_visible(
    session: Session,
    *,
    subject_id: str,
    thesis_revision_ids: tuple[str, ...],
    visible_at: datetime,
) -> None:
    """Revisions must exist, same subject, and confirmed_at <= visible_at."""
    require_aware_datetime(visible_at, field_name="visible_at")
    visible_text = dt_to_db(visible_at)
    for revision_id in thesis_revision_ids:
        rev = session.get(ThesisRevisionRow, revision_id)
        if rev is None:
            raise InvalidResearchLink(
                "referenced thesis revision does not exist",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                },
            )
        if rev.subject_id != subject_id:
            raise InvalidResearchLink(
                "thesis revision does not belong to the same research subject",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "subject_id": subject_id,
                },
            )
        if rev.confirmed_at > visible_text:
            raise InvalidResearchLink(
                "thesis revision is not yet visible at record time",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                },
            )


def require_external_note_revision_visible(
    session: Session,
    *,
    subject_id: str,
    external_note_revision_id: str | None,
    visible_at: datetime,
) -> None:
    """Validate an optional exact observation revision reference.

    External observations do not carry a Research Subject foreign key: their
    durable identity is source/external-id plus an optional instrument.  A
    Decision may therefore reference a revision only when the note identity
    and the Research Subject expose the same primary instrument.  This is the
    existing observation-to-Subject ownership boundary and deliberately fails
    closed for unscoped notes.  The revision must also have been visible by
    the Decision's recorded time, preserving the point-in-time contract used
    by the other Decision source links.
    """

    if external_note_revision_id is None:
        return
    require_aware_datetime(visible_at, field_name="visible_at")
    revision = session.get(ExternalNoteRevisionRow, external_note_revision_id)
    if revision is None:
        raise InvalidResearchLink(
            "referenced external note revision does not exist",
            details={
                "entity_type": "external_note_revision",
                "external_note_revision_id": external_note_revision_id,
                "subject_id": subject_id,
            },
        )
    identity = session.get(ExternalNoteIdentityRow, revision.note_id)
    subject = session.get(ResearchSubjectRow, subject_id)
    if identity is None or subject is None:
        # The normal Subject check runs before this helper, but keep the
        # boundary explicit if this validator is called independently.
        raise InvalidResearchLink(
            "external note revision ownership cannot be established",
            details={
                "entity_type": "external_note_revision",
                "external_note_revision_id": external_note_revision_id,
                "subject_id": subject_id,
            },
        )
    if (
        identity.primary_instrument_id is None
        or subject.primary_instrument_id is None
        or identity.primary_instrument_id != subject.primary_instrument_id
    ):
        raise InvalidResearchLink(
            "external note revision does not belong to the same research subject",
            details={
                "entity_type": "external_note_revision",
                "external_note_revision_id": external_note_revision_id,
                "subject_id": subject_id,
            },
        )
    visible_text = dt_to_db(visible_at)
    if revision.observed_at > visible_text:
        raise InvalidResearchLink(
            "external note revision is not yet visible at record time",
            details={
                "entity_type": "external_note_revision",
                "external_note_revision_id": external_note_revision_id,
                "subject_id": subject_id,
            },
        )


def require_report_ids_visible(
    session: Session,
    *,
    subject_id: str,
    report_ids: tuple[str, ...],
    visible_at: datetime,
) -> None:
    """Reports must exist, same subject, and created_at <= visible_at."""
    require_aware_datetime(visible_at, field_name="visible_at")
    visible_text = dt_to_db(visible_at)
    for report_id in report_ids:
        report = session.get(ResearchReportRow, report_id)
        if report is None:
            raise InvalidResearchLink(
                "referenced report does not exist",
                details={"entity_type": "report", "report_id": report_id},
            )
        if report.subject_id != subject_id:
            raise InvalidResearchLink(
                "report does not belong to the same research subject",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "subject_id": subject_id,
                },
            )
        if report.created_at > visible_text:
            raise InvalidResearchLink(
                "report is not yet visible at record time",
                details={"entity_type": "report", "report_id": report_id},
            )


def require_same_subject_supersedes(
    *,
    new_subject_id: str | None,
    old_subject_id: str | None,
    entity_type: str,
    supersedes_id: str,
) -> None:
    """Journal allows both sides None; otherwise subject_ids must be equal."""
    if new_subject_id != old_subject_id:
        raise InvalidResearchLink(
            "supersedes target must belong to the same research subject",
            details={
                "entity_type": entity_type,
                "supersedes_id": supersedes_id,
            },
        )


def require_visible_not_after(
    *,
    old_visible_at: datetime,
    new_visible_at: datetime,
    entity_type: str,
    supersedes_id: str,
) -> None:
    if old_visible_at > new_visible_at:
        raise InvalidResearchLink(
            "superseded record visible time must be <= new record visible time",
            details={
                "entity_type": entity_type,
                "supersedes_id": supersedes_id,
            },
        )


def require_journal_page_bounds(*, limit: int, offset: int) -> None:
    if type(limit) is not int or limit < 1 or limit > 100:
        raise DataContractError(
            "journal list limit must be an int in [1, 100]",
            details={"field": "limit", "limit": limit},
        )
    if type(offset) is not int or offset < 0:
        raise DataContractError(
            "journal list offset must be an int >= 0",
            details={"field": "offset", "offset": offset},
        )
