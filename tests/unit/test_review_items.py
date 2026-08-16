from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from application.dto.review_item import ReviewItemTransitionInput
from application.services.review_item_service import ReviewItemService
from domain.common.errors import IdempotencyConflict, ReviewItemVersionConflict
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType
from domain.review_item.models import ReviewItemProjection
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.review_item_repository import SqlAlchemyReviewItemRepository


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self) -> None:
        self.count = 0

    def new(self, _prefix: object) -> str:
        self.count += 1
        return f"review_item_{self.count}"


def _projection(detail: str = "No durable outcome is linked.") -> ReviewItemProjection:
    return ReviewItemProjection(
        source_key="agenda-overdue-agenda_1",
        source_type=ReviewItemSourceType.CATALYST_AGENDA,
        source_ref="agenda_1",
        subject_id="case_1",
        title="Catalyst outcome overdue",
        detail=detail,
        severity=ReviewItemSeverity.ATTENTION,
        recommended_action="LINK_OUTCOME_OR_REVISE",
        href="/agenda#agenda-detail",
    )


@pytest.fixture
def service() -> tuple[ReviewItemService, _Clock]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    clock = _Clock(datetime(2026, 8, 13, 9, tzinfo=UTC))
    return ReviewItemService(SqlAlchemyReviewItemRepository(engine), clock, _Ids()), clock


