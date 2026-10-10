"""Separate exact-revision evidence cohorts; never sum links into an evidence score."""

from datetime import datetime
from typing import Any

from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.research.evidence_impact import EvidenceImpact


def reviewed_impact_cohort(
    uow: ResearchUnitOfWork,
    subject_id: str,
    revision_id: str,
    as_of: datetime,
    assessment_evidence_ids: set[str],
) -> dict[str, Any]:
    try:
        history = uow.evidence_impacts.list_by_subject(subject_id, include_history=True, limit=501)
        if len(history) > 500:
            return {
                "coverage": "PARTIAL",
                "link_count": None,
                "source_count": None,
                "overlap_with_assessments": None,
                "missing_sources": None,
                "refs": [],
            }
        latest: dict[str, EvidenceImpact] = {}
        for row in history:
            if row.subject_id != subject_id or row.confirmed_at > as_of:
                continue
            prior = latest.get(row.impact_id)
            if prior is None or row.version > prior.version:
                latest[row.impact_id] = row
        links = tuple(
            row
            for row in latest.values()
            if row.thesis_revision_id == revision_id and row.state == "ACTIVE"
        )
        sources = {row.source for row in links}
        missing = sum(
            uow.research_sources.get(subject_id, ref, as_of=as_of) is None for ref in sources
        )
        return {
            "coverage": "COMPLETE",
            "link_count": len(links),
            "source_count": len(sources),
            "overlap_with_assessments": len(
                {ref.source_id for ref in sources if ref.kind == "EVIDENCE"}
                & assessment_evidence_ids
            ),
            "missing_sources": missing,
            "stances": {
                stance: sum(row.stance == stance for row in links)
                for stance in ("supports", "contradicts", "uncertain")
            },
            "refs": [{"impact_id": row.impact_id, "version": row.version} for row in links],
        }
    except Exception:  # noqa: BLE001 - unavailable is not zero evidence
        return {
            "coverage": "UNAVAILABLE",
            "link_count": None,
            "source_count": None,
            "overlap_with_assessments": None,
            "missing_sources": None,
            "refs": [],
        }
