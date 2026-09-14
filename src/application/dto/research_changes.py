"""Durable, version-pinned changes alongside a reviewed Decision."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaselineThesisDTO(_DTO):
    thesis_id: str
    revision_id: str
    statement: str
    assumptions: list[dict[str, str]] = Field(default_factory=list)
    invalidations: list[dict[str, str]] = Field(default_factory=list)


class BaselinePlanDTO(_DTO):
    plan_id: str
    version: int


class ResearchChangesBaselineDTO(_DTO):
    decision_id: str
    title: str
    rationale: str = ""
    external_note_revision_id: str | None = None
    recorded_at: AwareDatetime
    decided_at: AwareDatetime
    theses: list[BaselineThesisDTO]
    plan: BaselinePlanDTO | None


class ResearchChangeDTO(_DTO):
    change_id: str
    kind: Literal["OBSERVATION", "MONITOR", "AGENDA"]
    title: str
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    instrument_id: str | None
    source_id: str
    source_version: str | None
    previous_occurred_at: AwareDatetime | None = None
    previous_source_id: str | None = None
    source_names: list[str] = Field(default_factory=list)
    old_value: str | None
    new_value: str | None
    relation: Literal["EXACT_PLAN", "SUBJECT_SCOPE", "UNLINKED"]
    relation_detail: str
    thesis_id: str | None = None
    plan_id: str | None = None
    plan_version: int | None = None
    note_revision_id: str | None = None
    monitor_id: str | None = None
    event_id: str | None = None
    agenda_item_id: str | None = None
    warning_codes: list[str] = Field(default_factory=list)


class ResearchChangesDTO(_DTO):
    subject_id: str
    as_of: AwareDatetime
    baseline: ResearchChangesBaselineDTO | None
    coverage: dict[str, Literal["COMPLETE", "PARTIAL", "UNAVAILABLE", "NOT_APPLICABLE"]]
    warning_codes: list[str]
    total: int
    offset: int
    limit: int
    has_more: bool
    items: list[ResearchChangeDTO]
