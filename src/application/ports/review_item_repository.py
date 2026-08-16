"""Persistence boundary for durable cross-feature ReviewItems."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.review_item.enums import ReviewItemSourceType, ReviewItemStatus
from domain.review_item.models import ReviewItem, ReviewItemMetrics, ReviewItemProjection


class ReviewItemRepository(Protocol):
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
    ) -> tuple[ReviewItem, ...]: ...

    def list(
        self,
        *,
        statuses: frozenset[ReviewItemStatus] | None = None,
        subject_id: str | None = None,
        limit: int | None = 100,
    ) -> tuple[ReviewItem, ...]: ...

    def metrics(
        self,
        *,
        now: datetime,
        subject_id: str | None = None,
    ) -> ReviewItemMetrics: ...

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
    ) -> ReviewItem: ...
