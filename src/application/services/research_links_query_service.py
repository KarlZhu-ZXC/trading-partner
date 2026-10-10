"""Read scoped evidence links and research-work metadata without side effects."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from application.dto.research import OpenQuestionCandidatePayload, parse_candidate_payload
from application.ports.clock import Clock
from application.services._research_support import UowFactory
from domain.common.enums import AssumptionStatus, CandidateKind, CandidateStatus
from domain.common.errors import DataContractError


class ResearchLinksQueryService:
    def __init__(self, uow_factory: UowFactory, clock: Clock) -> None:
        self._uow = uow_factory
        self._clock = clock

    def get(self, subject_id: str) -> dict[str, Any]:
        now = self._clock.now()
        with self._uow() as uow:
            uow.subjects.get(subject_id)
            coverage: dict[str, str] = {}
            try:
                sources = uow.research_sources.list_for_subject(subject_id, as_of=now, limit=200)
                sources_has_more = len(sources) > 200
                coverage["sources"] = "PARTIAL" if sources_has_more else "COMPLETE"
                sources = sources[:200]
            except Exception:  # noqa: BLE001 - source failure is not absence of evidence
                sources = ()
                sources_has_more = False
                coverage["sources"] = "UNAVAILABLE"
            try:
                impacts = uow.evidence_impacts.list_by_subject(subject_id, limit=201)
                impacts_has_more = len(impacts) > 200
                current = impacts[:200]
                impacts = tuple(row for row in current if row.confirmed_at <= now)
                coverage["impacts"] = (
                    "PARTIAL" if impacts_has_more or len(impacts) < len(current) else "COMPLETE"
                )
            except Exception:  # noqa: BLE001 - keep source choices available independently
                impacts = ()
                impacts_has_more = False
                coverage["impacts"] = "UNAVAILABLE"
            try:
                impact_history = uow.evidence_impacts.list_by_subject(
                    subject_id,
                    include_history=True,
                    limit=201,
                )
                coverage["impact_history"] = "PARTIAL" if len(impact_history) > 200 else "COMPLETE"
                impact_history = tuple(
                    row for row in impact_history[:200] if row.confirmed_at <= now
                )
            except Exception:  # noqa: BLE001 - preserve current links when history is unavailable
                impact_history = ()
                coverage["impact_history"] = "UNAVAILABLE"
            available = {source.source: True for source in sources}
            rendered = []
            for impact in impacts:
                if impact.source not in available:
                    try:
                        available[impact.source] = (
                            uow.research_sources.get(
                                subject_id,
                                impact.source,
                                as_of=now,
                            )
                            is not None
                        )
                    except Exception:  # noqa: BLE001 - unavailable reference never deletes a link
                        available[impact.source] = False
                        coverage["sources"] = "UNAVAILABLE"
                rendered.append({**asdict(impact), "source_available": available[impact.source]})
            targets = []
            theses = uow.theses.list_by_subject(subject_id)
            for thesis in theses[:100]:
                revision = uow.revisions.get(thesis.latest_revision_id)
                if revision.subject_id != subject_id or revision.thesis_id != thesis.thesis_id:
                    raise DataContractError("Invalid Thesis scope")
                if revision.confirmed_at > now:
                    continue
                for assumption in uow.assumptions.list_by_revision(
                    thesis.thesis_id, revision.revision_no
                ):
                    if (
                        assumption.subject_id != subject_id
                        or assumption.thesis_id != thesis.thesis_id
                    ):
                        raise DataContractError("Invalid assumption scope")
                    if (
                        assumption.status is not AssumptionStatus.ACCEPTED
                        or assumption.confirmed_at > now
                    ):
                        continue
                    targets.append(
                        {
                            "thesis_id": thesis.thesis_id,
                            "thesis_revision_id": revision.revision_id,
                            "revision_no": revision.revision_no,
                            "thesis_title": thesis.title,
                            "assumption_id": assumption.assumption_id,
                            "statement": assumption.statement,
                            "is_current": True,
                        }
                    )
            coverage["targets"] = (
                "PARTIAL" if len(theses) > 100 or len(targets) > 200 else "COMPLETE"
            )
            decisions = [
                decision
                for decision in uow.decisions.list_by_subject(subject_id)
                if decision.recorded_at <= now and decision.decided_at <= now
            ]
            decisions.sort(key=lambda item: (item.recorded_at, item.decision_id), reverse=True)
            coverage["decisions"] = "PARTIAL" if len(decisions) > 200 else "COMPLETE"
            history = uow.candidates.list(
                subject_id=subject_id,
                kind=CandidateKind.OPEN_QUESTION,
                status=CandidateStatus.CONFIRMED,
                limit=201,
            )
            question_history = []
            invalid_history = False
            for candidate in history[:200]:
                if candidate.reviewed_at is None or candidate.reviewed_at > now:
                    continue
                try:
                    payload = parse_candidate_payload(candidate.payload_json)
                except Exception:  # noqa: BLE001 - malformed legacy history cannot hide current links
                    invalid_history = True
                    continue
                if isinstance(payload, OpenQuestionCandidatePayload) and payload.question_id:
                    question_history.append(
                        {
                            "candidate_id": candidate.candidate_id,
                            "question_id": payload.question_id,
                            "reviewed_at": candidate.reviewed_at,
                            "reviewed_by": candidate.reviewed_by,
                            "action": payload.action,
                            "payload": payload.model_dump(mode="json"),
                        }
                    )
            coverage["question_history"] = (
                "PARTIAL" if len(history) > 200 or invalid_history else "COMPLETE"
            )
        return {
            "subject_id": subject_id,
            "as_of": now,
            "sources": [asdict(source) for source in sources],
            "sources_has_more": sources_has_more,
            "impacts": rendered,
            "impact_history": [asdict(row) for row in impact_history],
            "impacts_has_more": impacts_has_more,
            "targets": targets[:200],
            "decisions": [
                {"decision_id": d.decision_id, "decided_at": d.decided_at, "title": d.title}
                for d in decisions[:200]
            ],
            "question_history": question_history,
            "coverage": coverage,
        }
