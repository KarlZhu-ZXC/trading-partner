"""SQLAlchemy append-only Observation review revisions."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.ports.external_note_review_repository import ExternalNoteReviewRepository
from domain.common.errors import (
    ExternalNoteReviewVersionConflict,
    IdempotencyConflict,
    PersistenceError,
)
from domain.external_note.enums import ExternalNoteReviewStatus
from domain.external_note.models import ExternalNoteReview, ExternalNoteReviewDraft
from infrastructure.persistence.orm.operations import (
    ExternalNoteReviewDraftRow,
    ExternalNoteReviewRevisionRow,
)


def _domain(row: ExternalNoteReviewRevisionRow) -> ExternalNoteReview:
    from datetime import datetime

    return ExternalNoteReview(
        review_id=row.review_id,
        note_revision_id=row.note_revision_id,
        note_id=row.note_id,
        version=row.version,
        status=ExternalNoteReviewStatus(row.status),
        subject_id=row.subject_id,
        decision_id=row.decision_id,
        due_at=datetime.fromisoformat(row.due_at) if row.due_at else None,
        actor=row.actor,
        authorization_note=row.authorization_note,
        idempotency_key=row.idempotency_key,
        created_at=datetime.fromisoformat(row.created_at),
    )


def _same_payload(left: ExternalNoteReview, right: ExternalNoteReview) -> bool:
    return (
        left.note_revision_id == right.note_revision_id
        and left.note_id == right.note_id
        and left.status == right.status
        and left.subject_id == right.subject_id
        and left.decision_id == right.decision_id
        and left.due_at == right.due_at
        and left.actor == right.actor
        and left.authorization_note == right.authorization_note
    )


def _draft_domain(row: ExternalNoteReviewDraftRow) -> ExternalNoteReviewDraft:
    from datetime import datetime

    return ExternalNoteReviewDraft(
        draft_id=row.draft_id,
        review_id=row.review_id,
        note_revision_id=row.note_revision_id,
        status=row.status,
        provider=row.provider,
        model=row.model,
        reasoning_effort=row.reasoning_effort,
        schema_version=row.schema_version,
        trigger_codes=row.trigger_codes,
        payload_json=row.payload_json,
        error_code=row.error_code,
        idempotency_key=row.idempotency_key,
        created_at=datetime.fromisoformat(row.created_at),
    )


class SqlAlchemyExternalNoteReviewRepository(ExternalNoteReviewRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(
        self,
        value: ExternalNoteReview,
        *,
        expected_version: int,
    ) -> ExternalNoteReview:
        try:
            with Session(self._engine) as session, session.begin():
                duplicate = session.scalar(
                    select(ExternalNoteReviewRevisionRow).where(
                        ExternalNoteReviewRevisionRow.idempotency_key
                        == value.idempotency_key
                    )
                )
                if duplicate is not None:
                    existing = _domain(duplicate)
                    if not _same_payload(existing, value):
                        raise IdempotencyConflict(
                            "Observation review idempotency key was reused"
                        )
                    return existing
                current_row = session.scalar(
                    select(ExternalNoteReviewRevisionRow)
                    .where(ExternalNoteReviewRevisionRow.review_id == value.review_id)
                    .order_by(ExternalNoteReviewRevisionRow.version.desc())
                    .limit(1)
                )
                current_version = current_row.version if current_row is not None else 0
                if current_row is not None and (
                    current_row.note_revision_id != value.note_revision_id
                    or current_row.note_id != value.note_id
                ):
                    raise ExternalNoteReviewVersionConflict(
                        "Observation review identity cannot change its source revision"
                    )
                if (
                    current_version != expected_version
                    or value.version != current_version + 1
                ):
                    raise ExternalNoteReviewVersionConflict(
                        "Observation review expected version does not match current version",
                        details={
                            "current_version": current_version,
                            "expected_version": expected_version,
                            "requested_version": value.version,
                        },
                    )
                row = ExternalNoteReviewRevisionRow(
                    review_id=value.review_id,
                    version=value.version,
                    note_revision_id=value.note_revision_id,
                    note_id=value.note_id,
                    status=value.status.value,
                    subject_id=value.subject_id,
                    decision_id=value.decision_id,
                    due_at=value.due_at.isoformat() if value.due_at else None,
                    actor=value.actor,
                    authorization_note=value.authorization_note,
                    idempotency_key=value.idempotency_key,
                    created_at=value.created_at.isoformat(),
                )
                session.add(row)
                session.flush()
                return value
        except IntegrityError as exc:
            # A concurrent materializer may win after this transaction's initial
            # duplicate check. Recover only the exact same idempotent payload.
            recovered = self.get_by_idempotency_key(value.idempotency_key)
            if recovered is not None and _same_payload(recovered, value):
                return recovered
            raise PersistenceError("Observation review append conflict") from exc

    def latest(self, review_id: str) -> ExternalNoteReview | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteReviewRevisionRow)
                .where(ExternalNoteReviewRevisionRow.review_id == review_id)
                .order_by(ExternalNoteReviewRevisionRow.version.desc())
                .limit(1)
            )
            return _domain(row) if row is not None else None

    def latest_for_revision(self, note_revision_id: str) -> ExternalNoteReview | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteReviewRevisionRow)
                .where(ExternalNoteReviewRevisionRow.note_revision_id == note_revision_id)
                .order_by(ExternalNoteReviewRevisionRow.version.desc())
                .limit(1)
            )
            return _domain(row) if row is not None else None

    def get_by_idempotency_key(self, idempotency_key: str) -> ExternalNoteReview | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteReviewRevisionRow).where(
                    ExternalNoteReviewRevisionRow.idempotency_key == idempotency_key
                )
            )
            return _domain(row) if row is not None else None

    def list_latest(
        self,
        *,
        statuses: frozenset[ExternalNoteReviewStatus] | None = None,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ExternalNoteReview, ...]:
        latest_versions = (
            select(
                ExternalNoteReviewRevisionRow.review_id.label("review_id"),
                func.max(ExternalNoteReviewRevisionRow.version).label("version"),
            )
            .group_by(ExternalNoteReviewRevisionRow.review_id)
            .subquery()
        )
        with Session(self._engine) as session:
            statement = select(ExternalNoteReviewRevisionRow).join(
                latest_versions,
                (ExternalNoteReviewRevisionRow.review_id == latest_versions.c.review_id)
                & (ExternalNoteReviewRevisionRow.version == latest_versions.c.version),
            )
            if statuses is not None:
                statement = statement.where(
                    ExternalNoteReviewRevisionRow.status.in_(
                        item.value for item in statuses
                    )
                )
            if subject_id is not None:
                statement = statement.where(
                    ExternalNoteReviewRevisionRow.subject_id == subject_id
                )
            statement = statement.order_by(
                ExternalNoteReviewRevisionRow.created_at.desc(),
                ExternalNoteReviewRevisionRow.review_id.desc(),
            ).limit(limit)
            return tuple(_domain(row) for row in session.scalars(statement))

    def append_draft(self, value: ExternalNoteReviewDraft) -> ExternalNoteReviewDraft:
        with Session(self._engine) as session, session.begin():
            duplicate = session.scalar(
                select(ExternalNoteReviewDraftRow).where(
                    ExternalNoteReviewDraftRow.idempotency_key == value.idempotency_key
                )
            )
            if duplicate is not None:
                existing = _draft_domain(duplicate)
                if (
                    existing.review_id != value.review_id
                    or existing.note_revision_id != value.note_revision_id
                    or existing.model != value.model
                    or existing.trigger_codes != value.trigger_codes
                ):
                    raise IdempotencyConflict(
                        "Observation review draft idempotency key was reused"
                    )
                return existing
            session.add(
                ExternalNoteReviewDraftRow(
                    draft_id=value.draft_id,
                    review_id=value.review_id,
                    note_revision_id=value.note_revision_id,
                    status=value.status,
                    provider=value.provider,
                    model=value.model,
                    reasoning_effort=value.reasoning_effort,
                    schema_version=value.schema_version,
                    trigger_codes=value.trigger_codes,
                    payload_json=value.payload_json,
                    error_code=value.error_code,
                    idempotency_key=value.idempotency_key,
                    created_at=value.created_at.isoformat(),
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise PersistenceError(
                    "Observation review draft append conflict"
                ) from exc
            return value

    def latest_draft(self, note_revision_id: str) -> ExternalNoteReviewDraft | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteReviewDraftRow)
                .where(
                    ExternalNoteReviewDraftRow.note_revision_id == note_revision_id
                )
                .order_by(
                    ExternalNoteReviewDraftRow.created_at.desc(),
                    ExternalNoteReviewDraftRow.draft_id.desc(),
                )
                .limit(1)
            )
            return _draft_domain(row) if row is not None else None

    def latest_successful_draft(
        self, note_revision_id: str
    ) -> ExternalNoteReviewDraft | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ExternalNoteReviewDraftRow)
                .where(
                    ExternalNoteReviewDraftRow.note_revision_id == note_revision_id,
                    ExternalNoteReviewDraftRow.status == "SUCCEEDED",
                )
                .order_by(
                    ExternalNoteReviewDraftRow.created_at.desc(),
                    ExternalNoteReviewDraftRow.draft_id.desc(),
                )
                .limit(1)
            )
            return _draft_domain(row) if row is not None else None
