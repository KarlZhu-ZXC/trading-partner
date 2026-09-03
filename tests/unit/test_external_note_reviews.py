from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from application.dto.external_note_review import ExternalNoteReviewTransitionInput
from application.services.external_note_review_service import ExternalNoteReviewService
from domain.common.errors import (
    ExternalNoteReviewVersionConflict,
    IdempotencyConflict,
    InvalidExternalNoteReviewTransition,
    InvalidResearchLink,
    PersistenceError,
)
from domain.common.ids import EntityIdPrefix
from domain.external_note.enums import ExternalNoteReviewStatus, NoteCoverage
from domain.external_note.models import (
    ExternalNoteIdentity,
    ExternalNoteInterpretation,
    ExternalNoteReview,
    ExternalNoteRevision,
)
from infrastructure.persistence.external_note_repository import (
    SqlAlchemyExternalNoteRepository,
)
from infrastructure.persistence.external_note_review_repository import (
    SqlAlchemyExternalNoteReviewRepository,
)

NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)
REVISION_ID = "external_note_revision_00000000-0000-7000-8000-000000000001"
NOTE_ID = "external_note_00000000-0000-7000-8000-000000000001"
SUBJECT_ID = "case_00000000-0000-7000-8000-000000000001"
DECISION_ID = "decision_00000000-0000-7000-8000-000000000001"


def _revision(*, coverage: NoteCoverage = NoteCoverage.FULL) -> ExternalNoteRevision:
    return ExternalNoteRevision(
        note_revision_id=REVISION_ID,
        note_id=NOTE_ID,
        version=1,
        content_sha256="a" * 64,
        source_revision_key="source:" + "b" * 64,
        title="NVDA",
        summary="Updated view",
        full_body="User view changed" if coverage is NoteCoverage.FULL else None,
        coverage=coverage,
        source_timestamp=NOW,
        observed_at=NOW,
        visibility="PRIVATE",
        related_provider_stock_ids=(),
        related_provider_codes=("NASDAQ:NVDA",),
        blocks=(),
    )


class _Notes:
    def __init__(self, revision: ExternalNoteRevision, *, succeeded: bool = True) -> None:
        self.revision = revision
        self.interpretation = ExternalNoteInterpretation(
            interpretation_id="external_note_interpretation_test",
            note_revision_id=revision.note_revision_id,
            status="SUCCEEDED" if succeeded else "FAILED",
            provider="test",
            model="test",
            reasoning_effort="max",
            schema_version="test-v1",
            payload_json="{}",
            error_code=None if succeeded else "TEST_FAILURE",
            created_at=NOW,
        )

    def revision_by_id(self, note_revision_id: str) -> ExternalNoteRevision | None:
        return self.revision if note_revision_id == self.revision.note_revision_id else None

    def interpretation_for_revision(
        self, note_revision_id: str
    ) -> ExternalNoteInterpretation | None:
        return self.interpretation if note_revision_id == self.revision.note_revision_id else None


class _Reviews:
    def __init__(self) -> None:
        self.values: list[ExternalNoteReview] = []

    def append(
        self, value: ExternalNoteReview, *, expected_version: int
    ) -> ExternalNoteReview:
        duplicate = next(
            (item for item in self.values if item.idempotency_key == value.idempotency_key),
            None,
        )
        if duplicate is not None:
            return duplicate
        current = self.latest(value.review_id)
        version = current.version if current else 0
        if version != expected_version:
            raise ExternalNoteReviewVersionConflict("version mismatch")
        self.values.append(value)
        return value

    def latest(self, review_id: str) -> ExternalNoteReview | None:
        values = [item for item in self.values if item.review_id == review_id]
        return max(values, key=lambda item: item.version) if values else None

    def latest_for_revision(self, note_revision_id: str) -> ExternalNoteReview | None:
        values = [item for item in self.values if item.note_revision_id == note_revision_id]
        return max(values, key=lambda item: item.version) if values else None

    def list_latest(self, **_: object) -> tuple[ExternalNoteReview, ...]:
        latest = self.latest_for_revision(REVISION_ID)
        return (latest,) if latest is not None else ()


