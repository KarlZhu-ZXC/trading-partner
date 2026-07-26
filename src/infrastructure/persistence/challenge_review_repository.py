"""SQLAlchemy Challenge Review repository with one-time resolution."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.challenge.enums import (
    ChallengeDimension,
    ChallengeFindingSeverity,
    ChallengeResolution,
    ChallengeReviewStatus,
    ChallengeTrigger,
)
from domain.challenge.models import ChallengeFinding, ChallengeQuestion, ChallengeReview
from domain.common.enums import ConfirmationMode
from domain.common.errors import (
    ChallengeReviewAlreadyResolved,
    ChallengeReviewNotFound,
    IdempotencyConflict,
)
from infrastructure.persistence.models import (
    ChallengeFindingRow,
    ChallengeQuestionRow,
    ChallengeReviewResolutionRow,
    ChallengeReviewRow,
)


class _RowCountResult(Protocol):
    rowcount: int


class SqlAlchemyChallengeReviewRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(
        self,
        review: ChallengeReview,
        *,
        idempotency_key: str,
        payload_sha256: str,
    ) -> ChallengeReview:
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    ChallengeReviewRow(
                        review_id=review.review_id,
                        case_id=review.case_id,
                        mode=review.mode.value,
                        trigger=review.trigger.value,
                        proposed_action=review.proposed_action,
                        related_candidate_id=review.related_candidate_id,
                        related_evidence_ids_json=json.dumps(review.related_evidence_ids),
                        position_context_snapshot_id=review.position_context_snapshot_id,
                        context_as_of=review.context_as_of.isoformat(),
                        status=review.status.value,
                        created_at=review.created_at.isoformat(),
                        resolution=None,
                        resolution_rationale=None,
                        resolved_at=None,
                        confirmed_by=None,
                        start_idempotency_key=idempotency_key,
                        start_payload_sha256=payload_sha256,
                    )
                )
                # Relationships are intentionally absent from persistence rows; make
                # the parent visible before child inserts when SQLite FKs are enabled.
                session.flush()
                session.add_all(
                    [
                        ChallengeQuestionRow(
                            question_id=item.question_id,
                            review_id=item.review_id,
                            dimension=item.dimension.value,
                            prompt=item.prompt,
                            ordinal=item.ordinal,
                            question_set_version=item.question_set_version,
                        )
                        for item in review.questions
                    ]
                )
                session.add_all(
                    [
                        ChallengeFindingRow(
                            finding_id=item.finding_id,
                            review_id=item.review_id,
                            dimension=item.dimension.value,
                            severity=item.severity.value,
                            summary=item.summary,
                            evidence_ids_json=json.dumps(item.evidence_ids),
                        )
                        for item in review.findings
                    ]
                )
        except IntegrityError as exc:
            raise IdempotencyConflict(
                "Challenge Review start idempotency key was reused"
            ) from exc
        return review

    def get_by_start_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ChallengeReview, str] | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ChallengeReviewRow).where(
                    ChallengeReviewRow.start_idempotency_key == idempotency_key
                )
            )
            if row is None or row.start_payload_sha256 is None:
                return None
            return self._hydrate(session, row), row.start_payload_sha256

    def get_by_resolution_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ChallengeReview, str] | None:
        with Session(self._engine) as session:
            resolution = session.scalar(
                select(ChallengeReviewResolutionRow).where(
                    ChallengeReviewResolutionRow.idempotency_key == idempotency_key
                )
            )
            if resolution is None:
                return None
            row = session.get(ChallengeReviewRow, resolution.review_id)
            if row is None:
                raise ChallengeReviewNotFound("Challenge Review was not found")
            return self._hydrate(session, row), resolution.payload_sha256

    def get(self, review_id: str) -> ChallengeReview:
        with Session(self._engine) as session:
            row = session.get(ChallengeReviewRow, review_id)
            if row is None:
                raise ChallengeReviewNotFound("Challenge Review was not found")
            return self._hydrate(session, row)

    def resolve(
        self,
        review_id: str,
        *,
        resolution: ChallengeResolution,
        rationale: str,
        confirmed_by: str,
        resolved_at: datetime,
        resolution_id: str,
        idempotency_key: str,
        payload_sha256: str,
    ) -> ChallengeReview:
        try:
            with Session(self._engine) as session, session.begin():
                prior = session.scalar(
                    select(ChallengeReviewResolutionRow).where(
                        ChallengeReviewResolutionRow.idempotency_key == idempotency_key
                    )
                )
                if prior is not None:
                    if prior.payload_sha256 != payload_sha256:
                        raise IdempotencyConflict(
                            "Challenge Review resolution idempotency key was reused"
                        )
                    row = session.get(ChallengeReviewRow, prior.review_id)
                    if row is None:
                        raise ChallengeReviewNotFound("Challenge Review was not found")
                    return self._hydrate(session, row)
                result = session.execute(
                    update(ChallengeReviewRow)
                    .where(
                        ChallengeReviewRow.review_id == review_id,
                        ChallengeReviewRow.status == ChallengeReviewStatus.OPEN.value,
                    )
                    .values(
                        status=ChallengeReviewStatus.RESOLVED.value,
                        resolution=resolution.value,
                        resolution_rationale=rationale,
                        resolved_at=resolved_at.isoformat(),
                        confirmed_by=confirmed_by,
                    )
                )
                changed = cast(_RowCountResult, result).rowcount
                if changed != 1:
                    existing = session.get(ChallengeReviewRow, review_id)
                    if existing is None:
                        raise ChallengeReviewNotFound("Challenge Review was not found")
                    raise ChallengeReviewAlreadyResolved(
                        "Challenge Review is already resolved"
                    )
                session.add(
                    ChallengeReviewResolutionRow(
                        resolution_id=resolution_id,
                        review_id=review_id,
                        idempotency_key=idempotency_key,
                        payload_sha256=payload_sha256,
                        resolution=resolution.value,
                        rationale=rationale,
                        confirmed_by=confirmed_by,
                        resolved_at=resolved_at.isoformat(),
                    )
                )
        except IntegrityError as exc:
            raise IdempotencyConflict(
                "Challenge Review resolution idempotency key was reused"
            ) from exc
        current = self.get(review_id)
        return replace(current, execution_effect=False)

    @staticmethod
    def _hydrate(session: Session, row: ChallengeReviewRow) -> ChallengeReview:
        questions = session.scalars(
            select(ChallengeQuestionRow)
            .where(ChallengeQuestionRow.review_id == row.review_id)
            .order_by(ChallengeQuestionRow.ordinal)
        )
        findings = session.scalars(
            select(ChallengeFindingRow)
            .where(ChallengeFindingRow.review_id == row.review_id)
            .order_by(ChallengeFindingRow.finding_id)
        )
        persisted_resolution = session.scalar(
            select(ChallengeReviewResolutionRow).where(
                ChallengeReviewResolutionRow.review_id == row.review_id
            )
        )
        resolution = (
            persisted_resolution.resolution
            if persisted_resolution is not None
            else row.resolution
        )
        rationale = (
            persisted_resolution.rationale
            if persisted_resolution is not None
            else row.resolution_rationale
        )
        resolved_at = (
            persisted_resolution.resolved_at
            if persisted_resolution is not None
            else row.resolved_at
        )
        confirmed_by = (
            persisted_resolution.confirmed_by
            if persisted_resolution is not None
            else row.confirmed_by
        )
        return ChallengeReview(
            review_id=row.review_id,
            case_id=row.case_id,
            mode=ConfirmationMode(row.mode),
            trigger=ChallengeTrigger(row.trigger),
            proposed_action=row.proposed_action,
            related_candidate_id=row.related_candidate_id,
            related_evidence_ids=tuple(json.loads(row.related_evidence_ids_json)),
            position_context_snapshot_id=row.position_context_snapshot_id,
            context_as_of=datetime.fromisoformat(row.context_as_of),
            status=ChallengeReviewStatus(row.status),
            questions=tuple(
                ChallengeQuestion(
                    item.question_id,
                    item.review_id,
                    ChallengeDimension(item.dimension),
                    item.prompt,
                    item.ordinal,
                    item.question_set_version,
                )
                for item in questions
            ),
            findings=tuple(
                ChallengeFinding(
                    item.finding_id,
                    item.review_id,
                    ChallengeDimension(item.dimension),
                    ChallengeFindingSeverity(item.severity),
                    item.summary,
                    tuple(json.loads(item.evidence_ids_json)),
                )
                for item in findings
            ),
            created_at=datetime.fromisoformat(row.created_at),
            resolution=ChallengeResolution(resolution) if resolution else None,
            resolution_rationale=rationale,
            resolved_at=datetime.fromisoformat(resolved_at) if resolved_at else None,
            confirmed_by=confirmed_by,
            execution_effect=False,
        )
