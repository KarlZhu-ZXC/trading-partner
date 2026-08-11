"""Point-in-time ResearchEvent/Report/Evidence validation for Catalyst Agenda."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from application.ports.catalyst_agenda_outcome_reader import AgendaOutcomeSnapshot
from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.errors import (
    HistoricalVisibilityViolation,
    InputValidationError,
    InvalidResearchLink,
)
from domain.common.time import require_aware_datetime


class SqlAlchemyCatalystAgendaOutcomeReader:
    """Resolve immutable research facts without exposing ORM rows to the service."""

    def __init__(self, uow_factory: Callable[[], ResearchUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def resolve(
        self,
        *,
        event_id: str | None,
        report_id: str | None,
        evidence_id: str | None,
        subject_id: str | None,
        as_of: datetime,
    ) -> AgendaOutcomeSnapshot:
        visible_at = require_aware_datetime(as_of, field_name="as_of")
        if event_id is None and report_id is None and evidence_id is None:
            raise InputValidationError("event_id, report_id, or evidence_id is required")

        with self._uow_factory() as uow:
            event = uow.events.get(event_id) if event_id is not None else None
            report = uow.reports.get(report_id) if report_id is not None else None
            fact_subject_ids = {
                value
                for value in (
                    event.subject_id if event is not None else None,
                    report.subject_id if report is not None else None,
                )
                if value is not None
            }
            if len(fact_subject_ids) > 1:
                raise InvalidResearchLink(
                    "linked Event and Report must belong to the same Research Subject",
                    details={"event_id": event_id, "report_id": report_id},
                )
            if subject_id is not None and fact_subject_ids and subject_id not in fact_subject_ids:
                raise InvalidResearchLink(
                    "Agenda outcome facts must belong to the Agenda Research Subject",
                    details={
                        "subject_id": subject_id,
                        "outcome_subject_id": next(iter(fact_subject_ids)),
                    },
                )
            resolved_subject_id = (
                next(iter(fact_subject_ids)) if fact_subject_ids else subject_id
            )
            if evidence_id is not None and resolved_subject_id is None:
                linked_subject_ids = uow.subject_evidence_links.list_subjects(evidence_id)
                if len(linked_subject_ids) != 1:
                    raise InvalidResearchLink(
                        "direct Agenda outcome Evidence must resolve to exactly one "
                        "Research Subject",
                        details={
                            "evidence_id": evidence_id,
                            "linked_subject_count": len(linked_subject_ids),
                        },
                    )
                resolved_subject_id = linked_subject_ids[0]
            if resolved_subject_id is None:
                raise InvalidResearchLink("Agenda outcome facts require a Research Subject")
            subject = uow.subjects.get(resolved_subject_id)

            fact_times: list[datetime] = []
            evidence_ids: list[str] = []
            event_instruments: list[str] = []
            report_instruments: list[str] = []
            direct_evidence_instruments: list[str] = []
            if event is not None:
                if event.recorded_at > visible_at or event.occurred_at > visible_at:
                    raise HistoricalVisibilityViolation(
                        "Agenda outcome Event is not visible and occurred by as_of",
                        details={"event_id": event.event_id, "as_of": visible_at.isoformat()},
                    )
                fact_times.extend((event.occurred_at, event.recorded_at))
                event_instruments.extend(event.instrument_ids)
                evidence_ids.extend(event.evidence_ids)
            if report is not None:
                if report.created_at > visible_at or report.as_of > visible_at:
                    raise HistoricalVisibilityViolation(
                        "Agenda outcome Report is not visible by as_of",
                        details={"report_id": report.report_id, "as_of": visible_at.isoformat()},
                    )
                fact_times.extend((report.as_of, report.created_at))
                evidence_ids.extend(report.evidence_ids)
            if evidence_id is not None:
                evidence_ids.append(evidence_id)

            resolved_evidence_ids = _stable_unique(evidence_ids)
            evidence_by_id: dict[str, object] = {}
            for evidence_id_value in resolved_evidence_ids:
                evidence = uow.evidence.get(evidence_id_value)
                evidence_by_id[evidence_id_value] = evidence
                if evidence.observed_at > visible_at:
                    raise HistoricalVisibilityViolation(
                        "Agenda outcome Evidence is not visible by as_of",
                        details={
                            "evidence_id": evidence_id_value,
                            "subject_id": resolved_subject_id,
                            "as_of": visible_at.isoformat(),
                        },
                    )
                if not uow.subject_evidence_links.exists(
                    resolved_subject_id, evidence_id_value
                ):
                    raise InvalidResearchLink(
                        "Agenda outcome Evidence must be linked to the same Research Subject",
                        details={
                            "evidence_id": evidence_id_value,
                            "subject_id": resolved_subject_id,
                        },
                    )
                link = uow.subject_evidence_links.get(
                    resolved_subject_id, evidence_id_value
                )
                if link.linked_at > visible_at:
                    raise HistoricalVisibilityViolation(
                        "Agenda outcome Evidence link is not visible by as_of",
                        details={
                            "evidence_id": evidence_id_value,
                            "subject_id": resolved_subject_id,
                            "as_of": visible_at.isoformat(),
                        },
                    )
                fact_times.extend((evidence.observed_at, link.linked_at))

            if report is not None:
                for report_evidence_id in report.evidence_ids:
                    report_instruments.extend(
                        getattr(evidence_by_id[report_evidence_id], "instrument_ids", ())
                    )
                if subject.primary_instrument_id is not None:
                    report_instruments.append(subject.primary_instrument_id)
            if evidence_id is not None:
                direct_evidence_instruments.extend(
                    getattr(evidence_by_id[evidence_id], "instrument_ids", ())
                )

            return AgendaOutcomeSnapshot(
                subject_id=resolved_subject_id,
                subject_type=subject.subject_type,
                event_instrument_ids=_stable_unique(event_instruments),
                report_instrument_ids=_stable_unique(report_instruments),
                evidence_instrument_ids=_stable_unique(direct_evidence_instruments),
                resolved_evidence_ids=resolved_evidence_ids,
                fact_visible_at=max(fact_times),
                event_occurred_at=event.occurred_at if event is not None else None,
            )


def _stable_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
