"""Closed Phase 1K Challenge Review DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.challenge.enums import (
    ChallengeDimension,
    ChallengeFindingSeverity,
    ChallengeResolution,
    ChallengeReviewStatus,
    ChallengeTrigger,
)
from domain.challenge.models import ChallengeReview
from domain.common.enums import ConfirmationMode


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class ChallengeReviewStartInput(_DTO):
    case_id: str = Field(min_length=1, max_length=128)
    trigger: ChallengeTrigger
    proposed_action: str = Field(min_length=1, max_length=4_000)
    related_candidate_id: str | None = Field(default=None, max_length=128)
    related_evidence_ids: tuple[str, ...] = ()
    position_context_snapshot_id: str | None = Field(default=None, max_length=128)


class ChallengeReviewGetInput(_DTO):
    review_id: str = Field(min_length=1, max_length=128)


class ChallengeReviewResolveInput(_DTO):
    review_id: str = Field(min_length=1, max_length=128)
    resolution: ChallengeResolution
    rationale: str = Field(min_length=1, max_length=4_000)
    confirmed_by: str = Field(min_length=1, max_length=128)


class ChallengeQuestionDTO(_DTO):
    question_id: str
    dimension: ChallengeDimension
    prompt: str
    ordinal: int
    question_set_version: str


class ChallengeFindingDTO(_DTO):
    finding_id: str
    dimension: ChallengeDimension
    severity: ChallengeFindingSeverity
    summary: str
    evidence_ids: tuple[str, ...]


class ChallengeReviewDTO(_DTO):
    review_id: str
    case_id: str
    mode: ConfirmationMode
    trigger: ChallengeTrigger
    proposed_action: str
    related_candidate_id: str | None
    related_evidence_ids: tuple[str, ...]
    position_context_snapshot_id: str | None
    context_as_of: datetime
    status: ChallengeReviewStatus
    questions: tuple[ChallengeQuestionDTO, ...]
    findings: tuple[ChallengeFindingDTO, ...]
    created_at: datetime
    resolution: ChallengeResolution | None
    resolution_rationale: str | None
    resolved_at: datetime | None
    confirmed_by: str | None
    execution_effect: Literal[False]

    @classmethod
    def from_domain(cls, value: ChallengeReview) -> ChallengeReviewDTO:
        return cls.model_validate(value)


class ChallengeReviewStartDTO(_DTO):
    mode: ConfirmationMode
    persisted: bool
    review: ChallengeReviewDTO | None
