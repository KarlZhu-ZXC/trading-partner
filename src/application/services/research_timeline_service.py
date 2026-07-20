"""ResearchTimelineService — unified case timeline projection (Phase 1C C4b1).

Projects Evidence / Report / Event / Decision / Journal / ThesisRevision /
Candidate resolution into a deterministic timeline. Read-only: no ResearchEvent
writes, no audit, no commit.
"""

from __future__ import annotations

from datetime import datetime

from application.dto.research_memory import (
    ResearchTimelineDTO,
    ResearchTimelineItemDTO,
)
from application.dto.tool_envelope import ToolEnvelope
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.ports.secret_redactor import SecretRedactor
from application.services._research_support import (
    UowFactory,
    envelope_failure,
    envelope_success,
)
from domain.common.enums import CandidateStatus, ResearchTimelineEntityType
from domain.common.errors import InputValidationError
from domain.common.ids import EntityIdPrefix
from domain.common.time import require_aware_datetime
from domain.research.models import (
    CandidateThesisRevision,
    DecisionRecord,
    Evidence,
    JournalEntry,
    ResearchEvent,
    ResearchReport,
    ThesisRevision,
)

_JOURNAL_PAGE_SIZE = 100
_CANDIDATE_PAGE_SIZE = 50

_RESOLUTION_STATUSES = frozenset(
    {
        CandidateStatus.CONFIRMED,
        CandidateStatus.REJECTED,
        CandidateStatus.WITHDRAWN,
    }
)

_ALL_TIMELINE_TYPE_WIRES = frozenset(t.value for t in ResearchTimelineEntityType)


