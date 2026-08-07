"""SQLAlchemy ResearchSubject repository (session-bound)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from domain.common.enums import ResearchSubjectStatus, ResearchSubjectType, ThesisRole, ThesisStatus
from domain.common.errors import ResearchSubjectNotFound
from domain.research.models import ResearchSubject
from infrastructure.persistence.orm import ResearchSubjectRow, ThesisRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: ResearchSubjectRow) -> ResearchSubject:
    return ResearchSubject(
        subject_id=row.subject_id,
        subject_type=ResearchSubjectType(row.subject_type),
        title=row.title,
        summary=row.summary,
        status=ResearchSubjectStatus(row.status),
        primary_instrument_id=row.primary_instrument_id,
        topic_tags=tuple(row.topic_tags_json),
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        updated_at=dt_from_db(row.updated_at, field_name="updated_at"),
        created_by=row.created_by,
        archived_at=dt_opt_from_db(row.archived_at, field_name="archived_at"),
        archived_reason=row.archived_reason,
        linked_subject_ids=tuple(row.linked_subject_ids_json),
        evidence_ids=tuple(row.evidence_ids_json),
        report_ids=tuple(row.report_ids_json),
        event_ids=tuple(row.event_ids_json),
        decision_ids=tuple(row.decision_ids_json),
        schema_version=row.schema_version,
    )


def _to_row(subject: ResearchSubject) -> ResearchSubjectRow:
    return ResearchSubjectRow(
        subject_id=subject.subject_id,
        subject_type=subject.subject_type.value,
        title=subject.title,
        summary=subject.summary,
        status=subject.status.value,
        primary_instrument_id=subject.primary_instrument_id,
        topic_tags_json=subject.topic_tags,
        created_at=dt_to_db(subject.created_at),
        updated_at=dt_to_db(subject.updated_at),
        created_by=subject.created_by,
        archived_at=dt_opt_to_db(subject.archived_at),
        archived_reason=subject.archived_reason,
        linked_subject_ids_json=subject.linked_subject_ids,
        evidence_ids_json=subject.evidence_ids,
        report_ids_json=subject.report_ids,
        event_ids_json=subject.event_ids,
        decision_ids_json=subject.decision_ids,
        schema_version=subject.schema_version,
    )


class SqlAlchemyResearchSubjectRepository:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def get(self, subject_id: str) -> ResearchSubject:
        row = self._session.get(ResearchSubjectRow, subject_id)
        if row is None:
            raise ResearchSubjectNotFound(
                f"ResearchSubject not found: {subject_id}",
                details={"subject_id": subject_id},
            )
        return _to_domain(row)

    def list(
        self,
        *,
        subject_type: ResearchSubjectType | None = None,
        status: ResearchSubjectStatus | None = None,
        primary_instrument_id: str | None = None,
        topic_tag: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ResearchSubject, ...]:
        stmt = select(ResearchSubjectRow)
        if subject_type is not None:
            stmt = stmt.where(ResearchSubjectRow.subject_type == subject_type.value)
        if status is not None:
            stmt = stmt.where(ResearchSubjectRow.status == status.value)
        if primary_instrument_id is not None:
            stmt = stmt.where(ResearchSubjectRow.primary_instrument_id == primary_instrument_id)
        if not include_archived and status is None:
            stmt = stmt.where(ResearchSubjectRow.status != ResearchSubjectStatus.ARCHIVED.value)
        stmt = stmt.order_by(ResearchSubjectRow.updated_at.desc())
        if topic_tag is None:
            stmt = stmt.offset(offset).limit(limit)
            return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

        # Correctness-first: filter the full candidate set in Python, then page.
        items = [_to_domain(row) for row in self._session.scalars(stmt).all()]
        needle = topic_tag.strip().lower()
        filtered = [c for c in items if needle in {t.lower() for t in c.topic_tags}]
        return tuple(filtered[offset : offset + limit])

    def add(self, subject: ResearchSubject) -> None:
        self._session.add(_to_row(subject))
        self._session.flush()

    def update(self, subject: ResearchSubject) -> None:
        row = self._session.get(ResearchSubjectRow, subject.subject_id, with_for_update=True)
        if row is None:
            raise ResearchSubjectNotFound(
                f"ResearchSubject not found: {subject.subject_id}",
                details={"subject_id": subject.subject_id},
            )
        now = self._clock.now()
        # Force updated_at from clock; re-validate residual archived_* via domain shape.
        validated = ResearchSubject(
            subject_id=subject.subject_id,
            subject_type=subject.subject_type,
            title=subject.title,
            summary=subject.summary,
            status=subject.status,
            primary_instrument_id=subject.primary_instrument_id,
            topic_tags=subject.topic_tags,
            created_at=subject.created_at,
            updated_at=now,
            created_by=subject.created_by,
            archived_at=subject.archived_at,
            archived_reason=subject.archived_reason,
            linked_subject_ids=subject.linked_subject_ids,
            evidence_ids=subject.evidence_ids,
            report_ids=subject.report_ids,
            event_ids=subject.event_ids,
            decision_ids=subject.decision_ids,
            schema_version=subject.schema_version,
        )
        row.subject_type = validated.subject_type.value
        row.title = validated.title
        row.summary = validated.summary
        row.status = validated.status.value
        row.primary_instrument_id = validated.primary_instrument_id
        row.topic_tags_json = validated.topic_tags
        row.updated_at = dt_to_db(validated.updated_at)
        row.archived_at = dt_opt_to_db(validated.archived_at)
        row.archived_reason = validated.archived_reason
        row.linked_subject_ids_json = validated.linked_subject_ids
        row.evidence_ids_json = validated.evidence_ids
        row.report_ids_json = validated.report_ids
        row.event_ids_json = validated.event_ids
        row.decision_ids_json = validated.decision_ids
        row.schema_version = validated.schema_version

    def list_active_primary_thesis_ids(self, subject_id: str) -> tuple[str, ...]:
        stmt = (
            select(ThesisRow.thesis_id)
            .where(ThesisRow.subject_id == subject_id)
            .where(ThesisRow.role == ThesisRole.PRIMARY.value)
            .where(ThesisRow.status == ThesisStatus.ACTIVE.value)
            .order_by(ThesisRow.thesis_id)
        )
        return tuple(self._session.scalars(stmt).all())
