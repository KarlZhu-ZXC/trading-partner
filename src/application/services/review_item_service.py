"""Deterministic materialization and explicit human closure for ReviewItems."""

from __future__ import annotations

from application.dto.review_item import (
    ReviewItemDTO,
    ReviewItemMetricsDTO,
    ReviewItemTransitionInput,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.review_item_repository import ReviewItemRepository
from domain.common.errors import DataContractError
from domain.common.ids import EntityIdPrefix
from domain.review_item.enums import ReviewItemSourceType, ReviewItemStatus
from domain.review_item.models import ReviewItemProjection


class ReviewItemService:
    def __init__(
        self,
        repository: ReviewItemRepository,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator

    def reconcile(
        self,
        projections: tuple[ReviewItemProjection, ...],
        *,
        observed_source_types: frozenset[ReviewItemSourceType],
        authoritative_source_refs: frozenset[tuple[ReviewItemSourceType, str]] = frozenset(),
        fully_observed_source_types: frozenset[ReviewItemSourceType] = frozenset(),
        subject_scope: str | None = None,
    ) -> tuple[ReviewItemDTO, ...]:
        keys = tuple(item.source_key for item in projections)
        if len(keys) != len(set(keys)):
            raise DataContractError("ReviewItem projections require unique source_key values")
        if any(item.source_type not in observed_source_types for item in projections):
            raise DataContractError("projection source_type must be marked observed")
        if any(
            source_type not in observed_source_types
            for source_type, _source_ref in authoritative_source_refs
        ):
            raise DataContractError("authoritative source refs must be marked observed")
        if not fully_observed_source_types.issubset(observed_source_types):
            raise DataContractError("fully observed source types must be marked observed")
        if subject_scope is not None and any(
            item.subject_id != subject_scope for item in projections
        ):
            raise DataContractError("scoped ReviewItem projections must match subject_scope")
        values = self._repository.reconcile(
            projections=projections,
            observed_source_types=observed_source_types,
            authoritative_source_refs=authoritative_source_refs,
            fully_observed_source_types=fully_observed_source_types,
            observed_at=self._clock.now(),
            new_ids={
                item.source_key: self._id_generator.new(EntityIdPrefix.REVIEW_ITEM)
                for item in projections
            },
            subject_scope=subject_scope,
        )
        return tuple(ReviewItemDTO.from_domain(item) for item in values)

    def list_open(
        self,
        *,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ReviewItemDTO, ...]:
        if not 1 <= limit <= 500:
            raise DataContractError("ReviewItem limit must be between 1 and 500")
        values = self._repository.list(
            statuses=frozenset({ReviewItemStatus.OPEN, ReviewItemStatus.ACKNOWLEDGED}),
            subject_id=subject_id,
            limit=None,
        )
        now = self._clock.now()
        ordered = sorted(
            values,
            key=lambda item: (
                0 if item.severity.value == "ERROR" else 1,
                0 if item.due_at is not None and item.due_at < now else 1,
                item.due_at or item.first_seen_at,
                item.first_seen_at,
            ),
        )[:limit]
        return tuple(ReviewItemDTO.from_domain(item) for item in ordered)

    def list_recent(
        self,
        *,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ReviewItemDTO, ...]:
        if not 1 <= limit <= 500:
            raise DataContractError("ReviewItem limit must be between 1 and 500")
        values = self._repository.list(
            subject_id=subject_id,
            limit=limit,
        )
        return tuple(ReviewItemDTO.from_domain(item) for item in values)

    def metrics(self, *, subject_id: str | None = None) -> ReviewItemMetricsDTO:
        return ReviewItemMetricsDTO.from_domain(
            self._repository.metrics(now=self._clock.now(), subject_id=subject_id)
        )

    def transition(self, request: ReviewItemTransitionInput) -> ReviewItemDTO:
        try:
            status = ReviewItemStatus(request.status)
        except ValueError as exc:
            raise DataContractError("ReviewItem status transition is invalid") from exc
        if status not in {ReviewItemStatus.ACKNOWLEDGED, ReviewItemStatus.RESOLVED}:
            raise DataContractError("human ReviewItem transition must acknowledge or resolve")
        if request.actor not in {"user", "external_agent"}:
            raise DataContractError("ReviewItem actor must be user or external_agent")
        value = self._repository.transition(
            review_item_id=request.review_item_id,
            status=status,
            expected_version=request.expected_version,
            actor=request.actor,
            authorization_note=request.authorization_note,
            resolution_note=request.resolution_note,
            resolution_ref=request.resolution_ref,
            due_at=request.due_at,
            idempotency_key=request.idempotency_key,
            now=self._clock.now(),
        )
        return ReviewItemDTO.from_domain(value)