def _wire(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(getattr(value, "value", value))


def _want(
    selected: frozenset[str], entity_type: ResearchTimelineEntityType
) -> bool:
    return not selected or entity_type.value in selected


def _in_occurred_window(
    occurred_at: datetime,
    *,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
) -> bool:
    too_early = occurred_from is not None and occurred_at < occurred_from
    too_late = occurred_to is not None and occurred_at > occurred_to
    return not too_early and not too_late


def _evidence_occurred_at(evidence: Evidence) -> datetime:
    if evidence.published_at is not None:
        return evidence.published_at
    if evidence.effective_from is not None:
        return evidence.effective_from
    return evidence.observed_at


def _stable_instrument_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for instrument_id in group:
            if instrument_id in seen:
                continue
            seen.add(instrument_id)
            out.append(instrument_id)
    return tuple(out)


def _candidate_title(candidate: CandidateThesisRevision) -> str:
    return f"{candidate.kind.value} {candidate.status.value}"


def _candidate_summary(candidate: CandidateThesisRevision) -> str:
    for value in (
        candidate.review_note,
        candidate.rejection_reason,
        candidate.proposed_by_rationale,
    ):
        if value is not None and value.strip():
            return value
    return ""


def _sort_timeline_items(
    items: list[ResearchTimelineItemDTO],
) -> list[ResearchTimelineItemDTO]:
    # Stable multi-pass: entity_id ASC, then visible_at DESC, then occurred_at DESC.
    items.sort(key=lambda item: item.entity_id)
    items.sort(key=lambda item: item.visible_at, reverse=True)
    items.sort(key=lambda item: item.occurred_at, reverse=True)
    return items


class ResearchTimelineService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._redactor = secret_redactor

    def get_timeline(
        self,
        *,
        case_id: str,
        entity_types: tuple[ResearchTimelineEntityType, ...] = (),
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> ToolEnvelope[ResearchTimelineDTO]:
        request_id = self._id_generator.new(EntityIdPrefix.REQ)
        try:
            case_id_n = case_id.strip()
            if not case_id_n:
                raise InputValidationError(
                    "case_id must be non-blank",
                    details={"field": "case_id"},
                )
            if type(limit) is not int or limit < 1 or limit > 500:
                raise InputValidationError(
                    "limit must be an int in [1, 500]",
                    details={"field": "limit", "limit": limit},
                )
            occurred_from_n = (
                require_aware_datetime(occurred_from, field_name="occurred_from")
                if occurred_from is not None
                else None
            )
            occurred_to_n = (
                require_aware_datetime(occurred_to, field_name="occurred_to")
                if occurred_to is not None
                else None
            )
            if (
                occurred_from_n is not None
                and occurred_to_n is not None
                and occurred_to_n < occurred_from_n
            ):
                raise InputValidationError(
                    "occurred_to must be >= occurred_from",
                    details={
                        "field": "occurred_to",
                        "occurred_from": occurred_from_n.isoformat(),
                        "occurred_to": occurred_to_n.isoformat(),
                    },
                )
            as_of_n = (
                require_aware_datetime(as_of, field_name="as_of")
                if as_of is not None
                else None
            )

            selected = frozenset(_wire(t) for t in entity_types)
            unknown = selected - _ALL_TIMELINE_TYPE_WIRES
            if unknown:
                raise InputValidationError(
                    "unsupported timeline entity_types",
                    details={"entity_types": sorted(unknown)},
                )

            with self._uow_factory() as uow:
                uow.cases.get(case_id_n)
                cutoff = as_of_n if as_of_n is not None else self._clock.now()
                items = self._collect_items(
                    uow,
                    case_id=case_id_n,
                    selected=selected,
                    occurred_from=occurred_from_n,
                    occurred_to=occurred_to_n,
                    cutoff=cutoff,
                )
                ordered = _sort_timeline_items(items)
                total = len(ordered)
                page = tuple(ordered[:limit])
                return envelope_success(
                    request_id=request_id,
                    clock=self._clock,
                    data=ResearchTimelineDTO(
                        case_id=case_id_n,
                        as_of=cutoff,
                        items=page,
                        total=total,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            return envelope_failure(
                request_id=request_id,
                clock=self._clock,
                redactor=self._redactor,
                exc=exc,
            )

    def _collect_items(
        self,
        uow: ResearchUnitOfWork,
        *,
        case_id: str,
        selected: frozenset[str],
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        cutoff: datetime,
    ) -> list[ResearchTimelineItemDTO]:
        items: list[ResearchTimelineItemDTO] = []

        if _want(selected, ResearchTimelineEntityType.EVIDENCE):
            for evidence in uow.case_evidence_links.list_evidence(
                case_id, as_of=cutoff
            ):
                item = self._from_evidence(
                    evidence,
                    case_id=case_id,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
                if item is not None:
                    items.append(item)

        if _want(selected, ResearchTimelineEntityType.REPORT):
            for report in uow.reports.list_by_case(case_id, as_of=cutoff):
                item = self._from_report(
                    uow,
                    report,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
                if item is not None:
                    items.append(item)

        if _want(selected, ResearchTimelineEntityType.EVENT):
            for event in uow.events.list_timeline(
                case_id,
                start=occurred_from,
                end=occurred_to,
                as_of=cutoff,
                event_types=(),
            ):
                items.append(self._from_event(event))

        if _want(selected, ResearchTimelineEntityType.DECISION):
            for decision in uow.decisions.list_by_case(case_id, as_of=cutoff):
                item = self._from_decision(
                    decision,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
                if item is not None:
                    items.append(item)

        if _want(selected, ResearchTimelineEntityType.JOURNAL):
            for entry in self._list_journal_exhausted(
                uow, case_id=case_id, as_of=cutoff
            ):
                item = self._from_journal(
                    entry,
                    case_id=case_id,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
                if item is not None:
                    items.append(item)

        if _want(selected, ResearchTimelineEntityType.THESIS_REVISION):
            for thesis in uow.theses.list_by_case(case_id):
                for revision in uow.revisions.list_by_thesis(thesis.thesis_id):
                    if revision.confirmed_at > cutoff:
                        continue
                    item = self._from_revision(
                        revision,
                        occurred_from=occurred_from,
                        occurred_to=occurred_to,
                    )
                    if item is not None:
                        items.append(item)

        if _want(selected, ResearchTimelineEntityType.CANDIDATE_RESOLUTION):
            for candidate in self._list_candidates_exhausted(uow, case_id=case_id):
                if candidate.status not in _RESOLUTION_STATUSES:
                    continue
                if candidate.reviewed_at is None:
                    continue
                if candidate.reviewed_at > cutoff:
                    continue
                item = self._from_candidate(
                    candidate,
                    case_id=case_id,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
                if item is not None:
                    items.append(item)

        return items

    def _list_journal_exhausted(
        self,
        uow: ResearchUnitOfWork,
        *,
        case_id: str,
        as_of: datetime,
    ) -> list[JournalEntry]:
        out: list[JournalEntry] = []
        offset = 0
        while True:
            page = uow.journal.list(
                case_id=case_id,
                as_of=as_of,
                limit=_JOURNAL_PAGE_SIZE,
                offset=offset,
            )
            if not page:
                break
            out.extend(page)
            if len(page) < _JOURNAL_PAGE_SIZE:
                break
            offset += len(page)
        return out

    def _list_candidates_exhausted(
        self,
        uow: ResearchUnitOfWork,
        *,
        case_id: str,
    ) -> list[CandidateThesisRevision]:
        out: list[CandidateThesisRevision] = []
        offset = 0
        while True:
            page = uow.candidates.list(
                case_id=case_id,
                limit=_CANDIDATE_PAGE_SIZE,
                offset=offset,
            )
            if not page:
                break
            out.extend(page)
            if len(page) < _CANDIDATE_PAGE_SIZE:
                break
            offset += len(page)
        return out

    def _item(
        self,
        *,
        entity_type: ResearchTimelineEntityType,
        entity_id: str,
        case_id: str,
        title: str,
        summary: str,
        occurred_at: datetime,
        visible_at: datetime,
        instrument_ids: tuple[str, ...],
        source_name: str | None,
    ) -> ResearchTimelineItemDTO:
        return ResearchTimelineItemDTO(
            entity_type=entity_type,
            entity_id=entity_id,
            case_id=case_id,
            title=self._redactor.redact_text(title),
            summary=self._redactor.redact_text(summary),
            occurred_at=occurred_at,
            visible_at=visible_at,
            instrument_ids=instrument_ids,
            source_name=(
                None
                if source_name is None
                else self._redactor.redact_text(source_name)
            ),
        )

    def _from_evidence(
        self,
        evidence: Evidence,
        *,
        case_id: str,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> ResearchTimelineItemDTO | None:
        occurred_at = _evidence_occurred_at(evidence)
        if not _in_occurred_window(
            occurred_at, occurred_from=occurred_from, occurred_to=occurred_to
        ):
            return None
        return self._item(
            entity_type=ResearchTimelineEntityType.EVIDENCE,
            entity_id=evidence.evidence_id,
            case_id=case_id,
            title=evidence.title,
            summary=evidence.summary,
            occurred_at=occurred_at,
            visible_at=evidence.observed_at,
            instrument_ids=evidence.instrument_ids,
            source_name=evidence.source_name,
        )

    def _from_report(
        self,
        uow: ResearchUnitOfWork,
        report: ResearchReport,
        *,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> ResearchTimelineItemDTO | None:
        if not _in_occurred_window(
            report.as_of, occurred_from=occurred_from, occurred_to=occurred_to
        ):
            return None
        instrument_groups: list[tuple[str, ...]] = []
        for evidence_id in report.evidence_ids:
            # Missing cited Evidence is a consistency failure — do not skip.
            evidence = uow.evidence.get(evidence_id)
            instrument_groups.append(evidence.instrument_ids)
        instruments = _stable_instrument_union(*instrument_groups)
        return self._item(
            entity_type=ResearchTimelineEntityType.REPORT,
            entity_id=report.report_id,
            case_id=report.case_id,
            title=report.title,
            summary=report.summary,
            occurred_at=report.as_of,
            visible_at=report.created_at,
            instrument_ids=instruments,
            source_name=None,
        )

    def _from_event(self, event: ResearchEvent) -> ResearchTimelineItemDTO:
        return self._item(
            entity_type=ResearchTimelineEntityType.EVENT,
            entity_id=event.event_id,
            case_id=event.case_id,
            title=event.title,
            summary=event.summary,
            occurred_at=event.occurred_at,
            visible_at=event.recorded_at,
            instrument_ids=event.instrument_ids,
            source_name=event.source_name,
        )

    def _from_decision(
        self,
        decision: DecisionRecord,
        *,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> ResearchTimelineItemDTO | None:
        if not _in_occurred_window(
            decision.decided_at, occurred_from=occurred_from, occurred_to=occurred_to
        ):
            return None
        instruments = (
            (decision.primary_instrument_id,)
            if decision.primary_instrument_id is not None
            else ()
        )
        return self._item(
            entity_type=ResearchTimelineEntityType.DECISION,
            entity_id=decision.decision_id,
            case_id=decision.case_id,
            title=decision.title,
            summary=decision.rationale,
            occurred_at=decision.decided_at,
            visible_at=decision.recorded_at,
            instrument_ids=instruments,
            source_name=None,
        )

    def _from_journal(
        self,
        entry: JournalEntry,
        *,
        case_id: str,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> ResearchTimelineItemDTO | None:
        if not _in_occurred_window(
            entry.created_at, occurred_from=occurred_from, occurred_to=occurred_to
        ):
            return None
        if entry.case_id is None or entry.case_id != case_id:
            return None
        return self._item(
            entity_type=ResearchTimelineEntityType.JOURNAL,
            entity_id=entry.journal_id,
            case_id=case_id,
            title=entry.title,
            summary=entry.body_markdown,
            occurred_at=entry.created_at,
            visible_at=entry.created_at,
            instrument_ids=entry.instrument_ids,
            source_name=None,
        )

    def _from_revision(
        self,
        revision: ThesisRevision,
        *,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> ResearchTimelineItemDTO | None:
        if not _in_occurred_window(
            revision.confirmed_at,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        ):
            return None
        # Fixed title from revision_no only — never current Thesis title.
        return self._item(
            entity_type=ResearchTimelineEntityType.THESIS_REVISION,
            entity_id=revision.revision_id,
            case_id=revision.case_id,
            title=f"Thesis revision {revision.revision_no}",
            summary=revision.statement,
            occurred_at=revision.confirmed_at,
            visible_at=revision.confirmed_at,
            instrument_ids=(),
            source_name=None,
        )

    def _from_candidate(
        self,
        candidate: CandidateThesisRevision,
        *,
        case_id: str,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> ResearchTimelineItemDTO | None:
        assert candidate.reviewed_at is not None
        if not _in_occurred_window(
            candidate.reviewed_at,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        ):
            return None
        return self._item(
            entity_type=ResearchTimelineEntityType.CANDIDATE_RESOLUTION,
            entity_id=candidate.candidate_id,
            case_id=case_id,
            title=_candidate_title(candidate),
            summary=_candidate_summary(candidate),
            occurred_at=candidate.reviewed_at,
            visible_at=candidate.reviewed_at,
            instrument_ids=(),
            source_name=None,
        )
