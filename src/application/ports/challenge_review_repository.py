"""Challenge Review persistence port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.challenge.enums import ChallengeResolution
from domain.challenge.models import ChallengeReview


class ChallengeReviewRepository(Protocol):
    def append(
        self,
        review: ChallengeReview,
        *,
        idempotency_key: str,
        payload_sha256: str,
    ) -> ChallengeReview: ...

    def get(self, review_id: str) -> ChallengeReview: ...

    def get_by_start_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ChallengeReview, str] | None: ...

    def get_by_resolution_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[ChallengeReview, str] | None: ...

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
    ) -> ChallengeReview: ...
