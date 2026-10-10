"""Closed reference-only contracts for reviewed evidence-to-assumption links."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.common.errors import DataContractError
from domain.research.evidence_impact import ResearchSourceRef


class ResearchSourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    kind: Literal["EVIDENCE", "OBSERVATION", "MONITOR"]
    source_id: str = Field(min_length=1, max_length=128)
    monitor_id: str | None = Field(default=None, min_length=1, max_length=128)
    rule_code: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        try:
            self.to_domain()
        except DataContractError:
            raise ValueError("Invalid source reference identifiers") from None
        return self

    def to_domain(self) -> ResearchSourceRef:
        return ResearchSourceRef(self.kind, self.source_id, self.monitor_id, self.rule_code)


class EvidenceImpactCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["evidence_impact"] = "evidence_impact"
    action: Literal["create", "revise", "retract"]
    impact_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    thesis_revision_id: str | None = Field(default=None, min_length=1, max_length=128)
    assumption_id: str | None = Field(default=None, min_length=1, max_length=128)
    source: ResearchSourcePayload | None = None
    stance: Literal["supports", "contradicts", "uncertain"] = "uncertain"
    rationale: str = Field(min_length=1, max_length=4000)

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A review rationale is required")
        return value.strip()

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action == "create":
            if self.impact_id is not None or self.expected_version is not None:
                raise ValueError("create must not set an existing impact/version")
        elif self.impact_id is None or self.expected_version is None:
            raise ValueError("revise/retract requires an exact impact/version")
        if self.action in {"create", "revise"}:
            if self.thesis_revision_id is None or self.assumption_id is None or self.source is None:
                raise ValueError("An exact source, Thesis revision and assumption are required")
        elif (
            any(
                value is not None
                for value in (
                    self.thesis_revision_id,
                    self.assumption_id,
                    self.source,
                )
            )
            or self.stance != "uncertain"
        ):
            raise ValueError("retract cannot change the prior target or source")
        return self


class EvidenceImpactDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    impact_id: str
    version: int
    subject_id: str
    thesis_id: str
    thesis_revision_id: str
    assumption_id: str
    source: ResearchSourcePayload
    stance: str
    rationale: str
    state: str
    confirmed_at: AwareDatetime
    confirmed_by: str
    candidate_id: str