class _Uow:
    def __init__(self, *, decision_revision_id: str = REVISION_ID) -> None:
        self.subjects = SimpleNamespace(
            get=lambda subject_id: SimpleNamespace(subject_id=subject_id)
        )
        self.decisions = SimpleNamespace(
            get=lambda decision_id: SimpleNamespace(
                decision_id=decision_id,
                subject_id=SUBJECT_ID,
                external_note_revision_id=decision_revision_id,
            )
        )

    def __enter__(self) -> _Uow:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _service(fixed_clock, id_generator, *, notes: _Notes | None = None, uow=None):
    reviews = _Reviews()
    service = ExternalNoteReviewService(
        reviews,  # type: ignore[arg-type]
        notes or _Notes(_revision()),  # type: ignore[arg-type]
        uow or (lambda: _Uow()),  # type: ignore[arg-type]
        fixed_clock,
        id_generator,
    )
    return service, reviews


def test_pending_review_is_idempotent_and_requires_full_successful_interpretation(
    fixed_clock, id_generator
) -> None:
    fixed_clock.set(NOW)
    service, reviews = _service(fixed_clock, id_generator)

    first = service.ensure_pending(note_revision_id=REVISION_ID)
    mapped = service.ensure_pending(note_revision_id=REVISION_ID, subject_id=SUBJECT_ID)
    repeated = service.ensure_pending(note_revision_id=REVISION_ID, subject_id=SUBJECT_ID)

    assert first.status == "PENDING"
    assert first.subject_id is None
    assert mapped == repeated
    assert first.status == "PENDING"
    assert mapped.subject_id == SUBJECT_ID
    assert len(reviews.values) == 2
    projection = service.pending_projections()[0]
    assert projection.source_type.value == "OBSERVATION_REVIEW_DUE"
    assert projection.source_ref == REVISION_ID
    assert projection.subject_id == SUBJECT_ID

    summary_service, _ = _service(
        fixed_clock,
        id_generator,
        notes=_Notes(_revision(coverage=NoteCoverage.SUMMARY_ONLY)),
    )
    with pytest.raises(InvalidExternalNoteReviewTransition):
        summary_service.ensure_pending(note_revision_id=REVISION_ID)

    failed_service, _ = _service(
        fixed_clock,
        id_generator,
        notes=_Notes(_revision(), succeeded=False),
    )
    with pytest.raises(InvalidExternalNoteReviewTransition):
        failed_service.ensure_pending(note_revision_id=REVISION_ID)


def test_pending_review_recovers_exact_concurrent_materialization(
    fixed_clock, id_generator
) -> None:
    fixed_clock.set(NOW)

    class RacingReviews(_Reviews):
        raced = False

        def append(
            self, value: ExternalNoteReview, *, expected_version: int
        ) -> ExternalNoteReview:
            if not self.raced:
                self.raced = True
                self.values.append(value)
                raise PersistenceError("simulated concurrent insert")
            return super().append(value, expected_version=expected_version)

    reviews = RacingReviews()
    service = ExternalNoteReviewService(
        reviews,  # type: ignore[arg-type]
        _Notes(_revision()),  # type: ignore[arg-type]
        lambda: _Uow(),  # type: ignore[arg-type]
        fixed_clock,
        id_generator,
    )

    result = service.ensure_pending(note_revision_id=REVISION_ID)

    assert result.status == "PENDING"
    assert len(reviews.values) == 1


def test_review_defers_then_adopts_exact_decision(fixed_clock, id_generator) -> None:
    fixed_clock.set(NOW)
    service, reviews = _service(fixed_clock, id_generator)
    pending = service.ensure_pending(note_revision_id=REVISION_ID, subject_id=SUBJECT_ID)
    deferred = service.transition(
        ExternalNoteReviewTransitionInput(
            review_id=pending.review_id,
            status="DEFERRED",
            expected_version=1,
            subject_id=SUBJECT_ID,
            due_at=NOW + timedelta(days=1),
            actor="user",
            authorization_note="Review after earnings call.",
            idempotency_key="defer-once",
        )
    )
    adopted = service.transition(
        ExternalNoteReviewTransitionInput(
            review_id=pending.review_id,
            status="ADOPTED",
            expected_version=2,
            subject_id=SUBJECT_ID,
            decision_id=DECISION_ID,
            actor="user",
            authorization_note="Adopt the exact reviewed view.",
            idempotency_key="adopt-once",
        )
    )

    assert deferred.status == "DEFERRED"
    assert adopted.status == "ADOPTED"
    assert adopted.decision_id == DECISION_ID
    assert [item.version for item in reviews.values] == [1, 2, 3]
    with pytest.raises(InvalidExternalNoteReviewTransition):
        service.transition(
            ExternalNoteReviewTransitionInput(
                review_id=pending.review_id,
                status="NO_ACTION",
                expected_version=3,
                subject_id=SUBJECT_ID,
                decision_id=DECISION_ID,
                actor="user",
                authorization_note="Cannot rewrite terminal outcome.",
                idempotency_key="terminal-rewrite",
            )
        )


