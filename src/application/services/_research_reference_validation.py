"""Research Subject reference validators for research-memory writes.

This module owns the application-level membership, related-entity registry, and
historical-visibility rules for Report, Event, Journal, and Decision writes.
The validator entry points intentionally remain separate because Event and
Journal have different wire registries and visibility contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.errors import HistoricalVisibilityViolation, InvalidResearchLink
from domain.common.time import require_aware_datetime

# Frozen Event related-entity wire types (Phase 1C C4a §8.2).
# The historical case token denotes a Research Subject and remains wire ABI.
# Type event is intentionally excluded for Event writes (no self-related).
EVENT_RELATED_ENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "case",
        "thesis",
        "thesis_revision",
        "evidence",
        "report",
        "decision",
        "journal",
    }
)

# Frozen Journal related-entity wire types (Phase 1C C4b2 §8.5).
# event is allowed after C4b1 added ResearchEventRepository.get.
JOURNAL_RELATED_ENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "case",
        "thesis",
        "thesis_revision",
        "evidence",
        "report",
        "event",
        "decision",
        "journal",
    }
)

def validate_report_references(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    as_of: datetime,
    created_at: datetime,
    evidence_ids: tuple[str, ...],
    thesis_revision_ids: tuple[str, ...],
    supersedes_report_id: str | None,
) -> None:
    """Application-level Research Subject membership + historical visibility for Report."""
    require_aware_datetime(as_of, field_name="as_of")
    require_aware_datetime(created_at, field_name="created_at")
    uow.subjects.get(subject_id)

    for evidence_id in evidence_ids:
        evidence = uow.evidence.get(evidence_id)
        if evidence.observed_at > as_of:
            raise HistoricalVisibilityViolation(
                "report as_of must not precede evidence observed_at",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )
        if not uow.subject_evidence_links.exists(subject_id, evidence_id):
            raise InvalidResearchLink(
                "evidence must be linked to the report subject",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )
        link = uow.subject_evidence_links.get(subject_id, evidence_id)
        # Frozen rule: Link.linked_at <= report created_at (not as_of).
        if link.linked_at > created_at:
            raise HistoricalVisibilityViolation(
                "report created_at must not precede subject evidence link linked_at",
                details={
                    "entity_type": "subject_evidence_link",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )

    for revision_id in thesis_revision_ids:
        revision = uow.revisions.get(revision_id)
        if revision.subject_id != subject_id:
            raise InvalidResearchLink(
                "thesis revision does not belong to the report subject",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "subject_id": subject_id,
                },
            )
        if revision.confirmed_at > as_of:
            raise HistoricalVisibilityViolation(
                "report as_of must not precede thesis revision confirmed_at",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "subject_id": subject_id,
                },
            )

    if supersedes_report_id is not None:
        old = uow.reports.get(supersedes_report_id)
        if old.subject_id != subject_id:
            raise InvalidResearchLink(
                "superseded report does not belong to the same subject",
                details={
                    "entity_type": "report",
                    "supersedes_report_id": supersedes_report_id,
                    "subject_id": subject_id,
                },
            )
        if old.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "superseded report created_at must be <= new created_at",
                details={
                    "entity_type": "report",
                    "supersedes_report_id": supersedes_report_id,
                },
            )


def validate_event_references(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    recorded_at: datetime,
    evidence_ids: tuple[str, ...],
    report_ids: tuple[str, ...],
) -> None:
    """Reject cross-Research Subject references and future leakage for Event writes."""
    require_aware_datetime(recorded_at, field_name="recorded_at")
    uow.subjects.get(subject_id)

    for evidence_id in evidence_ids:
        evidence = uow.evidence.get(evidence_id)
        if evidence.observed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede evidence observed_at",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )
        if not uow.subject_evidence_links.exists(subject_id, evidence_id):
            raise InvalidResearchLink(
                "evidence must be linked to the event subject",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )
        link = uow.subject_evidence_links.get(subject_id, evidence_id)
        if link.linked_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede subject evidence link linked_at",
                details={
                    "entity_type": "subject_evidence_link",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )

    for report_id in report_ids:
        report = uow.reports.get(report_id)
        if report.subject_id != subject_id:
            raise InvalidResearchLink(
                "report does not belong to the event subject",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "subject_id": subject_id,
                },
            )
        if report.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede report created_at",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "subject_id": subject_id,
                },
            )


def validate_event_related_entity(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    recorded_at: datetime,
    related_entity_type: str | None,
    related_entity_id: str | None,
) -> None:
    """Typed registry for Event generic related pair (design §8.2).

    Uses existing repository ports only. Rejects unknown types and ``event``.
    """
    require_aware_datetime(recorded_at, field_name="recorded_at")
    if related_entity_type is None and related_entity_id is None:
        return
    if related_entity_type is None or related_entity_id is None:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must both be set or both null",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )

    rel_type = related_entity_type.strip()
    rel_id = related_entity_id.strip()
    if not rel_type or not rel_id:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must be non-blank when set",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )

    if rel_type == "event":
        raise InvalidResearchLink(
            "event related_entity_type is not allowed in Phase 1C",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
            },
        )
    if rel_type not in EVENT_RELATED_ENTITY_TYPES:
        raise InvalidResearchLink(
            "unknown related_entity_type for research event",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "allowed": sorted(EVENT_RELATED_ENTITY_TYPES),
            },
        )

    if rel_type == "case":
        if rel_id != subject_id:
            raise InvalidResearchLink(
                "related subject must equal the event subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        uow.subjects.get(rel_id)
        return

    if rel_type == "thesis":
        thesis = uow.theses.get(rel_id)
        if thesis.subject_id != subject_id:
            raise InvalidResearchLink(
                "related thesis does not belong to the event subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if thesis.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede thesis created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "thesis_revision":
        revision = uow.revisions.get(rel_id)
        if revision.subject_id != subject_id:
            raise InvalidResearchLink(
                "related thesis revision does not belong to the event subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if revision.confirmed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede thesis revision confirmed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "evidence":
        evidence = uow.evidence.get(rel_id)
        if evidence.observed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related evidence observed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if not uow.subject_evidence_links.exists(subject_id, rel_id):
            raise InvalidResearchLink(
                "related evidence must be linked to the event subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        link = uow.subject_evidence_links.get(subject_id, rel_id)
        if link.linked_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related evidence link linked_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "report":
        report = uow.reports.get(rel_id)
        if report.subject_id != subject_id:
            raise InvalidResearchLink(
                "related report does not belong to the event subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if report.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related report created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "decision":
        decision = uow.decisions.get(rel_id)
        if decision.subject_id != subject_id:
            raise InvalidResearchLink(
                "related decision does not belong to the event subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if decision.recorded_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "event recorded_at must not precede related decision recorded_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    # journal
    journal = uow.journal.get(rel_id)
    if journal.subject_id is None:
        raise InvalidResearchLink(
            "global journal cannot be related to a subject-scoped event",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "subject_id": subject_id,
            },
        )
    if journal.subject_id != subject_id:
        raise InvalidResearchLink(
            "related journal does not belong to the event subject",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "subject_id": subject_id,
            },
        )
    if journal.created_at > recorded_at:
        raise HistoricalVisibilityViolation(
            "event recorded_at must not precede related journal created_at",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "subject_id": subject_id,
            },
        )


def _normalize_related_pair(
    related_entity_type: str | None,
    related_entity_id: str | None,
) -> tuple[str | None, str | None]:
    if related_entity_type is None and related_entity_id is None:
        return None, None
    if related_entity_type is None or related_entity_id is None:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must both be set or both null",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )
    rel_type = related_entity_type.strip()
    rel_id = related_entity_id.strip()
    if not rel_type or not rel_id:
        raise InvalidResearchLink(
            "related_entity_type and related_entity_id must be non-blank when set",
            details={
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
            },
        )
    return rel_type, rel_id


def validate_journal_related_entity(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str | None,
    created_at: datetime,
    related_entity_type: str | None,
    related_entity_id: str | None,
) -> None:
    """Typed registry for Journal generic related pair (design §8.5).

    Research Subject-scoped Journal: related entity must share the subject and be visible at
    ``created_at``. Global Journal (``subject_id is None``) may only relate to
    another global Journal. Evidence also requires a visible Research Subject link.
    """
    require_aware_datetime(created_at, field_name="created_at")
    rel_type, rel_id = _normalize_related_pair(related_entity_type, related_entity_id)
    if rel_type is None or rel_id is None:
        return

    if rel_type not in JOURNAL_RELATED_ENTITY_TYPES:
        raise InvalidResearchLink(
            "unknown related_entity_type for journal entry",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "allowed": sorted(JOURNAL_RELATED_ENTITY_TYPES),
            },
        )

    # Global journal: only other global journals are allowed.
    if subject_id is None:
        if rel_type != "journal":
            raise InvalidResearchLink(
                "global journal may only relate to a global journal",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": None,
                },
            )
        journal = uow.journal.get(rel_id)
        if journal.subject_id is not None:
            raise InvalidResearchLink(
                "global journal may only relate to a global journal",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "related_subject_id": journal.subject_id,
                },
            )
        if journal.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related journal created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                },
            )
        return

    # Research Subject-scoped journal related registry.
    if rel_type == "case":
        if rel_id != subject_id:
            raise InvalidResearchLink(
                "related subject must equal the journal subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        uow.subjects.get(rel_id)
        return

    if rel_type == "thesis":
        thesis = uow.theses.get(rel_id)
        if thesis.subject_id != subject_id:
            raise InvalidResearchLink(
                "related thesis does not belong to the journal subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if thesis.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede thesis created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "thesis_revision":
        revision = uow.revisions.get(rel_id)
        if revision.subject_id != subject_id:
            raise InvalidResearchLink(
                "related thesis revision does not belong to the journal subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if revision.confirmed_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede thesis revision confirmed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "evidence":
        evidence = uow.evidence.get(rel_id)
        if evidence.observed_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related evidence observed_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if not uow.subject_evidence_links.exists(subject_id, rel_id):
            raise InvalidResearchLink(
                "related evidence must be linked to the journal subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        link = uow.subject_evidence_links.get(subject_id, rel_id)
        if link.linked_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related evidence link linked_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "report":
        report = uow.reports.get(rel_id)
        if report.subject_id != subject_id:
            raise InvalidResearchLink(
                "related report does not belong to the journal subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if report.created_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related report created_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "event":
        event = uow.events.get(rel_id)
        if event.subject_id != subject_id:
            raise InvalidResearchLink(
                "related event does not belong to the journal subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if event.recorded_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related event recorded_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    if rel_type == "decision":
        decision = uow.decisions.get(rel_id)
        if decision.subject_id != subject_id:
            raise InvalidResearchLink(
                "related decision does not belong to the journal subject",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        if decision.recorded_at > created_at:
            raise HistoricalVisibilityViolation(
                "journal created_at must not precede related decision recorded_at",
                details={
                    "related_entity_type": rel_type,
                    "related_entity_id": rel_id,
                    "subject_id": subject_id,
                },
            )
        return

    # journal
    journal = uow.journal.get(rel_id)
    if journal.subject_id is None:
        raise InvalidResearchLink(
            "global journal cannot be related to a subject-scoped journal",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "subject_id": subject_id,
            },
        )
    if journal.subject_id != subject_id:
        raise InvalidResearchLink(
            "related journal does not belong to the journal subject",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "subject_id": subject_id,
            },
        )
    if journal.created_at > created_at:
        raise HistoricalVisibilityViolation(
            "journal created_at must not precede related journal created_at",
            details={
                "related_entity_type": rel_type,
                "related_entity_id": rel_id,
                "subject_id": subject_id,
            },
        )


def validate_journal_supersedes(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str | None,
    created_at: datetime,
    supersedes_journal_id: str | None,
) -> None:
    """Superseded journal must exist, share subject (incl. both None), and be older."""
    if supersedes_journal_id is None:
        return
    require_aware_datetime(created_at, field_name="created_at")
    old = uow.journal.get(supersedes_journal_id)
    if old.subject_id != subject_id:
        raise InvalidResearchLink(
            "superseded journal does not share the same subject_id",
            details={
                "entity_type": "journal",
                "supersedes_journal_id": supersedes_journal_id,
                "subject_id": subject_id,
                "old_subject_id": old.subject_id,
            },
        )
    if old.created_at > created_at:
        raise HistoricalVisibilityViolation(
            "superseded journal created_at must be <= new created_at",
            details={
                "entity_type": "journal",
                "supersedes_journal_id": supersedes_journal_id,
            },
        )


def validate_decision_references(
    uow: ResearchUnitOfWork,
    *,
    subject_id: str,
    recorded_at: datetime,
    thesis_revision_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    report_ids: tuple[str, ...],
    supersedes_decision_id: str | None,
) -> None:
    """Application-level Research Subject membership + historical visibility for Decision."""
    require_aware_datetime(recorded_at, field_name="recorded_at")
    uow.subjects.get(subject_id)

    for revision_id in thesis_revision_ids:
        revision = uow.revisions.get(revision_id)
        if revision.subject_id != subject_id:
            raise InvalidResearchLink(
                "thesis revision does not belong to the decision subject",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "subject_id": subject_id,
                },
            )
        if revision.confirmed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede thesis revision confirmed_at",
                details={
                    "entity_type": "thesis_revision",
                    "thesis_revision_id": revision_id,
                    "subject_id": subject_id,
                },
            )

    for evidence_id in evidence_ids:
        evidence = uow.evidence.get(evidence_id)
        if evidence.observed_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede evidence observed_at",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )
        if not uow.subject_evidence_links.exists(subject_id, evidence_id):
            raise InvalidResearchLink(
                "evidence must be linked to the decision subject",
                details={
                    "entity_type": "evidence",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )
        link = uow.subject_evidence_links.get(subject_id, evidence_id)
        if link.linked_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede subject evidence link linked_at",
                details={
                    "entity_type": "subject_evidence_link",
                    "evidence_id": evidence_id,
                    "subject_id": subject_id,
                },
            )

    for report_id in report_ids:
        report = uow.reports.get(report_id)
        if report.subject_id != subject_id:
            raise InvalidResearchLink(
                "report does not belong to the decision subject",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "subject_id": subject_id,
                },
            )
        if report.created_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "decision recorded_at must not precede report created_at",
                details={
                    "entity_type": "report",
                    "report_id": report_id,
                    "subject_id": subject_id,
                },
            )

    if supersedes_decision_id is not None:
        old = uow.decisions.get(supersedes_decision_id)
        if old.subject_id != subject_id:
            raise InvalidResearchLink(
                "superseded decision does not belong to the same subject",
                details={
                    "entity_type": "decision",
                    "supersedes_decision_id": supersedes_decision_id,
                    "subject_id": subject_id,
                },
            )
        if old.recorded_at > recorded_at:
            raise HistoricalVisibilityViolation(
                "superseded decision recorded_at must be <= new recorded_at",
                details={
                    "entity_type": "decision",
                    "supersedes_decision_id": supersedes_decision_id,
                },
            )


