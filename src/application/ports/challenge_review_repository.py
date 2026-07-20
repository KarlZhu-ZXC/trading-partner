"""Challenge Review persistence port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.challenge.enums import ChallengeResolution
from domain.challenge.models import ChallengeReview


class ChallengeReviewRepository(Protocol):
    def append(self, review: ChallengeReview) -> ChallengeReview: ...

    def get(self, review_id: str) -> ChallengeReview: ...

    def resolve(
        self,
        review_id: str,
        *,
        resolution: ChallengeResolution,
        rationale: str,
        confirmed_by: str,
        resolved_at: datetime,
    ) -> ChallengeReview: ...