def test_review_rejects_decision_for_another_revision(fixed_clock, id_generator) -> None:
    fixed_clock.set(NOW)
    service, _ = _service(
        fixed_clock,
        id_generator,
        uow=lambda: _Uow(decision_revision_id="external_note_revision_other"),
    )
    pending = service.ensure_pending(note_revision_id=REVISION_ID, subject_id=SUBJECT_ID)

    with pytest.raises(InvalidResearchLink):
        service.transition(
            ExternalNoteReviewTransitionInput(
                review_id=pending.review_id,
                status="ADOPTED",
                expected_version=1,
                subject_id=SUBJECT_ID,
                decision_id=DECISION_ID,
                actor="user",
                authorization_note="Wrong revision must fail.",
                idempotency_key="wrong-revision",
            )
        )


def test_sql_repository_appends_versions_and_filters_latest(
    migrated_sqlite_url, fixed_clock, id_generator
) -> None:
    fixed_clock.set(NOW)
    engine = create_engine(migrated_sqlite_url)
    notes = SqlAlchemyExternalNoteRepository(engine)
    reviews = SqlAlchemyExternalNoteReviewRepository(engine)
    notes.append_identity(
        ExternalNoteIdentity(
            note_id=NOTE_ID,
            source="MOOMOO_NOTE",
            external_id="nvda-review-test",
            title="NVDA",
            primary_instrument_id="equity:US:NVDA",
            created_at=NOW,
            last_seen_at=NOW,
        )
    )
    notes.append_revision(_revision())
    review_id = id_generator.new(EntityIdPrefix.EXTERNAL_NOTE_REVIEW)
    pending = ExternalNoteReview(
        review_id=review_id,
        note_revision_id=REVISION_ID,
        note_id=NOTE_ID,
        version=1,
        status=ExternalNoteReviewStatus.PENDING,
        subject_id=None,
        decision_id=None,
        due_at=None,
        actor="system",
        authorization_note="Await review.",
        idempotency_key="repo-pending",
        created_at=NOW,
    )
    reviews.append(pending, expected_version=0)
    deferred = replace(
        pending,
        version=2,
        status=ExternalNoteReviewStatus.DEFERRED,
        due_at=NOW + timedelta(days=2),
        actor="user",
        authorization_note="Wait for earnings.",
        idempotency_key="repo-deferred",
        created_at=NOW + timedelta(minutes=1),
    )
    reviews.append(deferred, expected_version=1)

    assert reviews.latest_for_revision(REVISION_ID) == deferred
    assert reviews.list_latest(
        statuses=frozenset({ExternalNoteReviewStatus.DEFERRED})
    ) == (deferred,)
    assert reviews.append(deferred, expected_version=1) == deferred
    with pytest.raises(ExternalNoteReviewVersionConflict):
        reviews.append(
            replace(
                deferred,
                version=3,
                idempotency_key="repo-bad-version",
            ),
            expected_version=0,
        )
    with pytest.raises(ExternalNoteReviewVersionConflict):
        reviews.append(
            replace(
                deferred,
                version=3,
                note_revision_id="external_note_revision_other",
                idempotency_key="repo-source-rewrite",
            ),
            expected_version=2,
        )
    with pytest.raises(IdempotencyConflict):
        reviews.append(
            replace(
                deferred,
                version=3,
                authorization_note="Different payload.",
                idempotency_key="repo-deferred",
            ),
            expected_version=2,
        )
