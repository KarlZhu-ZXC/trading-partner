"""Append-only enforcement for immutable research rows via SQLAlchemy events.

Phase 1B ``ThesisRevisionRow`` continues to raise ``AppendOnlyViolation``.
Phase 1C research-memory rows raise ``ImmutableResearchRecord`` so the wire
code does not change for Phase 1B callers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Mapper

from domain.common.errors import AppendOnlyViolation, ImmutableResearchRecord
from infrastructure.persistence.orm import (
    DecisionRecordRow,
    EvidenceAssessmentRow,
    JournalEntryRow,
    ResearchEventRow,
    ResearchEvidenceRow,
    ResearchReportRow,
    SubjectEvidenceLinkRow,
    ThesisRevisionRow,
)

_LISTENERS_REGISTERED = False

_PHASE1C_IMMUTABLE_ROWS: tuple[type[Any], ...] = (
    ResearchEvidenceRow,
    SubjectEvidenceLinkRow,
    EvidenceAssessmentRow,
    ResearchReportRow,
    ResearchEventRow,
    DecisionRecordRow,
    JournalEntryRow,
)

_PHASE1C_ID_ATTR: dict[type[Any], str] = {
    ResearchEvidenceRow: "evidence_id",
    SubjectEvidenceLinkRow: "link_id",
    EvidenceAssessmentRow: "assessment_id",
    ResearchReportRow: "report_id",
    ResearchEventRow: "event_id",
    DecisionRecordRow: "decision_id",
    JournalEntryRow: "journal_id",
}

_PHASE1C_ENTITY_TYPE: dict[type[Any], str] = {
    ResearchEvidenceRow: "evidence",
    SubjectEvidenceLinkRow: "subject_evidence_link",
    EvidenceAssessmentRow: "evidence_assessment",
    ResearchReportRow: "report",
    ResearchEventRow: "event",
    DecisionRecordRow: "decision",
    JournalEntryRow: "journal",
}


def _deny_thesis_revision_update(
    mapper: Mapper[Any],
    connection: object,
    target: ThesisRevisionRow,
) -> None:
    raise AppendOnlyViolation(
        "thesis_revisions is append-only; UPDATE is forbidden",
        details={"revision_id": target.revision_id},
    )


def _deny_thesis_revision_delete(
    mapper: Mapper[Any],
    connection: object,
    target: ThesisRevisionRow,
) -> None:
    raise AppendOnlyViolation(
        "thesis_revisions is append-only; DELETE is forbidden",
        details={"revision_id": target.revision_id},
    )


def _phase1c_deny_update(
    mapper: Mapper[Any],
    connection: object,
    target: object,
) -> None:
    row_type = type(target)
    id_attr = _PHASE1C_ID_ATTR[row_type]
    entity_type = _PHASE1C_ENTITY_TYPE[row_type]
    entity_id = getattr(target, id_attr)
    raise ImmutableResearchRecord(
        f"{entity_type} is immutable; UPDATE is forbidden",
        details={"entity_type": entity_type, id_attr: entity_id},
    )


def _phase1c_deny_delete(
    mapper: Mapper[Any],
    connection: object,
    target: object,
) -> None:
    row_type = type(target)
    id_attr = _PHASE1C_ID_ATTR[row_type]
    entity_type = _PHASE1C_ENTITY_TYPE[row_type]
    entity_id = getattr(target, id_attr)
    raise ImmutableResearchRecord(
        f"{entity_type} is immutable; DELETE is forbidden",
        details={"entity_type": entity_type, id_attr: entity_id},
    )


def register_append_only_listeners() -> None:
    """Idempotently register before_update / before_delete guards."""
    global _LISTENERS_REGISTERED
    if _LISTENERS_REGISTERED:
        return
    event.listen(ThesisRevisionRow, "before_update", _deny_thesis_revision_update)
    event.listen(ThesisRevisionRow, "before_delete", _deny_thesis_revision_delete)
    for row_cls in _PHASE1C_IMMUTABLE_ROWS:
        event.listen(row_cls, "before_update", _phase1c_deny_update)
        event.listen(row_cls, "before_delete", _phase1c_deny_delete)
    _LISTENERS_REGISTERED = True


# Register on import so any repository/UoW usage is protected.
register_append_only_listeners()
