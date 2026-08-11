"""Durable research-fact reader used to close Catalyst Agenda outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from domain.common.enums import ResearchSubjectType


@dataclass(frozen=True, slots=True)
class AgendaOutcomeSnapshot:
    """Validated, point-in-time facts behind one Agenda outcome link."""

    subject_id: str
    subject_type: ResearchSubjectType
    event_instrument_ids: tuple[str, ...]
    report_instrument_ids: tuple[str, ...]
    evidence_instrument_ids: tuple[str, ...]
    resolved_evidence_ids: tuple[str, ...]
    fact_visible_at: datetime
    event_occurred_at: datetime | None


class CatalystAgendaOutcomeReader(Protocol):
    def resolve(
        self,
        *,
        event_id: str | None,
        report_id: str | None,
        evidence_id: str | None,
        subject_id: str | None,
        as_of: datetime,
    ) -> AgendaOutcomeSnapshot: ...
