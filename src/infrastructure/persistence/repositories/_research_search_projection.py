"""Search document projection builders (Phase 1C C3).

Pure helpers that map business-source fields into FTS projection columns and
structured mapping payloads. No Session I/O lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.common.enums import ResearchSearchEntityType
from domain.research.models import (
    DecisionRecord,
    Evidence,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    SubjectEvidenceLink,
)
from infrastructure.persistence.repositories._research_search_normalization import (
    normalize_fts_text,
)

_BODY_MAX_CHARS = 200_000


@dataclass(frozen=True, slots=True)
class SubjectMembershipProjection:
    subject_id: str
    membership_visible_at: datetime


@dataclass(frozen=True, slots=True)
class SearchDocumentProjection:
    entity_type: ResearchSearchEntityType
    entity_id: str
    title_fts: str
    body_fts: str
    topic_tags_fts: str
    instrument_ids: tuple[str, ...]
    topic_tags: tuple[str, ...]
    case_memberships: tuple[SubjectMembershipProjection, ...]
    visible_at: datetime
    occurred_at: datetime | None
    supersedes_entity_id: str | None


def stable_instrument_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Stable de-duplicated union preserving first-seen order across groups."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for instrument_id in group:
            if instrument_id in seen:
                continue
            seen.add(instrument_id)
            out.append(instrument_id)
    return tuple(out)


def instrument_ids_text(instrument_ids: tuple[str, ...]) -> str:
    """Space-join raw instrument IDs for diagnostic projection only."""
    return " ".join(instrument_ids)


def compose_body_fts(*, summary: str, body_content: str) -> str:
    """``normalize_fts_text(summary + "\\n" + body_content)`` truncated to 200k."""
    raw = f"{summary}\n{body_content}"
    return normalize_fts_text(raw)[:_BODY_MAX_CHARS]


def topic_tags_fts(tags: tuple[str, ...]) -> str:
    return normalize_fts_text(" ".join(tags))


def evidence_body_content(evidence: Evidence) -> str:
    if evidence.content_text is not None:
        return evidence.content_text
    if evidence.structured_data_json is not None:
        return evidence.structured_data_json
    return ""


def evidence_occurred_at(evidence: Evidence) -> datetime:
    if evidence.published_at is not None:
        return evidence.published_at
    if evidence.effective_from is not None:
        return evidence.effective_from
    return evidence.observed_at


def project_evidence(
    evidence: Evidence,
    *,
    links: tuple[SubjectEvidenceLink, ...],
) -> SearchDocumentProjection:
    tags = evidence.topic_tags
    memberships = tuple(
        SubjectMembershipProjection(
            subject_id=link.subject_id,
            membership_visible_at=link.linked_at,
        )
        for link in sorted(links, key=lambda item: (item.subject_id, item.link_id))
    )
    return SearchDocumentProjection(
        entity_type=ResearchSearchEntityType.EVIDENCE,
        entity_id=evidence.evidence_id,
        title_fts=normalize_fts_text(evidence.title),
        body_fts=compose_body_fts(
            summary=evidence.summary,
            body_content=evidence_body_content(evidence),
        ),
        topic_tags_fts=topic_tags_fts(tags),
        instrument_ids=evidence.instrument_ids,
        topic_tags=tags,
        case_memberships=memberships,
        visible_at=evidence.observed_at,
        occurred_at=evidence_occurred_at(evidence),
        supersedes_entity_id=evidence.supersedes_evidence_id,
    )


def project_report(
    report: ResearchReport,
    *,
    referenced_instrument_ids: tuple[str, ...],
) -> SearchDocumentProjection:
    instruments = stable_instrument_union(referenced_instrument_ids)
    return SearchDocumentProjection(
        entity_type=ResearchSearchEntityType.REPORT,
        entity_id=report.report_id,
        title_fts=normalize_fts_text(report.title),
        body_fts=compose_body_fts(
            summary=report.summary,
            body_content=report.content_markdown,
        ),
        topic_tags_fts=topic_tags_fts(()),
        instrument_ids=instruments,
        topic_tags=(),
        case_memberships=(
            SubjectMembershipProjection(
                subject_id=report.subject_id,
                membership_visible_at=report.created_at,
            ),
        ),
        visible_at=report.created_at,
        occurred_at=report.as_of,
        supersedes_entity_id=report.supersedes_report_id,
    )


def project_event(event: ResearchEvent) -> SearchDocumentProjection:
    # Event body_content is empty; summary appears once via compose_body_fts.
    return SearchDocumentProjection(
        entity_type=ResearchSearchEntityType.EVENT,
        entity_id=event.event_id,
        title_fts=normalize_fts_text(event.title),
        body_fts=compose_body_fts(summary=event.summary, body_content=""),
        topic_tags_fts=topic_tags_fts(()),
        instrument_ids=event.instrument_ids,
        topic_tags=(),
        case_memberships=(
            SubjectMembershipProjection(
                subject_id=event.subject_id,
                membership_visible_at=event.recorded_at,
            ),
        ),
        visible_at=event.recorded_at,
        occurred_at=event.occurred_at,
        supersedes_entity_id=None,
    )


def project_decision(
    decision: DecisionRecord,
    *,
    referenced_instrument_ids: tuple[str, ...],
) -> SearchDocumentProjection:
    primary = (
        (decision.primary_instrument_id,) if decision.primary_instrument_id is not None else ()
    )
    instruments = stable_instrument_union(primary, referenced_instrument_ids)
    return SearchDocumentProjection(
        entity_type=ResearchSearchEntityType.DECISION,
        entity_id=decision.decision_id,
        title_fts=normalize_fts_text(decision.title),
        body_fts=compose_body_fts(
            summary="",
            body_content=decision.rationale,
        ),
        topic_tags_fts=topic_tags_fts(()),
        instrument_ids=instruments,
        topic_tags=(),
        case_memberships=(
            SubjectMembershipProjection(
                subject_id=decision.subject_id,
                membership_visible_at=decision.recorded_at,
            ),
        ),
        visible_at=decision.recorded_at,
        occurred_at=decision.decided_at,
        supersedes_entity_id=decision.supersedes_decision_id,
    )


def project_journal(entry: JournalEntry) -> SearchDocumentProjection:
    tags = entry.topic_tags
    memberships: tuple[SubjectMembershipProjection, ...]
    if entry.subject_id is None:
        memberships = ()
    else:
        memberships = (
            SubjectMembershipProjection(
                subject_id=entry.subject_id,
                membership_visible_at=entry.created_at,
            ),
        )
    return SearchDocumentProjection(
        entity_type=ResearchSearchEntityType.JOURNAL,
        entity_id=entry.journal_id,
        title_fts=normalize_fts_text(entry.title),
        body_fts=compose_body_fts(
            summary="",
            body_content=entry.body_markdown,
        ),
        topic_tags_fts=topic_tags_fts(tags),
        instrument_ids=entry.instrument_ids,
        topic_tags=tags,
        case_memberships=memberships,
        visible_at=entry.created_at,
        occurred_at=entry.created_at,
        supersedes_entity_id=entry.supersedes_journal_id,
    )
