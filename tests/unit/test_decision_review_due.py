"""Decision review-due source query and ReviewItem lifecycle coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from application.services.review_item_service import ReviewItemService
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType, ReviewItemStatus
from domain.review_item.models import ReviewItemProjection
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.orm import DecisionRecordRow
from infrastructure.persistence.repositories.decision_record import (
    SqlAlchemyDecisionRecordRepository,
)
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
SUBJECT = "case_00000000-0000-7000-8000-000000000001"
OTHER_SUBJECT = "case_00000000-0000-7000-8000-000000000002"


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self) -> None:
        self.index = 0

    def new(self, _prefix: object) -> str:
        self.index += 1
        return f"review_item_00000000-0000-7000-8000-{self.index:012d}"


def _engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _row(
    number: int,
    *,
    subject_id: str = SUBJECT,
    recorded_at: datetime = NOW - timedelta(days=2),
    review_due_at: datetime | None = NOW - timedelta(hours=1),
    supersedes_decision_id: str | None = None,
) -> DecisionRecordRow:
    decision_id = f"decision_00000000-0000-7000-8000-{number:012d}"
    return DecisionRecordRow(
        decision_id=decision_id,
        subject_id=subject_id,
        decision_type="watch",
        title=f"Decision {number}",
        rationale="Review the durable decision context.",
        decided_at=recorded_at.isoformat(),
        recorded_at=recorded_at.isoformat(),
        decided_by="user",
        confirmation_mode="normal",
        primary_instrument_id=None,
        thesis_revision_ids_json=(),
        evidence_ids_json=(),
        report_ids_json=(),
        supersedes_decision_id=supersedes_decision_id,
        position_context_snapshot_id=None,
        strategy_code=None,
        strategy_version=None,
        scenario=None,
        trade_plan_id=None,
        trade_plan_version=None,
        review_due_at=review_due_at.isoformat() if review_due_at is not None else None,
        idempotency_key=f"decision-review-{number}",
        idempotency_payload_sha256="a" * 64,
        schema_version=1,
    )


def test_due_query_uses_cutoff_and_exact_later_superseder() -> None:
    engine = _engine()
    old = _row(1)
    exact_successor = _row(
        2,
        recorded_at=NOW - timedelta(days=1),
        review_due_at=None,
        supersedes_decision_id=old.decision_id,
    )
    unrelated = _row(
        3,
        recorded_at=NOW - timedelta(days=1),
        review_due_at=NOW - timedelta(hours=2),
    )
    future = _row(4, review_due_at=NOW + timedelta(minutes=1))
    with Session(engine) as session:
        session.add_all((old, exact_successor, unrelated, future))
        session.commit()
        values = SqlAlchemyDecisionRecordRepository(session).list_review_due(now=NOW)
    # The exact successor hides only decision 1; unrelated later records do
    # not clear a due reminder, while a future due time is not yet visible.
    assert [value.decision_id for value in values] == [
        "decision_00000000-0000-7000-8000-000000000003"
    ]
    engine.dispose()


def test_due_query_supports_subject_scope_and_bound() -> None:
    engine = _engine()
    rows = [_row(number) for number in range(1, 4)]
    rows.append(_row(4, subject_id=OTHER_SUBJECT))
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()
        values = SqlAlchemyDecisionRecordRepository(session).list_review_due(
            now=NOW,
            subject_id=SUBJECT,
            limit=2,
        )
    assert len(values) == 2
    assert all(value.subject_id == SUBJECT for value in values)
    assert [value.decision_id for value in values] == [rows[0].decision_id, rows[1].decision_id]
    engine.dispose()


def _projection(decision_id: str) -> ReviewItemProjection:
    return ReviewItemProjection(
        source_key=f"decision-review-due-{decision_id}",
        source_type=ReviewItemSourceType.DECISION_REVIEW_DUE,
        source_ref=decision_id,
        subject_id=SUBJECT,
        title="Decision review due · Decision",
        detail="The Decision review deadline has passed.",
        severity=ReviewItemSeverity.ATTENTION,
        recommended_action="REVIEW_DECISION",
        href=(
            f"/decision-workbench?subject_id={SUBJECT}&capture=decision"
            f"&supersedes_decision_id={decision_id}"
        ),
        due_at=NOW - timedelta(hours=1),
    )


def test_due_review_item_recurrence_and_exact_auto_close() -> None:
    engine = _engine()
    clock = _Clock(NOW)
    service = ReviewItemService(SqlAlchemyReviewItemRepository(engine), clock, _Ids())
    decision_id = "decision_00000000-0000-7000-8000-000000000101"
    projection = _projection(decision_id)

    first = service.reconcile(
        (projection,),
        observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
        fully_observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
    )
    assert first[0].status == ReviewItemStatus.OPEN.value
    clock.value = NOW + timedelta(hours=1)
    service.reconcile(
        (projection,),
        observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
        fully_observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
    )
    assert service.list_open(subject_id=SUBJECT)[0].occurrence_count == 1

    clock.value = NOW + timedelta(hours=2)
    closed = service.reconcile(
        (),
        observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
        fully_observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
    )
    assert closed[0].status == ReviewItemStatus.AUTO_RESOLVED.value
    assert closed[0].active_at_source is False
    clock.value = NOW + timedelta(hours=3)
    reopened = service.reconcile(
        (projection,),
        observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
        fully_observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
    )
    assert reopened[0].status == ReviewItemStatus.OPEN.value
    assert reopened[0].occurrence_count == 2
    engine.dispose()


def test_bounded_or_failed_source_read_cannot_auto_close() -> None:
    engine = _engine()
    clock = _Clock(NOW)
    service = ReviewItemService(SqlAlchemyReviewItemRepository(engine), clock, _Ids())
    projection = _projection("decision_00000000-0000-7000-8000-000000000102")
    service.reconcile(
        (projection,),
        observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
        fully_observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
    )

    # A successful bounded page and a failed read both omit the full-observed
    # marker; the source remains open until an authoritative later read.
    service.reconcile(
        (),
        observed_source_types=frozenset({ReviewItemSourceType.DECISION_REVIEW_DUE}),
    )
    still_open = service.list_open(subject_id=SUBJECT)
    assert still_open[0].status == ReviewItemStatus.OPEN.value
    assert still_open[0].active_at_source is True
    engine.dispose()
