"""SQLAlchemy InvestmentCase repository (session-bound)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from application.ports.clock import Clock
from domain.common.enums import InvestmentCaseStatus, InvestmentCaseType, ThesisRole, ThesisStatus
from domain.common.errors import InvestmentCaseNotFound
from domain.research.models import InvestmentCase
from infrastructure.persistence.orm import InvestmentCaseRow, ThesisRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)


def _to_domain(row: InvestmentCaseRow) -> InvestmentCase:
    return InvestmentCase(
        case_id=row.case_id,
        case_type=InvestmentCaseType(row.case_type),
        title=row.title,
        summary=row.summary,
        status=InvestmentCaseStatus(row.status),
        primary_instrument_id=row.primary_instrument_id,
        topic_tags=tuple(row.topic_tags_json),
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        updated_at=dt_from_db(row.updated_at, field_name="updated_at"),
        created_by=row.created_by,
        archived_at=dt_opt_from_db(row.archived_at, field_name="archived_at"),
        archived_reason=row.archived_reason,
        linked_case_ids=tuple(row.linked_case_ids_json),
        evidence_ids=tuple(row.evidence_ids_json),
        report_ids=tuple(row.report_ids_json),
        event_ids=tuple(row.event_ids_json),
        decision_ids=tuple(row.decision_ids_json),
        schema_version=row.schema_version,
    )


def _to_row(case: InvestmentCase) -> InvestmentCaseRow:
    return InvestmentCaseRow(
        case_id=case.case_id,
        case_type=case.case_type.value,
        title=case.title,
        summary=case.summary,
        status=case.status.value,
        primary_instrument_id=case.primary_instrument_id,
        topic_tags_json=case.topic_tags,
        created_at=dt_to_db(case.created_at),
        updated_at=dt_to_db(case.updated_at),
        created_by=case.created_by,
        archived_at=dt_opt_to_db(case.archived_at),
        archived_reason=case.archived_reason,
        linked_case_ids_json=case.linked_case_ids,
        evidence_ids_json=case.evidence_ids,
        report_ids_json=case.report_ids,
        event_ids_json=case.event_ids,
        decision_ids_json=case.decision_ids,
        schema_version=case.schema_version,
    )


class SqlAlchemyInvestmentCaseRepository:
    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    def get(self, case_id: str) -> InvestmentCase:
        row = self._session.get(InvestmentCaseRow, case_id)
        if row is None:
            raise InvestmentCaseNotFound(
                f"InvestmentCase not found: {case_id}",
                details={"case_id": case_id},
            )
        return _to_domain(row)

    def list(
        self,
        *,
        case_type: InvestmentCaseType | None = None,
        status: InvestmentCaseStatus | None = None,
        primary_instrument_id: str | None = None,
        topic_tag: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[InvestmentCase, ...]:
        stmt = select(InvestmentCaseRow)
        if case_type is not None:
            stmt = stmt.where(InvestmentCaseRow.case_type == case_type.value)
        if status is not None:
            stmt = stmt.where(InvestmentCaseRow.status == status.value)
        if primary_instrument_id is not None:
            stmt = stmt.where(
                InvestmentCaseRow.primary_instrument_id == primary_instrument_id
            )
        if not include_archived and status is None:
            stmt = stmt.where(
                InvestmentCaseRow.status != InvestmentCaseStatus.ARCHIVED.value
            )
        stmt = stmt.order_by(InvestmentCaseRow.updated_at.desc())
        if topic_tag is None:
            stmt = stmt.offset(offset).limit(limit)
            return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

        # Correctness-first: filter the full candidate set in Python, then page.
        items = [_to_domain(row) for row in self._session.scalars(stmt).all()]
        needle = topic_tag.strip().lower()
        filtered = [c for c in items if needle in {t.lower() for t in c.topic_tags}]
        return tuple(filtered[offset : offset + limit])

    def add(self, case: InvestmentCase) -> None:
        self._session.add(_to_row(case))
        self._session.flush()

    def update(self, case: InvestmentCase) -> None:
        row = self._session.get(InvestmentCaseRow, case.case_id, with_for_update=True)
        if row is None:
            raise InvestmentCaseNotFound(
                f"InvestmentCase not found: {case.case_id}",
                details={"case_id": case.case_id},
            )
        now = self._clock.now()
        # Force updated_at from clock; re-validate residual archived_* via domain shape.
        validated = InvestmentCase(
            case_id=case.case_id,
            case_type=case.case_type,
            title=case.title,
            summary=case.summary,
            status=case.status,
            primary_instrument_id=case.primary_instrument_id,
            topic_tags=case.topic_tags,
            created_at=case.created_at,
            updated_at=now,
            created_by=case.created_by,
            archived_at=case.archived_at,
            archived_reason=case.archived_reason,
            linked_case_ids=case.linked_case_ids,
            evidence_ids=case.evidence_ids,
            report_ids=case.report_ids,
            event_ids=case.event_ids,
            decision_ids=case.decision_ids,
            schema_version=case.schema_version,
        )
        row.case_type = validated.case_type.value
        row.title = validated.title
        row.summary = validated.summary
        row.status = validated.status.value
        row.primary_instrument_id = validated.primary_instrument_id
        row.topic_tags_json = validated.topic_tags
        row.updated_at = dt_to_db(validated.updated_at)
        row.archived_at = dt_opt_to_db(validated.archived_at)
        row.archived_reason = validated.archived_reason
        row.linked_case_ids_json = validated.linked_case_ids
        row.evidence_ids_json = validated.evidence_ids
        row.report_ids_json = validated.report_ids
        row.event_ids_json = validated.event_ids
        row.decision_ids_json = validated.decision_ids
        row.schema_version = validated.schema_version

    def list_active_primary_thesis_ids(self, case_id: str) -> tuple[str, ...]:
        stmt = (
            select(ThesisRow.thesis_id)
            .where(ThesisRow.case_id == case_id)
            .where(ThesisRow.role == ThesisRole.PRIMARY.value)
            .where(ThesisRow.status == ThesisStatus.ACTIVE.value)
            .order_by(ThesisRow.thesis_id)
        )
        return tuple(self._session.scalars(stmt).all())
