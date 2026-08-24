"""Application service for append-only cross-period behavior reviews."""

from __future__ import annotations

from application.dto.behavior_review import (
    BehaviorReviewRunDTO,
    BehaviorReviewRunInput,
)
from application.ports.behavior_review_repository import BehaviorReviewRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from domain.behavior_review.calculator import BehaviorReviewRunCalculator
from domain.behavior_review.enums import BehaviorActionStatus
from domain.common.errors import IdempotencyConflict
from domain.common.ids import EntityIdPrefix


class BehaviorReviewService:
    def __init__(
        self,
        repository: BehaviorReviewRepository,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = id_generator
        self._calculator = BehaviorReviewRunCalculator()

    def run(self, request: BehaviorReviewRunInput) -> BehaviorReviewRunDTO:
        existing = self._repository.get_run_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            if not self._same_request(existing, request):
                raise IdempotencyConflict("Behavior Review idempotency key was reused")
            return BehaviorReviewRunDTO.from_domain(existing)
        raw_id = self._ids.new(EntityIdPrefix.RUN)
        token = raw_id.split("_", 1)[1] if "_" in raw_id else raw_id
        run_id = f"behavior_review_{token}"
        cohort = request.cohort()
        prior = self._repository.list_action_observations()
        value = self._calculator.calculate(
            run_id=run_id,
            cohort=cohort,
            generated_at=self._clock.now(),
            current_actions=tuple(item.to_domain() for item in request.action_items),
            prior_observations=prior,
            source_read_complete=request.source_read_complete,
            source_error_code=request.source_error_code,
            idempotency_key=request.idempotency_key,
        )
        return BehaviorReviewRunDTO.from_domain(self._repository.append_run(value))

    def get(self, run_id: str) -> BehaviorReviewRunDTO | None:
        value = self._repository.get_run(run_id)
        return BehaviorReviewRunDTO.from_domain(value) if value is not None else None

    def history(self, *, limit: int = 50) -> tuple[BehaviorReviewRunDTO, ...]:
        return tuple(
            BehaviorReviewRunDTO.from_domain(item)
            for item in self._repository.list_runs(limit=limit)
        )

    @staticmethod
    def _same_request(value: object, request: BehaviorReviewRunInput) -> bool:
        # The durable repository already compares the complete immutable row
        # on replay.  This narrow pre-check prevents generating a new run ID
        # when a caller retries the exact idempotency key.
        existing_actions = {
            item.stable_key
            for item in getattr(value, "action_observations", ())
            if item.status is not BehaviorActionStatus.RESOLVED
        }
        request_actions = {item.to_domain().stable_key for item in request.action_items}
        return bool(
            getattr(value, "idempotency_key", None) == request.idempotency_key
            and getattr(value, "cohort", None) == request.cohort()
            and getattr(value, "source_read_complete", None) == request.source_read_complete
            and getattr(value, "source_error_code", None) == request.source_error_code
            and existing_actions == request_actions
        )


__all__ = ["BehaviorReviewService"]
