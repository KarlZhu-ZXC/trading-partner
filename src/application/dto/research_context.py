"""Closed DTOs for deterministic cross-thread research context."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.market import DecimalWire
from application.dto.research import ResearchStateDTO, ResearchSubjectDTO
from domain.common.enums import (
    ConfirmationMode,
    DecisionType,
    EvidenceStance,
    JournalEntryType,
    ResearchEventType,
    ResearchReportType,
    VendorId,
)
from domain.common.time import require_aware_datetime
from domain.common.values import parse_instrument_id
from domain.portfolio.enums import AccountPositionSide


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class ResearchContextBuildInput(_DTO):
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    instrument_id: str | None = None
    since: datetime | None = None
    token_budget: int = Field(default=4_000, ge=2_000, le=12_000)

    @field_validator("instrument_id")
    @classmethod
    def instrument(cls, value: str | None) -> str | None:
        if value is not None:
            parse_instrument_id(value)
        return value

    @field_validator("since")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            require_aware_datetime(value, field_name="since")
        return value

    @model_validator(mode="after")
    def one_selector(self) -> Self:
        if (self.subject_id is None) == (self.instrument_id is None):
            raise ValueError("exactly one of subject_id or instrument_id is required")
        return self


class ContextEvidenceDTO(_DTO):
    evidence_id: str
    title: str
    summary: str
    source_name: str
    observed_at: datetime
    stances: tuple[EvidenceStance, ...]
    materiality: DecimalWire | None
    assessment_rationales: tuple[str, ...]


class ContextReportDTO(_DTO):
    report_id: str
    report_type: ResearchReportType
    title: str
    summary: str
    as_of: datetime


class ContextEventDTO(_DTO):
    event_id: str
    event_type: ResearchEventType
    title: str
    summary: str
    occurred_at: datetime


class ContextDecisionDTO(_DTO):
    decision_id: str
    decision_type: DecisionType
    title: str
    rationale: str
    decided_at: datetime
    confirmation_mode: ConfirmationMode
    execution_effect: Literal[False] = False


class ContextJournalDTO(_DTO):
    journal_id: str
    entry_type: JournalEntryType
    title: str
    body: str
    created_at: datetime


class ContextPositionDTO(_DTO):
    snapshot_id: str
    account_ref: str
    provider: VendorId
    account_as_of: datetime
    instrument_id: str
    side: AccountPositionSide
    quantity: DecimalWire
    market_value: DecimalWire | None
    currency: str


class ContextBudgetDTO(_DTO):
    requested_tokens: int
    estimated_tokens: int
    truncated: bool
    truncated_collections: tuple[str, ...]


class ResearchContextDTO(_DTO):
    subject: ResearchSubjectDTO
    research_state: ResearchStateDTO
    evidence: tuple[ContextEvidenceDTO, ...]
    reports: tuple[ContextReportDTO, ...]
    events: tuple[ContextEventDTO, ...]
    decisions: tuple[ContextDecisionDTO, ...]
    journals: tuple[ContextJournalDTO, ...]
    positions: tuple[ContextPositionDTO, ...]
    conflicts: tuple[str, ...]
    missing_information: tuple[str, ...]
    degraded_sources: tuple[str, ...]
    live_fact_tools_required: tuple[str, ...]
    budget: ContextBudgetDTO
