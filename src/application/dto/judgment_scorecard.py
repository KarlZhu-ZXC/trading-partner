"""Closed application DTOs for Judgment Scorecard S0/S1 runs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from domain.scorecard.models import (
    JudgmentScorecardRun,
    ScorecardDimension,
    ScorecardSourceRef,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class JudgmentScorecardHistoryInput(_DTO):
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    thesis_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class ScorecardSourceRefDTO(_DTO):
    kind: str
    entity_id: str
    version: int | None

    @classmethod
    def from_domain(cls, value: ScorecardSourceRef) -> ScorecardSourceRefDTO:
        return cls.model_validate(value)


class ScorecardDimensionDTO(_DTO):
    code: str
    status: str
    result_code: str
    title: str
    summary: str
    facts: tuple[tuple[str, str], ...]
    source_refs: tuple[ScorecardSourceRefDTO, ...]
    limitation_codes: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: ScorecardDimension) -> ScorecardDimensionDTO:
        return cls(
            code=value.code,
            status=value.status.value,
            result_code=value.result_code,
            title=value.title,
            summary=value.summary,
            facts=value.facts,
            source_refs=tuple(
                ScorecardSourceRefDTO.from_domain(item) for item in value.source_refs
            ),
            limitation_codes=value.limitation_codes,
        )


class JudgmentScorecardRunDTO(_DTO):
    scorecard_id: str
    subject_id: str
    subject_title: str
    thesis_id: str
    thesis_title: str
    thesis_revision_id: str
    thesis_revision_no: int
    generated_at: datetime
    status: str
    dimensions: tuple[ScorecardDimensionDTO, ...]
    warning_codes: tuple[str, ...]
    input_fingerprint: str
    idempotency_key: str
    algorithm_version: str
    schema_version: int
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: JudgmentScorecardRun) -> JudgmentScorecardRunDTO:
        return cls(
            scorecard_id=value.scorecard_id,
            subject_id=value.subject_id,
            subject_title=value.subject_title,
            thesis_id=value.thesis_id,
            thesis_title=value.thesis_title,
            thesis_revision_id=value.thesis_revision_id,
            thesis_revision_no=value.thesis_revision_no,
            generated_at=value.generated_at,
            status=value.status.value,
            dimensions=tuple(ScorecardDimensionDTO.from_domain(item) for item in value.dimensions),
            warning_codes=value.warning_codes,
            input_fingerprint=value.input_fingerprint,
            idempotency_key=value.idempotency_key,
            algorithm_version=value.algorithm_version,
            schema_version=value.schema_version,
            execution_effect=value.execution_effect,
        )


class JudgmentScorecardHistoryDTO(_DTO):
    runs: tuple[JudgmentScorecardRunDTO, ...]
    total: int
    has_more: bool
