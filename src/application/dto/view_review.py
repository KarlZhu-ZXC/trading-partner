"""Deterministic review package for one exact external Observation revision."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from application.dto.external_note_review import ExternalNoteReviewDTO


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ViewScenarioDTO(_DTO):
    scenario: str
    action: str
    condition: str
    confirmation: str
    loss_boundary: str


class ExternalViewpointDTO(_DTO):
    speaker_label: str
    summary: str
    direction: str


class ViewThesisBaselineDTO(_DTO):
    thesis_id: str
    revision_id: str
    title: str
    statement: str
    status: str
    rating: str
    confidence_band: str


class ViewPlanBaselineDTO(_DTO):
    plan_id: str
    version: int
    status: str
    instrument_id: str


class ViewDecisionBaselineDTO(_DTO):
    decision_id: str
    decision_type: str
    title: str
    rationale: str
    decided_at: datetime
    external_note_revision_id: str | None


class ViewMonitorBaselineDTO(_DTO):
    monitor_id: str
    version: int
    name: str
    status: str


class ViewPositionContextDTO(_DTO):
    account_ref: str
    provider: str
    instrument_id: str
    quantity: Decimal
    currency: str
    account_as_of: datetime


class ViewReviewPackageDTO(_DTO):
    review: ExternalNoteReviewDTO
    note_id: str
    note_revision_id: str
    note_version: int
    source: str
    title: str
    instrument_id: str | None
    observed_at: datetime
    change_relation: str
    material_change_summary: str
    user_scenarios: tuple[ViewScenarioDTO, ...]
    external_viewpoints: tuple[ExternalViewpointDTO, ...]
    contradictions: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    suggested_next_step: str
    subject_id: str | None
    subject_title: str | None
    subject_status: str | None
    thesis: ViewThesisBaselineDTO | None
    trade_plan: ViewPlanBaselineDTO | None
    latest_decision: ViewDecisionBaselineDTO | None
    monitors: tuple[ViewMonitorBaselineDTO, ...]
    positions: tuple[ViewPositionContextDTO, ...]
    deterministic_flags: tuple[str, ...]
    coverage: dict[str, str]
    allowed_actions: tuple[str, ...]


class CurrentViewDTO(_DTO):
    """Derived current view; every field points back to confirmed durable records."""

    subject_id: str
    subject_title: str
    subject_status: str
    instrument_id: str | None
    source_note_revision_id: str
    review: ExternalNoteReviewDTO
    decision: ViewDecisionBaselineDTO
    thesis: ViewThesisBaselineDTO | None
    trade_plan: ViewPlanBaselineDTO | None
    coverage: dict[str, str]


class ViewInboxItemDTO(_DTO):
    review: ExternalNoteReviewDTO
    note_revision_id: str
    note_version: int
    title: str
    instrument_id: str | None
    observed_at: datetime
    change_relation: str
    material_change_summary: str
    suggested_next_step: str
    subject_id: str | None
    allowed_actions: tuple[str, ...]


class ViewInboxDTO(_DTO):
    items: tuple[ViewInboxItemDTO, ...]
    returned_count: int
    has_more: bool
