"""Compact Challenge Review operation adapters."""

from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from application.dto.challenge import (
    ChallengeReviewGetInput,
    ChallengeReviewResolveInput,
    ChallengeReviewStartInput,
)
from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure


def build_challenge_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact Challenge Review operation adapters."""

    def challenge_review_start(
        case_id: str,
        trigger: str,
        proposed_action: str,
        related_candidate_id: str | None = None,
        related_evidence_ids: tuple[str, ...] = (),
        position_context_snapshot_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start a deterministic strict review, or bypass ordinary discussion."""
        try:
            request = ChallengeReviewStartInput.model_validate(
                {
                    "case_id": case_id,
                    "trigger": trigger,
                    "proposed_action": proposed_action,
                    "related_candidate_id": related_candidate_id,
                    "related_evidence_ids": related_evidence_ids,
                    "position_context_snapshot_id": position_context_snapshot_id,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.services.challenge.start(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    def challenge_review_get(review_id: str) -> dict[str, Any]:
        """Get one persisted Challenge Review."""
        try:
            request = ChallengeReviewGetInput.model_validate({"review_id": review_id})
            return container.services.challenge.get(request.review_id).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    def challenge_review_resolve(
        review_id: str,
        resolution: str,
        rationale: str,
        confirmed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record a user-confirmed non-executing Challenge Review resolution."""
        try:
            request = ChallengeReviewResolveInput.model_validate(
                {
                    "review_id": review_id,
                    "resolution": resolution,
                    "rationale": rationale,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.services.challenge.resolve(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return unexpected_failure(container, exc)

    return SimpleNamespace(
        challenge_review_start=challenge_review_start,
        challenge_review_get=challenge_review_get,
        challenge_review_resolve=challenge_review_resolve,
    )
