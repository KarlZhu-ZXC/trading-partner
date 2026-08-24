"""Pure recurrence calculator for cross-period Behavior Review Runs."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from domain.behavior_review.enums import BehaviorActionStatus, BehaviorReviewRunStatus
from domain.behavior_review.models import (
    BEHAVIOR_REVIEW_ALGORITHM_VERSION,
    BehaviorActionInput,
    BehaviorActionObservation,
    BehaviorReviewCohort,
    BehaviorReviewRun,
)
from domain.common.errors import DataContractError


def _observation_id(run_id: str, stable_key: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{stable_key}".encode()).hexdigest()
    return f"behavior_action_observation_{digest}"


def _warning_codes(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


class BehaviorReviewRunCalculator:
    """Derive NEW/PERSISTENT/RESOLVED/RECURRED without mutating history.

    The caller must state whether the current action source was completely
    observed.  A failed or bounded source read never emits RESOLVED
    observations for actions that disappeared from the current input.
    """

    algorithm_version = BEHAVIOR_REVIEW_ALGORITHM_VERSION

    def calculate(
        self,
        *,
        run_id: str,
        cohort: BehaviorReviewCohort,
        generated_at: datetime,
        current_actions: tuple[BehaviorActionInput, ...],
        prior_observations: tuple[BehaviorActionObservation, ...] = (),
        source_read_complete: bool = True,
        source_error_code: str | None = None,
        idempotency_key: str,
    ) -> BehaviorReviewRun:
        if not run_id.startswith("behavior_review_"):
            raise DataContractError("run_id has an invalid prefix")
        if not idempotency_key.strip():
            raise DataContractError("idempotency_key must be non-blank")

        deduped = self._dedupe_actions(current_actions)
        prior = self._latest_prior(
            tuple(item for item in prior_observations if item.observed_at < generated_at)
        )
        observations: list[BehaviorActionObservation] = []
        current_keys = set(deduped)
        for stable_key in sorted(deduped):
            action = deduped[stable_key]
            previous = prior.get(stable_key)
            if previous is None:
                status = BehaviorActionStatus.NEW
                occurrence_count = 1
            elif previous.status is BehaviorActionStatus.RESOLVED:
                status = BehaviorActionStatus.RECURRED
                occurrence_count = previous.occurrence_count + 1
            else:
                status = BehaviorActionStatus.PERSISTENT
                occurrence_count = previous.occurrence_count + 1
            observations.append(
                BehaviorActionObservation(
                    observation_id=_observation_id(run_id, stable_key),
                    run_id=run_id,
                    stable_key=stable_key,
                    action_text=action.action_text,
                    action_code=action.action_code,
                    status=status,
                    occurrence_count=occurrence_count,
                    period_key=cohort.period_key,
                    cohort_key=cohort.cohort_key,
                    review_item_source_keys=action.review_item_source_keys,
                    retro_review_ids=action.retro_review_ids,
                    cycle_ids=action.cycle_ids,
                    decision_ids=action.decision_ids,
                    observed_at=generated_at,
                    previous_observation_id=(
                        previous.observation_id if previous is not None else None
                    ),
                )
            )

        if source_read_complete:
            for stable_key, previous in sorted(prior.items()):
                if stable_key in current_keys or previous.status is BehaviorActionStatus.RESOLVED:
                    continue
                observations.append(
                    BehaviorActionObservation(
                        observation_id=_observation_id(run_id, stable_key),
                        run_id=run_id,
                        stable_key=stable_key,
                        action_text=previous.action_text,
                        action_code=previous.action_code,
                        status=BehaviorActionStatus.RESOLVED,
                        occurrence_count=previous.occurrence_count,
                        period_key=cohort.period_key,
                        cohort_key=cohort.cohort_key,
                        review_item_source_keys=previous.review_item_source_keys,
                        retro_review_ids=previous.retro_review_ids,
                        cycle_ids=previous.cycle_ids,
                        decision_ids=previous.decision_ids,
                        observed_at=generated_at,
                        previous_observation_id=previous.observation_id,
                        resolved_at=generated_at,
                        resolution_note=(
                            "The complete durable action source no longer reports this action."
                        ),
                    )
                )

        warnings: list[str] = []
        if not source_read_complete:
            warnings.append(source_error_code or "BEHAVIOR_ACTION_SOURCE_READ_INCOMPLETE")
        run_status = (
            BehaviorReviewRunStatus.COMPLETE
            if source_read_complete
            else BehaviorReviewRunStatus.INCOMPLETE
        )
        return BehaviorReviewRun(
            run_id=run_id,
            cohort=cohort,
            generated_at=generated_at,
            status=run_status,
            source_read_complete=source_read_complete,
            action_observations=tuple(
                sorted(observations, key=lambda item: item.stable_key)
            ),
            warning_codes=_warning_codes(warnings),
            idempotency_key=idempotency_key,
            source_error_code=source_error_code,
            algorithm_version=self.algorithm_version,
        )

    build = calculate

    @staticmethod
    def _dedupe_actions(
        actions: tuple[BehaviorActionInput, ...],
    ) -> dict[str, BehaviorActionInput]:
        grouped: dict[str, list[BehaviorActionInput]] = defaultdict(list)
        for action in actions:
            grouped[action.stable_key].append(action)
        result: dict[str, BehaviorActionInput] = {}
        for stable_key, values in grouped.items():
            ordered = sorted(
                values,
                key=lambda item: (
                    item.action_code or "",
                    item.action_text.casefold(),
                    item.review_item_source_keys,
                    item.retro_review_ids,
                    item.cycle_ids,
                    item.decision_ids,
                ),
            )
            first = ordered[0]
            result[stable_key] = BehaviorActionInput(
                action_text=first.action_text,
                action_code=first.action_code,
                review_item_source_keys=tuple(
                    sorted(
                        {
                            key
                            for item in values
                            for key in item.review_item_source_keys
                        }
                    )
                ),
                retro_review_ids=tuple(
                    sorted(
                        {
                            key for item in values for key in item.retro_review_ids
                        }
                    )
                ),
                cycle_ids=tuple(
                    sorted({key for item in values for key in item.cycle_ids})
                ),
                decision_ids=tuple(
                    sorted({key for item in values for key in item.decision_ids})
                ),
            )
        return result

    @staticmethod
    def _latest_prior(
        observations: tuple[BehaviorActionObservation, ...],
    ) -> dict[str, BehaviorActionObservation]:
        latest: dict[str, BehaviorActionObservation] = {}
        for observation in observations:
            current = latest.get(observation.stable_key)
            if current is None or (
                observation.observed_at,
                observation.occurrence_count,
                observation.observation_id,
            ) > (
                current.observed_at,
                current.occurrence_count,
                current.observation_id,
            ):
                latest[observation.stable_key] = observation
        return latest


BehaviorReviewCalculator = BehaviorReviewRunCalculator


__all__ = ["BehaviorReviewCalculator", "BehaviorReviewRunCalculator"]
