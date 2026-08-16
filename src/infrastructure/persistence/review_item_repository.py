"""SQLAlchemy materialized ReviewItem projection and human transitions."""

from __future__ import annotations

from datetime import datetime
from statistics import median

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.common.errors import (
    IdempotencyConflict,
    PersistenceError,
    ReviewItemNotFound,
    ReviewItemVersionConflict,
)
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType, ReviewItemStatus
from domain.review_item.models import ReviewItem, ReviewItemMetrics, ReviewItemProjection
from infrastructure.persistence.orm.review_item import (
    ReviewItemActionRow,
    ReviewItemOccurrenceRow,
    ReviewItemRow,
)


def _domain(row: ReviewItemRow) -> ReviewItem:
    return ReviewItem(
        review_item_id=row.review_item_id,
        source_key=row.source_key,
        source_type=ReviewItemSourceType(row.source_type),
        source_ref=row.source_ref,
        subject_id=row.subject_id,
        title=row.title,
        detail=row.detail,
        severity=ReviewItemSeverity(row.severity),
        recommended_action=row.recommended_action,
        href=row.href,
        status=ReviewItemStatus(row.status),
        active_at_source=bool(row.active_at_source),
        first_seen_at=datetime.fromisoformat(row.first_seen_at),
        last_seen_at=datetime.fromisoformat(row.last_seen_at),
        due_at=datetime.fromisoformat(row.due_at) if row.due_at else None,
        resolved_at=datetime.fromisoformat(row.resolved_at) if row.resolved_at else None,
        resolved_by=row.resolved_by,
        resolution_note=row.resolution_note,
        resolution_ref=row.resolution_ref,
        occurrence_count=row.occurrence_count,
        version=row.version,
    )


class SqlAlchemyReviewItemRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def reconcile(
        self,
        *,
        projections: tuple[ReviewItemProjection, ...],
        observed_source_types: frozenset[ReviewItemSourceType],
        authoritative_source_refs: frozenset[tuple[ReviewItemSourceType, str]] = frozenset(),
        fully_observed_source_types: frozenset[ReviewItemSourceType] = frozenset(),
        observed_at: datetime,
        new_ids: dict[str, str],
        subject_scope: str | None = None,
        _retry: bool = False,
    ) -> tuple[ReviewItem, ...]:
        observed_values = {item.value for item in observed_source_types}
        projection_keys = {item.source_key for item in projections}
        try:
            with Session(self._engine) as session, session.begin():
                statement = select(ReviewItemRow).where(
                    ReviewItemRow.source_type.in_(observed_values)
                )
                rows = tuple(session.scalars(statement)) if observed_values else ()
                by_key = {row.source_key: row for row in rows}
                touched: list[ReviewItemRow] = []
                for item in projections:
                    row = by_key.get(item.source_key)
                    if row is None:
                        row = ReviewItemRow(
                            review_item_id=new_ids[item.source_key],
                            source_key=item.source_key,
                            source_type=item.source_type.value,
                            source_ref=item.source_ref,
                            subject_id=item.subject_id,
                            title=item.title,
                            detail=item.detail,
                            severity=item.severity.value,
                            recommended_action=item.recommended_action,
                            href=item.href,
                            status=ReviewItemStatus.OPEN.value,
                            active_at_source=1,
                            first_seen_at=observed_at.isoformat(),
                            last_seen_at=observed_at.isoformat(),
                            due_at=item.due_at.isoformat() if item.due_at else None,
                            resolved_at=None,
                            resolved_by=None,
                            resolution_note=None,
                            resolution_ref=None,
                            occurrence_count=1,
                            version=1,
                        )
                        session.add(row)
                        session.add(
                            ReviewItemOccurrenceRow(
                                review_item_id=row.review_item_id,
                                occurrence_no=1,
                                opened_at=observed_at.isoformat(),
                                last_seen_at=observed_at.isoformat(),
                            )
                        )
                    else:
                        if observed_at < datetime.fromisoformat(row.last_seen_at):
                            touched.append(row)
                            continue
                        reappeared = not bool(row.active_at_source)
                        row.source_ref = item.source_ref
                        row.subject_id = item.subject_id
                        row.title = item.title
                        row.detail = item.detail
                        row.severity = item.severity.value
                        row.recommended_action = item.recommended_action
                        row.href = item.href
                        row.last_seen_at = observed_at.isoformat()
                        row.active_at_source = 1
                        if reappeared:
                            row.status = ReviewItemStatus.OPEN.value
                            row.resolved_at = None
                            row.resolved_by = None
                            row.resolution_note = None
                            row.resolution_ref = None
                            row.due_at = item.due_at.isoformat() if item.due_at else None
                            row.occurrence_count += 1
                            row.version += 1
                            session.add(
                                ReviewItemOccurrenceRow(
                                    review_item_id=row.review_item_id,
                                    occurrence_no=row.occurrence_count,
                                    opened_at=observed_at.isoformat(),
                                    last_seen_at=observed_at.isoformat(),
                                )
                            )
                        elif item.due_at is not None:
                            row.due_at = item.due_at.isoformat()
                        if not reappeared:
                            occurrence = session.get(
                                ReviewItemOccurrenceRow,
                                (row.review_item_id, row.occurrence_count),
                            )
                            if occurrence is not None:
                                occurrence.last_seen_at = observed_at.isoformat()
                    touched.append(row)

                for row in rows:
                    if row.source_key in projection_keys or not bool(row.active_at_source):
                        continue
                    if observed_at < datetime.fromisoformat(row.last_seen_at):
                        continue
                    if subject_scope is not None and row.subject_id != subject_scope:
                        continue
                    source_type = ReviewItemSourceType(row.source_type)
                    if (
                        source_type not in fully_observed_source_types
                        and (source_type, row.source_ref) not in authoritative_source_refs
                    ):
                        continue
                    row.active_at_source = 0
                    if row.status in {
                        ReviewItemStatus.OPEN.value,
                        ReviewItemStatus.ACKNOWLEDGED.value,
                    }:
                        row.status = ReviewItemStatus.AUTO_RESOLVED.value
                        row.resolved_at = observed_at.isoformat()
                        row.resolved_by = "system"
                        row.resolution_note = "The durable source no longer reports this issue."
                        row.resolution_ref = None
                        row.version += 1
                        occurrence = session.get(
                            ReviewItemOccurrenceRow,
                            (row.review_item_id, row.occurrence_count),
                        )
                        if occurrence is not None and occurrence.resolved_at is None:
                            occurrence.resolved_at = observed_at.isoformat()
                            occurrence.resolved_by = "system"
                            occurrence.resolution_mode = "AUTO"
                    touched.append(row)
                session.flush()
                return tuple(_domain(row) for row in touched)
        except IntegrityError as exc:
            if not _retry:
                return self.reconcile(
                    projections=projections,
                    observed_source_types=observed_source_types,
                    authoritative_source_refs=authoritative_source_refs,
                    fully_observed_source_types=fully_observed_source_types,
                    observed_at=observed_at,
                    new_ids=new_ids,
                    subject_scope=subject_scope,
                    _retry=True,
                )
            raise PersistenceError("ReviewItem reconciliation conflict") from exc

    def list(
        self,
        *,
        statuses: frozenset[ReviewItemStatus] | None = None,
        subject_id: str | None = None,
        limit: int | None = 100,
    ) -> tuple[ReviewItem, ...]:
        with Session(self._engine) as session:
            statement = select(ReviewItemRow)
            if statuses is not None:
                statement = statement.where(
                    ReviewItemRow.status.in_({item.value for item in statuses})
                )
            if subject_id is not None:
                statement = statement.where(ReviewItemRow.subject_id == subject_id)
            statement = statement.order_by(
                ReviewItemRow.last_seen_at.desc(),
                ReviewItemRow.review_item_id.desc(),
            )
            if limit is not None:
                statement = statement.limit(limit)
            rows = session.scalars(statement)
            return tuple(_domain(row) for row in rows)

    def metrics(
        self,
        *,
        now: datetime,
        subject_id: str | None = None,
    ) -> ReviewItemMetrics:
        with Session(self._engine) as session:
            statement = select(ReviewItemRow)
            if subject_id is not None:
                statement = statement.where(ReviewItemRow.subject_id == subject_id)
            rows = tuple(session.scalars(statement))
            relevant_ids = tuple(row.review_item_id for row in rows)
            occurrences = (
                tuple(
                    session.scalars(
                        select(ReviewItemOccurrenceRow).where(
                            ReviewItemOccurrenceRow.review_item_id.in_(relevant_ids)
                        )
                    )
                )
                if relevant_ids
                else ()
            )
        open_rows = tuple(
            row
            for row in rows
            if row.status in {ReviewItemStatus.OPEN.value, ReviewItemStatus.ACKNOWLEDGED.value}
        )
        scoped_occurrences = occurrences
        close_durations = [
            max(
                0,
                int(
                    (
                        datetime.fromisoformat(item.resolved_at)
                        - datetime.fromisoformat(item.opened_at)
                    ).total_seconds()
                ),
            )
            for item in scoped_occurrences
            if item.resolved_at is not None
        ]
        acknowledge_durations = [
            max(
                0,
                int(
                    (
                        datetime.fromisoformat(item.first_acknowledged_at)
                        - datetime.fromisoformat(item.opened_at)
                    ).total_seconds()
                ),
            )
            for item in scoped_occurrences
            if item.first_acknowledged_at is not None
        ]
        open_by_source: dict[str, int] = {}
        for row in open_rows:
            open_by_source[row.source_type] = open_by_source.get(row.source_type, 0) + 1
        manual_closure_count = sum(item.resolution_mode == "MANUAL" for item in scoped_occurrences)
        auto_closure_count = sum(item.resolution_mode == "AUTO" for item in scoped_occurrences)
        recurring_count = sum(row.occurrence_count > 1 for row in rows)
        current_occurrences = {
            (item.review_item_id, item.occurrence_no): item for item in scoped_occurrences
        }
        current_opened_at = tuple(
            datetime.fromisoformat(item.opened_at)
            for row in open_rows
            if (item := current_occurrences.get((row.review_item_id, row.occurrence_count)))
            is not None
        )
        return ReviewItemMetrics(
            measured_at=now,
            total_items=len(rows),
            open_count=sum(row.status == ReviewItemStatus.OPEN.value for row in rows),
            acknowledged_count=sum(
                row.status == ReviewItemStatus.ACKNOWLEDGED.value for row in rows
            ),
            resolved_count=sum(row.status == ReviewItemStatus.RESOLVED.value for row in rows),
            auto_resolved_count=sum(
                row.status == ReviewItemStatus.AUTO_RESOLVED.value for row in rows
            ),
            overdue_count=sum(
                row.due_at is not None and datetime.fromisoformat(row.due_at) < now
                for row in open_rows
            ),
            recurring_count=recurring_count,
            oldest_current_open_age_seconds=(
                max(0, int((now - min(current_opened_at)).total_seconds()))
                if current_opened_at
                else None
            ),
            median_open_to_ack_seconds=(
                int(median(acknowledge_durations)) if acknowledge_durations else None
            ),
            median_open_to_close_seconds=(
                int(median(close_durations)) if close_durations else None
            ),
            acknowledgment_sample_size=len(acknowledge_durations),
            closure_sample_size=len(close_durations),
            manual_closure_count=manual_closure_count,
            auto_closure_count=auto_closure_count,
            manual_resolution_rate=(
                manual_closure_count / len(close_durations) if close_durations else None
            ),
            recurrence_rate=(recurring_count / len(rows) if rows else None),
            open_by_source=open_by_source,
        )

    def transition(
        self,
        *,
        review_item_id: str,
        status: ReviewItemStatus,
        expected_version: int,
        actor: str,
        authorization_note: str,
        resolution_note: str | None,
        resolution_ref: str | None,
        due_at: datetime | None,
        idempotency_key: str,
        now: datetime,
    ) -> ReviewItem:
        with Session(self._engine) as session, session.begin():
            prior = session.scalar(
                select(ReviewItemActionRow).where(
                    ReviewItemActionRow.idempotency_key == idempotency_key
                )
            )
            if prior is not None:
                row = session.get(ReviewItemRow, review_item_id)
                if row is None:
                    raise ReviewItemNotFound("ReviewItem was not found")
                if (
                    prior.review_item_id != review_item_id
                    or prior.occurrence_no != row.occurrence_count
                    or prior.status != status.value
                    or prior.expected_version != expected_version
                    or prior.actor != actor
                    or prior.authorization_note != authorization_note
                    or prior.resolution_note != resolution_note
                    or prior.resolution_ref != resolution_ref
                    or prior.due_at != (due_at.isoformat() if due_at else None)
                ):
                    raise IdempotencyConflict("ReviewItem idempotency key was reused")
                return _domain(row)

            row = session.get(ReviewItemRow, review_item_id)
            if row is None:
                raise ReviewItemNotFound("ReviewItem was not found")
            if row.version != expected_version:
                raise ReviewItemVersionConflict(
                    "ReviewItem version changed",
                    details={"current_version": row.version},
                )
            if row.status in {
                ReviewItemStatus.RESOLVED.value,
                ReviewItemStatus.AUTO_RESOLVED.value,
            }:
                raise ReviewItemVersionConflict("Resolved ReviewItem cannot be changed")
            row.status = status.value
            row.version += 1
            occurrence = session.get(
                ReviewItemOccurrenceRow,
                (row.review_item_id, row.occurrence_count),
            )
            if occurrence is None:
                raise PersistenceError("ReviewItem occurrence is missing")
            if status is ReviewItemStatus.ACKNOWLEDGED and occurrence.first_acknowledged_at is None:
                occurrence.first_acknowledged_at = now.isoformat()
                occurrence.first_acknowledged_by = actor
            if status is ReviewItemStatus.RESOLVED:
                row.resolved_at = now.isoformat()
                row.resolved_by = actor
                row.resolution_note = resolution_note
                row.resolution_ref = resolution_ref
                occurrence.resolved_at = now.isoformat()
                occurrence.resolved_by = actor
                occurrence.resolution_mode = "MANUAL"
            if due_at is not None:
                row.due_at = due_at.isoformat()
            session.add(
                ReviewItemActionRow(
                    review_item_id=review_item_id,
                    occurrence_no=row.occurrence_count,
                    status=status.value,
                    expected_version=expected_version,
                    result_version=row.version,
                    actor=actor,
                    authorization_note=authorization_note,
                    resolution_note=resolution_note,
                    resolution_ref=resolution_ref,
                    due_at=due_at.isoformat() if due_at else None,
                    idempotency_key=idempotency_key,
                    created_at=now.isoformat(),
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise PersistenceError("ReviewItem transition conflict") from exc
            return _domain(row)
