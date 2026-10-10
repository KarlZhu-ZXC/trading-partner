"""Reference-only question binding carried by durable Research receipts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.research_links import ResearchSourcePayload


class QuestionResearchContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject_id: str = Field(min_length=1, max_length=128)
    question_id: str = Field(min_length=1, max_length=128)
    question_version: int = Field(ge=1)
    question_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_cutoff: str = Field(max_length=40)
    thesis_revision_id: str | None = Field(default=None, min_length=1, max_length=128)
    assumption_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_refs: tuple[ResearchSourcePayload, ...] = Field(default=(), max_length=8)
    source_coverage: str = Field(pattern=r"^(COMPLETE|PARTIAL|UNAVAILABLE)$")

    @field_validator("evidence_cutoff")
    @classmethod
    def aware_cutoff(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Evidence cutoff must be timezone-aware")
        return parsed.isoformat()

    @model_validator(mode="after")
    def target_pair(self) -> "QuestionResearchContext":
        if (self.thesis_revision_id is None) != (self.assumption_id is None):
            raise ValueError("An exact revision and assumption must be selected together")
        return self
