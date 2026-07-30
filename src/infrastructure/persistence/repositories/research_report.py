"""SQLAlchemy ResearchReport repository (append-only, session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import ResearchReportType
from domain.common.errors import InvalidResearchLink, ResearchMemoryNotFound
from domain.research.models import ResearchReport
from infrastructure.persistence.orm import ResearchReportRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    require_case_exists,
    require_evidence_ids_linked_and_visible,
    require_same_case_supersedes,
    require_thesis_revision_ids_visible,
    require_visible_not_after,
)


def _to_domain(row: ResearchReportRow) -> ResearchReport:
    return ResearchReport(
        report_id=row.report_id,
        case_id=row.case_id,
        report_type=ResearchReportType(row.report_type),
        title=row.title,
        summary=row.summary,
        content_markdown=row.content_markdown,
        as_of=dt_from_db(row.as_of, field_name="as_of"),
        created_at=dt_from_db(row.created_at, field_name="created_at"),
        created_by=row.created_by,
        research_run_id=row.research_run_id,
        evidence_ids=tuple(row.evidence_ids_json),
        thesis_revision_ids=tuple(row.thesis_revision_ids_json),
        supersedes_report_id=row.supersedes_report_id,
        content_sha256=row.content_sha256,
        model_name=row.model_name,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
    )


def _to_row(report: ResearchReport) -> ResearchReportRow:
    return ResearchReportRow(
        report_id=report.report_id,
        case_id=report.case_id,
        report_type=report.report_type.value,
        title=report.title,
        summary=report.summary,
        content_markdown=report.content_markdown,
        as_of=dt_to_db(report.as_of),
        created_at=dt_to_db(report.created_at),
        created_by=report.created_by,
        research_run_id=report.research_run_id,
        evidence_ids_json=report.evidence_ids,
        thesis_revision_ids_json=report.thesis_revision_ids,
        supersedes_report_id=report.supersedes_report_id,
        content_sha256=report.content_sha256,
        model_name=report.model_name,
        prompt_version=report.prompt_version,
        schema_version=report.schema_version,
    )


class SqlAlchemyResearchReportRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, report: ResearchReport) -> None:
        require_case_exists(self._session, report.case_id)
        # Report hindsight: observed_at <= as_of; membership/link <= created_at;
        # thesis revision confirmed_at <= as_of (not merely created_at).
        require_evidence_ids_linked_and_visible(
            self._session,
            case_id=report.case_id,
            evidence_ids=report.evidence_ids,
            observed_at_not_after=report.as_of,
            linked_at_not_after=report.created_at,
        )
        require_thesis_revision_ids_visible(
            self._session,
            case_id=report.case_id,
            thesis_revision_ids=report.thesis_revision_ids,
            visible_at=report.as_of,
        )
        if report.supersedes_report_id is not None:
            old = self._session.get(ResearchReportRow, report.supersedes_report_id)
            if old is None:
                raise InvalidResearchLink(
                    "superseded report does not exist",
                    details={
                        "entity_type": "report",
                        "supersedes_report_id": report.supersedes_report_id,
                    },
                )
            require_same_case_supersedes(
                new_case_id=report.case_id,
                old_case_id=old.case_id,
                entity_type="report",
                supersedes_id=report.supersedes_report_id,
            )
            require_visible_not_after(
                old_visible_at=dt_from_db(old.created_at, field_name="created_at"),
                new_visible_at=report.created_at,
                entity_type="report",
                supersedes_id=report.supersedes_report_id,
            )
        self._session.add(_to_row(report))
        self._session.flush()

    def get(self, report_id: str) -> ResearchReport:
        row = self._session.get(ResearchReportRow, report_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "ResearchReport not found",
                details={"entity_type": "report", "report_id": report_id},
            )
        return _to_domain(row)

    def get_by_content_sha256(self, content_sha256: str) -> ResearchReport | None:
        stmt = select(ResearchReportRow).where(
            ResearchReportRow.content_sha256 == content_sha256
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            return None
        return _to_domain(row)

    def list_by_case(
        self, case_id: str, *, as_of: datetime | None = None
    ) -> tuple[ResearchReport, ...]:
        stmt = select(ResearchReportRow).where(ResearchReportRow.case_id == case_id)
        if as_of is not None:
            as_of_text = dt_to_db(as_of)
            stmt = stmt.where(
                ResearchReportRow.created_at <= as_of_text,
                ResearchReportRow.as_of <= as_of_text,
            )
        stmt = stmt.order_by(
            ResearchReportRow.created_at.desc(),
            ResearchReportRow.report_id.asc(),
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())
