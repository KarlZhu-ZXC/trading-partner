"""Typed, source-safe DTOs for historical Decision replay.

The replay projection deliberately has a smaller surface than the current
research state.  In particular, a question exposes only its immutable
existence fields and an assumption may have an unknown status when the only
available row records a retirement after the replay cutoff.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from application.dto.research_links import ResearchSourcePayload
from domain.common.enums import (
    AssumptionStatus,
    ConfidenceBand,
    ConfirmationMode,
    InvestmentRating,
)

CoverageStatus = Literal["COMPLETE", "PARTIAL", "UNAVAILABLE", "NOT_APPLICABLE"]
ReplayWarningCode = Literal[
    "REVISION_OUTPUT_CAP_REACHED",
    "REVISION_UNAVAILABLE",
    "REVISION_SCOPE_INVALID",
    "REVISION_AFTER_CUTOFF",
    "ASSUMPTIONS_UNAVAILABLE",
    "ASSUMPTION_SCOPE_INVALID",
    "ASSUMPTION_UNAVAILABLE",
    "ASSUMPTION_AFTER_CUTOFF",
    "ASSUMPTION_OUTPUT_CAP_REACHED",
    "ASSUMPTION_STATUS_UNAVAILABLE",
    "IMPACTS_UNAVAILABLE",
    "IMPACT_SCOPE_INVALID",
    "IMPACT_UNAVAILABLE",
    "IMPACT_HISTORY_PARTIAL",
    "IMPACT_REVISION_UNAVAILABLE",
    "IMPACT_ASSUMPTION_UNAVAILABLE",
    "SOURCE_OUTPUT_CAP_REACHED",
    "SOURCE_REFERENCE_INVALID",
    "SOURCES_UNAVAILABLE",
    "SOURCE_NOT_AVAILABLE_AT_CUTOFF",
    "SOURCE_REFERENCE_MISMATCH",
    "SOURCE_AFTER_CUTOFF",
    "OBSERVATION_SCOPE_UNAVAILABLE",
    "QUESTION_SCOPE_INVALID",
    "QUESTIONS_UNAVAILABLE",
    "QUESTION_UNAVAILABLE",
    "QUESTION_AFTER_CUTOFF",
    "QUESTION_OUTPUT_CAP_REACHED",
    "QUESTION_STATE_HISTORY_UNAVAILABLE",
]


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class DecisionReplaySourceDTO(_DTO):
    """Metadata for one source visible at the Decision's historical cutoff."""

    source: ResearchSourcePayload
    title: str
    fact_at: AwareDatetime | None
    recorded_at: AwareDatetime
    origin: str
    warning_codes: list[str] = Field(default_factory=list)
    source_name: str | None = None
    source_url: str | None = None


class DecisionReplayAssumptionDTO(_DTO):
    assumption_id: str
    thesis_id: str
    subject_id: str
    revision_no: int
    statement: str
    basis: str
    falsifiability: str
    # A later retirement is intentionally not projected as a made-up prior
    # status.  None means that the durable current row cannot reconstruct the
    # pre-retirement status at this cutoff.
    status: AssumptionStatus | None
    proposed_at: AwareDatetime
    confirmed_at: AwareDatetime
    proposed_by: str
    confirmed_by: str
    retired_at: AwareDatetime | None
    retired_reason: str | None


class DecisionReplayRevisionDTO(_DTO):
    """Immutable ThesisRevision fields plus assumptions visible at cutoff."""

    revision_id: str
    thesis_id: str
    subject_id: str
    revision_no: int
    supersedes_revision_no: int | None
    statement: str
    rationale: str
    confidence_band: ConfidenceBand
    rating: InvestmentRating
    confirmation_mode: ConfirmationMode
    proposed_by: str
    confirmed_by: str
    proposed_at: AwareDatetime
    confirmed_at: AwareDatetime
    observation_window_start: date | None
    observation_window_end: date | None
    invalidation_check_note: str
    schema_version: int
    assumptions: list[DecisionReplayAssumptionDTO] = Field(default_factory=list)


class DecisionReplayImpactDTO(_DTO):
    """An ACTIVE impact version visible before the replay cutoff."""

    impact_id: str
    version: int
    subject_id: str
    thesis_id: str
    thesis_revision_id: str
    assumption_id: str
    source: ResearchSourcePayload
    stance: Literal["supports", "contradicts", "uncertain"]
    rationale: str
    state: Literal["ACTIVE"]
    confirmed_at: AwareDatetime
    confirmed_by: str
    candidate_id: str
    source_available: bool
    source_snapshot: DecisionReplaySourceDTO | None = None


class DecisionReplayQuestionDTO(_DTO):
    """The question as it existed, without leaking its later mutable state."""

    question_id: str
    text: str
    asked_at: AwareDatetime
    history_availability: Literal["UNAVAILABLE"] = "UNAVAILABLE"


class DecisionReplayExcludedReferenceDTO(_DTO):
    reference_id: str
    reason: str


class DecisionReplayDTO(_DTO):
    subject_id: str
    decision_id: str
    cutoff: AwareDatetime
    recorded_at: AwareDatetime
    title: str
    rationale: str
    generated_at: AwareDatetime
    revisions: list[DecisionReplayRevisionDTO] = Field(default_factory=list)
    sources: list[DecisionReplaySourceDTO] = Field(default_factory=list)
    impacts: list[DecisionReplayImpactDTO] = Field(default_factory=list)
    questions: list[DecisionReplayQuestionDTO] = Field(default_factory=list)
    coverage: dict[str, CoverageStatus]
    warnings: list[ReplayWarningCode] = Field(default_factory=list)
    excluded_references: list[DecisionReplayExcludedReferenceDTO] = Field(default_factory=list)


__all__ = [
    "CoverageStatus",
    "ReplayWarningCode",
    "DecisionReplayAssumptionDTO",
    "DecisionReplayDTO",
    "DecisionReplayExcludedReferenceDTO",
    "DecisionReplayImpactDTO",
    "DecisionReplayQuestionDTO",
    "DecisionReplayRevisionDTO",
    "DecisionReplaySourceDTO",
]
