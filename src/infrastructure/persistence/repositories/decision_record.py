"""SQLAlchemy DecisionRecord repository (append-only, session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.common.enums import ConfirmationMode, DecisionType
from domain.common.errors import InvalidResearchLink, ResearchMemoryNotFound
from domain.research.models import DecisionRecord
from infrastructure.persistence.models import DecisionRecordRow
from infrastructure.persistence.repositories import append_only as _append_only  # noqa: F401
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_to_db,
)
from infrastructure.persistence.repositories._research_memory_validation import (
    require_case_exists,
    require_evidence_ids_linked_and_visible,
    require_idempotency_storage,
    require_instruments_exist,
    require_report_ids_visible,
    require_same_case_supersedes,
    require_thesis_revision_ids_visible,
    require_visible_not_after,
)


def _to_domain(row: DecisionRecordRow) -> DecisionRecord:
    return DecisionRecord(
        decision_id=row.decision_id,
        case_id=row.case_id,
        decision_type=DecisionType(row.decision_type),
        title=row.title,
        rationale=row.rationale,
        decided_at=dt_from_db(row.decided_at, field_name="decided_at"),
        recorded_at=dt_from_db(row.recorded_at, field_name="recorded_at"),
        decided_by=row.decided_by,
        confirmation_mode=ConfirmationMode(row.confirmation_mode),
        primary_instrument_id=row.primary_instrument_id,
        thesis_revision_ids=tuple(row.thesis_revision_ids_json),
        evidence_ids=tuple(row.evidence_ids_json),
        report_ids=tuple(row.report_ids_json),
        supersedes_decision_id=row.supersedes_decision_id,
        position_context_snapshot_id=row.position_context_snapshot_id,
        schema_version=row.schema_version,
    )


def _to_row(
    decision: DecisionRecord,
    *,
    idempotency_key: str,
    idempotency_payload_sha256: str,
) -> DecisionRecordRow:
    return DecisionRecordRow(
        decision_id=decision.decision_id,
        case_id=decision.case_id,
        decision_type=decision.decision_type.value,
        title=decision.title,
        rationale=decision.rationale,
        decided_at=dt_to_db(decision.decided_at),
        recorded_at=dt_to_db(decision.recorded_at),
        decided_by=decision.decided_by,
        confirmation_mode=decision.confirmation_mode.value,
        primary_instrument_id=decision.primary_instrument_id,
        thesis_revision_ids_json=decision.thesis_revision_ids,
        evidence_ids_json=decision.evidence_ids,
        report_ids_json=decision.report_ids,
        supersedes_decision_id=decision.supersedes_decision_id,
        position_context_snapshot_id=decision.position_context_snapshot_id,
        idempotency_key=idempotency_key,
        idempotency_payload_sha256=idempotency_payload_sha256,
        schema_version=decision.schema_version,
    )


class SqlAlchemyDecisionRecordRepository:
    """Append-only repository: no update/delete methods by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        decision: DecisionRecord,
        *,
        idempotency_key: str,
        idempotency_payload_sha256: str,
    ) -> None:
        require_idempotency_storage(
            idempotency_key=idempotency_key,
            idempotency_payload_sha256=idempotency_payload_sha256,
        )
        require_case_exists(self._session, decision.case_id)
        if decision.primary_instrument_id is not None:
            require_instruments_exist(
                self._session, (decision.primary_instrument_id,)
            )
        # position_context_snapshot_id: domain shape only (Phase 1I not landed)
        # Decision visible_at is recorded_at for both evidence observed_at and link.
        require_evidence_ids_linked_and_visible(
            self._session,
            case_id=decision.case_id,
            evidence_ids=decision.evidence_ids,
            observed_at_not_after=decision.recorded_at,
            linked_at_not_after=decision.recorded_at,
        )
        require_report_ids_visible(
            self._session,
            case_id=decision.case_id,
            report_ids=decision.report_ids,
            visible_at=decision.recorded_at,
        )
        require_thesis_revision_ids_visible(
            self._session,
            case_id=decision.case_id,
            thesis_revision_ids=decision.thesis_revision_ids,
            visible_at=decision.recorded_at,
        )
        if decision.supersedes_decision_id is not None:
            old = self._session.get(
                DecisionRecordRow, decision.supersedes_decision_id
            )
            if old is None:
                raise InvalidResearchLink(
                    "superseded decision does not exist",
                    details={
                        "entity_type": "decision",
                        "supersedes_decision_id": decision.supersedes_decision_id,
                    },
                )
            require_same_case_supersedes(
                new_case_id=decision.case_id,
                old_case_id=old.case_id,
                entity_type="decision",
                supersedes_id=decision.supersedes_decision_id,
            )
            require_visible_not_after(
                old_visible_at=dt_from_db(old.recorded_at, field_name="recorded_at"),
                new_visible_at=decision.recorded_at,
                entity_type="decision",
                supersedes_id=decision.supersedes_decision_id,
            )
        self._session.add(
            _to_row(
                decision,
                idempotency_key=idempotency_key,
                idempotency_payload_sha256=idempotency_payload_sha256,
            )
        )
        self._session.flush()

    def get(self, decision_id: str) -> DecisionRecord:
        row = self._session.get(DecisionRecordRow, decision_id)
        if row is None:
            raise ResearchMemoryNotFound(
                "DecisionRecord not found",
                details={"entity_type": "decision", "decision_id": decision_id},
            )
        return _to_domain(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> DecisionRecord | None:
        stmt = select(DecisionRecordRow).where(
            DecisionRecordRow.idempotency_key == idempotency_key
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            return None
        return _to_domain(row)

    def list_by_case(
        self, case_id: str, *, as_of: datetime | None = None
    ) -> tuple[DecisionRecord, ...]:
        stmt = select(DecisionRecordRow).where(DecisionRecordRow.case_id == case_id)
        if as_of is not None:
            stmt = stmt.where(DecisionRecordRow.recorded_at <= dt_to_db(as_of))
        stmt = stmt.order_by(
            DecisionRecordRow.recorded_at.desc(),
            DecisionRecordRow.decision_id.asc(),
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())
