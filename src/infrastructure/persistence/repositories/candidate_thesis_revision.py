"""SQLAlchemy CandidateThesisRevision repository (session-bound)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapper, Session

from domain.common.enums import CandidateKind, CandidateStatus, ConfirmationMode
from domain.common.errors import (
    AppendOnlyViolation,
    CandidateAlreadyResolved,
    CandidateNotFound,
    DataContractError,
)
from domain.research.models import CandidateThesisRevision
from infrastructure.persistence.models import CandidateThesisRevisionRow
from infrastructure.persistence.repositories._mapping import (
    dt_from_db,
    dt_opt_from_db,
    dt_opt_to_db,
    dt_to_db,
)

_TERMINAL_STATUSES = frozenset(
    {
        CandidateStatus.CONFIRMED,
        CandidateStatus.REJECTED,
        CandidateStatus.WITHDRAWN,
        CandidateStatus.EXPIRED,
    }
)

_PAYLOAD_LISTENERS_REGISTERED = False


def _to_domain(row: CandidateThesisRevisionRow) -> CandidateThesisRevision:
    return CandidateThesisRevision(
        candidate_id=row.candidate_id,
        case_id=row.case_id,
        thesis_id=row.thesis_id,
        target_revision_no=row.target_revision_no,
        payload_json=row.payload_json,
        kind=CandidateKind(row.kind),
        confirmation_mode=ConfirmationMode(row.confirmation_mode),
        status=CandidateStatus(row.status),
        proposed_at=dt_from_db(row.proposed_at, field_name="proposed_at"),
        expires_at=dt_from_db(row.expires_at, field_name="expires_at"),
        proposed_by=row.proposed_by,
        proposed_by_rationale=row.proposed_by_rationale,
        reviewed_at=dt_opt_from_db(row.reviewed_at, field_name="reviewed_at"),
        reviewed_by=row.reviewed_by,
        review_note=row.review_note,
        rejection_reason=row.rejection_reason,
        idempotency_key=row.idempotency_key,
    )


def _to_row(candidate: CandidateThesisRevision) -> CandidateThesisRevisionRow:
    return CandidateThesisRevisionRow(
        candidate_id=candidate.candidate_id,
        case_id=candidate.case_id,
        thesis_id=candidate.thesis_id,
        target_revision_no=candidate.target_revision_no,
        payload_json=candidate.payload_json,
        kind=candidate.kind.value,
        confirmation_mode=candidate.confirmation_mode.value,
        status=candidate.status.value,
        proposed_at=dt_to_db(candidate.proposed_at),
        expires_at=dt_to_db(candidate.expires_at),
        proposed_by=candidate.proposed_by,
        proposed_by_rationale=candidate.proposed_by_rationale,
        reviewed_at=dt_opt_to_db(candidate.reviewed_at),
        reviewed_by=candidate.reviewed_by,
        review_note=candidate.review_note,
        rejection_reason=candidate.rejection_reason,
        idempotency_key=candidate.idempotency_key,
    )


def _deny_payload_mutation_after_proposed(
    mapper: Mapper[object],
    connection: object,
    target: CandidateThesisRevisionRow,
) -> None:
    """INV-15: payload_json is immutable once status leaves PROPOSED.

    Status and review fields may still update; only payload_json is protected.
    """
    state = sa_inspect(target)
    hist = state.attrs.payload_json.history
    if not hist.has_changes():
        return
    # Original (DB) status: once left PROPOSED, payload is frozen forever.
    original_status = state.committed_state.get("status", target.status)
    if original_status != CandidateStatus.PROPOSED.value:
        raise AppendOnlyViolation(
            "candidate payload_json is immutable after leaving PROPOSED",
            details={
                "candidate_id": target.candidate_id,
                "status": original_status,
            },
        )
    # Same-flush transition away from PROPOSED with payload edit is also forbidden.
    if target.status != CandidateStatus.PROPOSED.value:
        raise AppendOnlyViolation(
            "candidate payload_json cannot change while leaving PROPOSED",
            details={
                "candidate_id": target.candidate_id,
                "status": target.status,
            },
        )


def register_candidate_payload_listeners() -> None:
    """Idempotently register payload immutability guard."""
    global _PAYLOAD_LISTENERS_REGISTERED
    if _PAYLOAD_LISTENERS_REGISTERED:
        return
    event.listen(
        CandidateThesisRevisionRow, "before_update", _deny_payload_mutation_after_proposed
    )
    _PAYLOAD_LISTENERS_REGISTERED = True


register_candidate_payload_listeners()


class SqlAlchemyCandidateThesisRevisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        register_candidate_payload_listeners()

    def get(self, candidate_id: str) -> CandidateThesisRevision:
        row = self._session.get(CandidateThesisRevisionRow, candidate_id)
        if row is None:
            raise CandidateNotFound(
                f"CandidateThesisRevision not found: {candidate_id}",
                details={"candidate_id": candidate_id},
            )
        return _to_domain(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> CandidateThesisRevision | None:
        stmt = select(CandidateThesisRevisionRow).where(
            CandidateThesisRevisionRow.idempotency_key == idempotency_key
        )
        row = self._session.scalars(stmt).first()
        if row is None:
            return None
        return _to_domain(row)

    def list(
        self,
        *,
        case_id: str | None = None,
        kind: CandidateKind | None = None,
        status: CandidateStatus | None = None,
        confirmation_mode: ConfirmationMode | None = None,
        proposed_by: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[CandidateThesisRevision, ...]:
        stmt = select(CandidateThesisRevisionRow)
        if case_id is not None:
            stmt = stmt.where(CandidateThesisRevisionRow.case_id == case_id)
        if kind is not None:
            stmt = stmt.where(CandidateThesisRevisionRow.kind == kind.value)
        if status is not None:
            stmt = stmt.where(CandidateThesisRevisionRow.status == status.value)
        if confirmation_mode is not None:
            stmt = stmt.where(
                CandidateThesisRevisionRow.confirmation_mode == confirmation_mode.value
            )
        if proposed_by is not None:
            stmt = stmt.where(CandidateThesisRevisionRow.proposed_by == proposed_by)
        stmt = (
            stmt.order_by(
                CandidateThesisRevisionRow.proposed_at.desc(),
                CandidateThesisRevisionRow.candidate_id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return tuple(_to_domain(row) for row in self._session.scalars(stmt).all())

    def add(self, candidate: CandidateThesisRevision) -> None:
        self._session.add(_to_row(candidate))
        self._session.flush()

    def update_status(
        self,
        candidate_id: str,
        *,
        new_status: CandidateStatus,
        reviewed_at: datetime | None,
        reviewed_by: str | None,
        review_note: str | None,
        rejection_reason: str | None,
    ) -> None:
        """Update lifecycle fields only — payload_json is never modified (INV-15)."""
        row = self._session.get(
            CandidateThesisRevisionRow, candidate_id, with_for_update=True
        )
        if row is None:
            raise CandidateNotFound(
                f"CandidateThesisRevision not found: {candidate_id}",
                details={"candidate_id": candidate_id},
            )
        current = CandidateStatus(row.status)
        if current in _TERMINAL_STATUSES:
            raise CandidateAlreadyResolved(
                f"Candidate already resolved with status={current.value}",
                details={"candidate_id": candidate_id, "status": current.value},
            )
        if current is not CandidateStatus.PROPOSED:
            raise DataContractError(
                "Only PROPOSED candidates can transition status",
                details={"candidate_id": candidate_id, "status": current.value},
            )
        # Residual fields for non-terminal / non-matching statuses are cleared
        # to match domain + SQL CHECK equality rules.
        effective_rejection = (
            rejection_reason if new_status is CandidateStatus.REJECTED else None
        )
        effective_review_note = review_note
        if new_status in {CandidateStatus.PROPOSED, CandidateStatus.EXPIRED}:
            effective_review_note = None
            reviewed_at = None
            reviewed_by = None
        current_domain = _to_domain(row)
        next_domain = CandidateThesisRevision(
            candidate_id=current_domain.candidate_id,
            case_id=current_domain.case_id,
            thesis_id=current_domain.thesis_id,
            target_revision_no=current_domain.target_revision_no,
            payload_json=current_domain.payload_json,
            kind=current_domain.kind,
            confirmation_mode=current_domain.confirmation_mode,
            status=new_status,
            proposed_at=current_domain.proposed_at,
            expires_at=current_domain.expires_at,
            proposed_by=current_domain.proposed_by,
            proposed_by_rationale=current_domain.proposed_by_rationale,
            reviewed_at=reviewed_at,
            reviewed_by=reviewed_by,
            review_note=effective_review_note,
            rejection_reason=effective_rejection,
            idempotency_key=current_domain.idempotency_key,
        )
        # payload_json intentionally untouched
        row.status = next_domain.status.value
        row.reviewed_at = dt_opt_to_db(next_domain.reviewed_at)
        row.reviewed_by = next_domain.reviewed_by
        row.review_note = next_domain.review_note
        row.rejection_reason = next_domain.rejection_reason

    def expire_due(self, *, now: datetime, limit: int = 200) -> tuple[str, ...]:
        now_iso = dt_to_db(now)
        stmt = (
            select(CandidateThesisRevisionRow)
            .where(CandidateThesisRevisionRow.status == CandidateStatus.PROPOSED.value)
            .where(CandidateThesisRevisionRow.expires_at < now_iso)
            .order_by(CandidateThesisRevisionRow.expires_at.asc())
            .limit(limit)
            .with_for_update()
        )
        rows = list(self._session.scalars(stmt).all())
        expired_ids: list[str] = []
        for row in rows:
            current_domain = _to_domain(row)
            next_domain = CandidateThesisRevision(
                candidate_id=current_domain.candidate_id,
                case_id=current_domain.case_id,
                thesis_id=current_domain.thesis_id,
                target_revision_no=current_domain.target_revision_no,
                payload_json=current_domain.payload_json,
                kind=current_domain.kind,
                confirmation_mode=current_domain.confirmation_mode,
                status=CandidateStatus.EXPIRED,
                proposed_at=current_domain.proposed_at,
                expires_at=current_domain.expires_at,
                proposed_by=current_domain.proposed_by,
                proposed_by_rationale=current_domain.proposed_by_rationale,
                reviewed_at=None,
                reviewed_by=None,
                review_note=None,
                rejection_reason=None,
                idempotency_key=current_domain.idempotency_key,
            )
            row.status = next_domain.status.value
            row.reviewed_at = None
            row.reviewed_by = None
            row.review_note = None
            row.rejection_reason = None
            expired_ids.append(row.candidate_id)
        return tuple(expired_ids)
