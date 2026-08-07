"""Immutable Challenge Review aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.challenge.enums import (
    ChallengeDimension,
    ChallengeFindingSeverity,
    ChallengeResolution,
    ChallengeReviewStatus,
    ChallengeTrigger,
)
from domain.common.enums import ConfirmationMode
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime


def _text(value: str, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DataContractError(f"{field} must be a bounded nonblank string")


@dataclass(frozen=True, slots=True)
class ChallengeQuestion:
    question_id: str
    review_id: str
    dimension: ChallengeDimension
    prompt: str
    ordinal: int
    question_set_version: str = "challenge_questions_v1"

    def __post_init__(self) -> None:
        _text(self.question_id, "question_id", 128)
        _text(self.review_id, "review_id", 128)
        if not isinstance(self.dimension, ChallengeDimension):
            raise DataContractError("dimension must be ChallengeDimension")
        _text(self.prompt, "prompt", 1_000)
        if not 1 <= self.ordinal <= 10:
            raise DataContractError("ordinal must be in [1,10]")
        _text(self.question_set_version, "question_set_version", 64)


@dataclass(frozen=True, slots=True)
class ChallengeFinding:
    finding_id: str
    review_id: str
    dimension: ChallengeDimension
    severity: ChallengeFindingSeverity
    summary: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.finding_id, "finding_id", 128)
        _text(self.review_id, "review_id", 128)
        if not isinstance(self.dimension, ChallengeDimension) or not isinstance(
            self.severity, ChallengeFindingSeverity
        ):
            raise DataContractError("finding enums are invalid")
        _text(self.summary, "summary", 2_000)
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise DataContractError("evidence_ids must be unique")


@dataclass(frozen=True, slots=True)
class ChallengeReview:
    review_id: str
    subject_id: str
    mode: ConfirmationMode
    trigger: ChallengeTrigger
    proposed_action: str
    related_candidate_id: str | None
    related_evidence_ids: tuple[str, ...]
    position_context_snapshot_id: str | None
    context_as_of: datetime
    status: ChallengeReviewStatus
    questions: tuple[ChallengeQuestion, ...]
    findings: tuple[ChallengeFinding, ...]
    created_at: datetime
    resolution: ChallengeResolution | None = None
    resolution_rationale: str | None = None
    resolved_at: datetime | None = None
    confirmed_by: str | None = None
    execution_effect: bool = False

    def __post_init__(self) -> None:
        _text(self.review_id, "review_id", 128)
        _text(self.subject_id, "subject_id", 128)
        if self.mode is not ConfirmationMode.STRICT_REVIEW:
            raise DataContractError("persistent ChallengeReview must be strict_review")
        if (
            not isinstance(self.trigger, ChallengeTrigger)
            or self.trigger is ChallengeTrigger.DISCUSSION
        ):
            raise DataContractError("persistent review requires a material trigger")
        _text(self.proposed_action, "proposed_action", 4_000)
        require_aware_datetime(self.context_as_of, field_name="context_as_of")
        require_aware_datetime(self.created_at, field_name="created_at")
        if len(self.questions) != 10 or {item.ordinal for item in self.questions} != set(
            range(1, 11)
        ):
            raise DataContractError("strict review requires exactly ten ordered questions")
        if any(item.review_id != self.review_id for item in self.questions + self.findings):
            raise DataContractError("review children must reference the aggregate")
        if self.execution_effect is not False:
            raise DataContractError("challenge review must not execute")
        if self.status is ChallengeReviewStatus.OPEN:
            if any(
                value is not None
                for value in (
                    self.resolution,
                    self.resolution_rationale,
                    self.resolved_at,
                    self.confirmed_by,
                )
            ):
                raise DataContractError("open review must not contain resolution fields")
        elif self.status is ChallengeReviewStatus.RESOLVED:
            if not isinstance(self.resolution, ChallengeResolution):
                raise DataContractError("resolved review requires resolution")
            if self.resolution_rationale is None or self.confirmed_by is None:
                raise DataContractError("resolved review requires rationale and confirmer")
            _text(self.resolution_rationale, "resolution_rationale", 4_000)
            _text(self.confirmed_by, "confirmed_by", 128)
            if self.resolved_at is None:
                raise DataContractError("resolved review requires resolved_at")
            require_aware_datetime(self.resolved_at, field_name="resolved_at")
