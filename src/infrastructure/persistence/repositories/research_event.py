"""SQLAlchemy ResearchEvent repository (append-only, session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import ResearchEventType
from domain.common.errors import ResearchMemoryNotFound
from domain.research.models import ResearchEvent
from infrastructure.persistence.orm import ResearchEventRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    require_case_exists,
    require_evidence_ids_linked_and_visible,
    require_instruments_exist,
    require_report_ids_visible,
)


def _to_domain(row: ResearchEventRow) -> ResearchEvent:
    return ResearchEvent(
        event_id=row.event_id,
        case_id=row.case_id,
        event_type=ResearchEventType(row.event_type),
        title=row.title,
        summary=row.summary,
        occurred_at=dt_from_db(row.occurred_at, field_name="occurred_at"),
        recorded_at=dt_from_db(row.recorded_at, field_name="recorded_at"),
        published_at=dt_opt_from_db(row.published_at, field_name="published_at"),
        instrument_ids=tuple(row.instrument_ids_json),
        evidence_ids=tuple(row.evidence_ids_json),
        report_ids=tuple(row.report_ids_json),
        related_entity_type=row.related_entity_type,
        related_entity_id=row.related_entity_id,
        source_name=row.source_name,
        schema_version=row.schema_version,
    )


def _to_row(event: ResearchEvent) -> ResearchEventRow:
    return ResearchEventRow(
        event_id=event.event_id,
        case_id=event.case_id,
        event_type=event.event_type.value,
        title=event.title,
        summary=event.summary,
        occurred_at=dt_to_db(event.occurred_at),
        recorded_at=dt_to_db(event.recorded_at),
        published_at=dt_opt_to_db(event.published_at),
        instrument_ids_json=event.instrument_ids,
        evidence_ids_json=event.evidence_ids,
        report_ids_json=event.report_ids,
        related_entity_type=event.related_entity_type,
        related_entity_id=event.related_entity_id,
        source_name=event.source_name,
        schema_version=event.schema_version,
    )


class SqlAlchemyResearchEventRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: ResearchEvent) -> None:
        require_case_exists(self._session, event.case_id)
        require_instruments_exist(self._session, event.instrument_ids)
        # Event visible_at is recorded_at for both evidence observed_at and link.
        require_evidence_ids_linked_and_visible(
            self._session,
            case_id=event.case_id,
            evidence_ids=event.evidence_ids,
            observed_at_not_after=event.recorded_at,
            linked_at_not_after=event.recorded_at,
        )
        require_report_ids_visible(
            self._session,
            case_id=event.case_id,
            report_ids=event.report_ids,
            visible_at=event.recorded_at,
        )
        # related entity pair: domain-only; no string-based cross-table resolution
        self._session.add(_to_row(event))
        self._session.flush()

    def get(self, event_id: str) -> ResearchEvent:
        row = self._session.get(ResearchEventRow, event_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "ResearchEvent not found",
                details={"entity_type": "event", "event_id": event_id},
            )
        return _to_domain(row)

    def list_timeline(
        self,
        case_id: str,
        *,
        start: datetime | None,
        end: datetime | None,
        as_of: datetime | None,
        event_types: tuple[ResearchEventType, ...],
    ) -> tuple[ResearchEvent, ...]:
        stmt = select(ResearchEventRow).where(ResearchEventRow.case_id == case_id)
        if start is not None:
            stmt = stmt.where(ResearchEventRow.occurred_at >= dt_to_db(start))
        if end is not None:
            stmt = stmt.where(ResearchEventRow.occurred_at <= dt_to_db(end))
        if as_of is not None:
            stmt = stmt.where(ResearchEventRow.recorded_at <= dt_to_db(as_of))
        if event_types:
            stmt = stmt.where(
                ResearchEventRow.event_type.in_([t.value for t in event_types])
            )
        stmt = stmt.order_by(
            ResearchEventRow.occurred_at.desc(),
            ResearchEventRow.recorded_at.desc(),
            ResearchEventRow.event_id.asc(),
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())