def test_review_item_auto_resolves_only_after_successful_source_observation_and_reopens(
    service: tuple[ReviewItemService, _Clock],
) -> None:
    subject, clock = service
    created = subject.reconcile(
        (_projection(),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    assert (created.status, created.version, created.occurrence_count) == ("OPEN", 1, 1)

    clock.value += timedelta(hours=1)
    # A failed/unobserved source is deliberately excluded and cannot close the item.
    subject.reconcile((), observed_source_types=frozenset())
    assert subject.list_open()[0].status == "OPEN"

    clock.value += timedelta(hours=1)
    subject.reconcile(
        (),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
        authoritative_source_refs=frozenset({(ReviewItemSourceType.CATALYST_AGENDA, "agenda_1")}),
    )
    assert subject.list_open() == ()

    clock.value += timedelta(hours=1)
    reopened = subject.reconcile(
        (_projection("The same source condition recurred."),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    assert (reopened.status, reopened.version, reopened.occurrence_count) == ("OPEN", 3, 2)
    assert reopened.detail == "The same source condition recurred."


def test_review_item_human_resolution_is_versioned_idempotent_and_not_immediately_reopened(
    service: tuple[ReviewItemService, _Clock],
) -> None:
    subject, clock = service
    created = subject.reconcile(
        (_projection(),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    request = ReviewItemTransitionInput(
        review_item_id=created.review_item_id,
        status="RESOLVED",
        expected_version=1,
        actor="user",
        authorization_note="User linked the durable outcome in Console.",
        resolution_note="Linked the earnings event outcome.",
        resolution_ref="event_1",
        idempotency_key="review-resolve-1",
    )
    resolved = subject.transition(request)
    assert (resolved.status, resolved.version, resolved.resolved_by) == ("RESOLVED", 2, "user")
    assert subject.transition(request).version == 2

    clock.value += timedelta(hours=1)
    still_present = subject.reconcile(
        (_projection(),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    assert still_present.status == "RESOLVED"
    assert subject.list_open() == ()

    with pytest.raises(ReviewItemVersionConflict):
        subject.transition(
            request.model_copy(update={"idempotency_key": "stale", "expected_version": 1})
        )
    with pytest.raises(IdempotencyConflict):
        subject.transition(request.model_copy(update={"status": "ACKNOWLEDGED"}))


def test_review_item_metrics_use_each_occurrence_not_lifetime_first_seen(
    service: tuple[ReviewItemService, _Clock],
) -> None:
    subject, clock = service
    created = subject.reconcile(
        (_projection(),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    clock.value += timedelta(hours=1)
    subject.transition(
        ReviewItemTransitionInput(
            review_item_id=created.review_item_id,
            status="ACKNOWLEDGED",
            expected_version=1,
            actor="user",
            authorization_note="User accepted ownership.",
            due_at=clock.value - timedelta(minutes=1),
            idempotency_key="review-ack-1",
        )
    )
    acknowledged = subject.metrics(subject_id="case_1")
    assert acknowledged.oldest_current_open_age_seconds == 3_600
    assert acknowledged.median_open_to_ack_seconds == 3_600
    assert acknowledged.overdue_count == 1

    clock.value += timedelta(minutes=30)
    subject.transition(
        ReviewItemTransitionInput(
            review_item_id=created.review_item_id,
            status="ACKNOWLEDGED",
            expected_version=2,
            actor="user",
            authorization_note="User adjusted the due date.",
            due_at=clock.value + timedelta(days=1),
            idempotency_key="review-ack-update-due",
        )
    )
    assert subject.metrics().median_open_to_ack_seconds == 3_600

    clock.value += timedelta(minutes=90)
    subject.reconcile(
        (),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
        authoritative_source_refs=frozenset({(ReviewItemSourceType.CATALYST_AGENDA, "agenda_1")}),
    )
    clock.value += timedelta(hours=1)
    reopened = subject.reconcile(
        (_projection("Recurred."),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    metrics = subject.metrics(subject_id="case_1")
    assert metrics.oldest_current_open_age_seconds == 0
    assert metrics.recurring_count == 1
    assert metrics.recurrence_rate == 1.0
    assert metrics.auto_closure_count == 1

    clock.value += timedelta(hours=2)
    subject.transition(
        ReviewItemTransitionInput(
            review_item_id=reopened.review_item_id,
            status="RESOLVED",
            expected_version=reopened.version,
            actor="user",
            authorization_note="User completed the repeated follow-up.",
            resolution_note="Closed with durable evidence.",
            resolution_ref="event_2",
            idempotency_key="review-resolve-2",
        )
    )
    closed = subject.metrics(subject_id="case_1")
    assert closed.oldest_current_open_age_seconds is None
    assert closed.median_open_to_close_seconds == 9_000
    assert closed.closure_sample_size == 2
    assert closed.manual_closure_count == 1
    assert closed.auto_closure_count == 1
    assert closed.manual_resolution_rate == 0.5


def test_review_item_metrics_are_not_limited_by_queue_page_size(
    service: tuple[ReviewItemService, _Clock],
) -> None:
    subject, _clock = service
    projections = tuple(
        replace(
            _projection(),
            source_key=f"agenda-overdue-agenda_{index}",
            source_ref=f"agenda_{index}",
        )
        for index in range(501)
    )
    subject.reconcile(
        projections,
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )
    assert len(subject.list_open(limit=500)) == 500
    assert subject.metrics().total_items == 501


def test_partial_source_page_cannot_auto_resolve_an_unobserved_reference(
    service: tuple[ReviewItemService, _Clock],
) -> None:
    subject, clock = service
    first = _projection()
    second = replace(
        first,
        source_key="agenda-overdue-agenda_2",
        source_ref="agenda_2",
    )
    subject.reconcile(
        (first, second),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )

    clock.value += timedelta(hours=1)
    subject.reconcile(
        (),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
        authoritative_source_refs=frozenset({(ReviewItemSourceType.CATALYST_AGENDA, "agenda_1")}),
    )

    remaining = subject.list_open()
    assert [item.source_ref for item in remaining] == ["agenda_2"]
    assert subject.metrics().auto_resolved_count == 1


def test_queue_priority_is_applied_before_page_limit(
    service: tuple[ReviewItemService, _Clock],
) -> None:
    subject, clock = service
    urgent = replace(_projection(), severity=ReviewItemSeverity.ERROR)
    subject.reconcile(
        (urgent,),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )
    clock.value += timedelta(hours=1)
    ordinary = tuple(
        replace(
            _projection(),
            source_key=f"agenda-overdue-recent-{index}",
            source_ref=f"recent_{index}",
        )
        for index in range(500)
    )
    subject.reconcile(
        ordinary,
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )

    assert subject.list_open(limit=1)[0].source_ref == "agenda_1"


def test_stale_reconciliation_cannot_regress_or_close_newer_state(
    service: tuple[ReviewItemService, _Clock],
) -> None:
    subject, clock = service
    subject.reconcile(
        (_projection("Initial"),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )
    clock.value += timedelta(hours=2)
    current = subject.reconcile(
        (_projection("Newest durable projection"),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]

    clock.value -= timedelta(hours=1)
    stale = subject.reconcile(
        (_projection("Stale projection"),),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
    )[0]
    subject.reconcile(
        (),
        observed_source_types=frozenset({ReviewItemSourceType.CATALYST_AGENDA}),
        authoritative_source_refs=frozenset({(ReviewItemSourceType.CATALYST_AGENDA, "agenda_1")}),
    )

    assert stale.detail == "Newest durable projection"
    assert stale.last_seen_at == current.last_seen_at
    assert subject.list_open()[0].review_item_id == current.review_item_id
