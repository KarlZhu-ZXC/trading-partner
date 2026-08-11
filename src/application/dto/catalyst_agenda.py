"""Closed application DTOs for Catalyst Agenda C0-C3."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.catalyst_agenda.enums import (
    AgendaDateCertainty,
    AgendaItemKind,
    AgendaItemStatus,
    AgendaScopeReason,
    AgendaSourceType,
)
from domain.catalyst_agenda.models import CatalystAgendaVersion


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class AgendaMutationAction(StrEnum):
    CREATE = "CREATE"
    REVISE = "REVISE"
    CANCEL = "CANCEL"
    LINK_OUTCOME = "LINK_OUTCOME"


class AgendaUpsertPayload(_DTO):
    instrument_id: str | None = Field(default=None, min_length=1, max_length=256)
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    kind: AgendaItemKind
    title: str = Field(min_length=1, max_length=300)
    fiscal_period: str | None = Field(default=None, min_length=1, max_length=100)
    upstream_event_key: str | None = Field(default=None, min_length=1, max_length=300)
    window_start: datetime | None = None
    window_end: datetime | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    date_certainty: AgendaDateCertainty
    source_reference: str | None = Field(default=None, min_length=1, max_length=1_000)
    source_visible_at: datetime | None = None
    last_verified_at: datetime | None = None
    expected_question: str | None = Field(default=None, min_length=1, max_length=2_000)
    revision_note: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_scope_and_window(self) -> AgendaUpsertPayload:
        if (
            self.instrument_id is None
            and self.subject_id is None
            and self.kind not in {AgendaItemKind.MACRO_RELEASE, AgendaItemKind.POLICY}
        ):
            raise ValueError(
                "only MACRO_RELEASE or POLICY may omit instrument_id and subject_id"
            )
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("window_start and window_end must be supplied together")
        if self.window_start is None and self.date_certainty is not AgendaDateCertainty.UNKNOWN:
            raise ValueError("only UNKNOWN date certainty may omit the event window")
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end < self.window_start
        ):
            raise ValueError("window_end must be >= window_start")
        return self


class AgendaCancelPayload(_DTO):
    cancellation_reason: str = Field(min_length=1, max_length=1_000)
    source_visible_at: datetime | None = None
    last_verified_at: datetime | None = None


class AgendaOutcomeLinkPayload(_DTO):
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    report_id: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_id: str | None = Field(default=None, min_length=1, max_length=128)
    outcome_occurred_at: AwareDatetime | None = None
    outcome_note: str = Field(min_length=1, max_length=2_000)

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("event_"):
            raise ValueError("event_id must use event_ prefix")
        return value

    @field_validator("report_id")
    @classmethod
    def validate_report_id(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("report_"):
            raise ValueError("report_id must use report_ prefix")
        return value

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("evidence_"):
            raise ValueError("evidence_id must use evidence_ prefix")
        return value

    @model_validator(mode="after")
    def require_durable_outcome(self) -> AgendaOutcomeLinkPayload:
        if self.event_id is None and self.report_id is None and self.evidence_id is None:
            raise ValueError("event_id, report_id, or evidence_id is required")
        if self.event_id is None and self.outcome_occurred_at is None:
            raise ValueError("outcome_occurred_at is required when event_id is absent")
        return self


class AgendaMutationInput(_DTO):
    action: AgendaMutationAction
    agenda_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    confirmed_by: Literal["user", "external_agent"]
    authorization_note: str = Field(min_length=1, max_length=1_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload: AgendaUpsertPayload | AgendaCancelPayload | AgendaOutcomeLinkPayload

    @model_validator(mode="after")
    def validate_action_shape(self) -> AgendaMutationInput:
        if self.action is AgendaMutationAction.CREATE:
            if self.agenda_item_id is not None or self.expected_version is not None:
                raise ValueError("CREATE cannot supply agenda_item_id or expected_version")
            if not isinstance(self.payload, AgendaUpsertPayload):
                raise ValueError("CREATE requires an Agenda upsert payload")
        elif self.action is AgendaMutationAction.REVISE:
            if self.agenda_item_id is None or self.expected_version is None:
                raise ValueError("REVISE requires agenda_item_id and expected_version")
            if not isinstance(self.payload, AgendaUpsertPayload):
                raise ValueError("REVISE requires an Agenda upsert payload")
        elif self.action is AgendaMutationAction.CANCEL:
            if self.agenda_item_id is None or self.expected_version is None:
                raise ValueError("CANCEL requires agenda_item_id and expected_version")
            if not isinstance(self.payload, AgendaCancelPayload):
                raise ValueError("CANCEL requires an Agenda cancel payload")
        else:
            if self.agenda_item_id is None or self.expected_version is None:
                raise ValueError("LINK_OUTCOME requires agenda_item_id and expected_version")
            if not isinstance(self.payload, AgendaOutcomeLinkPayload):
                raise ValueError("LINK_OUTCOME requires an Agenda outcome-link payload")
        return self


class AgendaQueryFilters(_DTO):
    scopes: tuple[AgendaScopeReason, ...] = (
        AgendaScopeReason.GLOBAL,
        AgendaScopeReason.PORTFOLIO,
        AgendaScopeReason.WATCHLIST,
        AgendaScopeReason.SUBJECT,
        AgendaScopeReason.EXPLICIT,
    )
    instrument_ids: tuple[str, ...] = ()
    subject_ids: tuple[str, ...] = ()
    kinds: tuple[AgendaItemKind, ...] = ()
    statuses: tuple[AgendaItemStatus, ...] = ()


class AgendaQueryInput(_DTO):
    agenda_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    include_history: bool = False
    as_of: datetime | None = None
    window_days: int = Field(default=30, ge=1, le=180)
    filters: AgendaQueryFilters = Field(default_factory=AgendaQueryFilters)
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

class AgendaItemDTO(_DTO):
    agenda_item_id: str
    version: int
    supersedes_version: int | None
    instrument_id: str | None
    subject_id: str | None
    kind: AgendaItemKind
    title: str
    fiscal_period: str | None
    upstream_event_key: str | None
    window_start: datetime | None
    window_end: datetime | None
    timezone: str
    date_certainty: AgendaDateCertainty
    status: AgendaItemStatus
    persisted_status: AgendaItemStatus
    source_type: AgendaSourceType
    source_vendor: str
    source_reference: str | None
    source_visible_at: datetime
    last_verified_at: datetime
    expected_question: str | None
    linked_event_id: str | None
    linked_report_id: str | None
    linked_evidence_id: str | None
    outcome_occurred_at: datetime | None
    outcome_note: str | None
    resolved_evidence_ids: tuple[str, ...] = ()
    revision_note: str | None
    created_by: str
    confirmed_by: str
    recorded_at: datetime
    historical_vintage: bool
    scope_reasons: tuple[AgendaScopeReason, ...] = ()
    limitation_codes: tuple[str, ...] = ()
    schema_version: int
    execution_effect: bool

    @classmethod
    def from_domain(
        cls,
        value: CatalystAgendaVersion,
        *,
        projected_status: AgendaItemStatus | None = None,
        scope_reasons: tuple[AgendaScopeReason, ...] = (),
        limitation_codes: tuple[str, ...] = (),
        resolved_evidence_ids: tuple[str, ...] = (),
    ) -> AgendaItemDTO:
        return cls(
            agenda_item_id=value.agenda_item_id,
            version=value.version,
            supersedes_version=value.supersedes_version,
            instrument_id=value.instrument_id,
            subject_id=value.subject_id,
            kind=value.kind,
            title=value.title,
            fiscal_period=value.fiscal_period,
            upstream_event_key=value.upstream_event_key,
            window_start=value.window_start,
            window_end=value.window_end,
            timezone=value.timezone,
            date_certainty=value.date_certainty,
            status=projected_status or value.status,
            persisted_status=value.status,
            source_type=value.source_type,
            source_vendor=value.source_vendor,
            source_reference=value.source_reference,
            source_visible_at=value.source_visible_at,
            last_verified_at=value.last_verified_at,
            expected_question=value.expected_question,
            linked_event_id=value.linked_event_id,
            linked_report_id=value.linked_report_id,
            linked_evidence_id=value.linked_evidence_id,
            outcome_occurred_at=value.outcome_occurred_at,
            outcome_note=value.outcome_note,
            resolved_evidence_ids=resolved_evidence_ids,
            revision_note=value.revision_note,
            created_by=value.created_by,
            confirmed_by=value.confirmed_by,
            recorded_at=value.recorded_at,
            historical_vintage=value.historical_vintage,
            scope_reasons=scope_reasons,
            limitation_codes=limitation_codes,
            schema_version=value.schema_version,
            execution_effect=value.execution_effect,
        )


class AgendaCoverageStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class AgendaCoverageDTO(_DTO):
    instrument_id: str | None
    subject_id: str | None
    scope_reasons: tuple[AgendaScopeReason, ...]
    status: AgendaCoverageStatus
    matched_item_count: int
    limitation_codes: tuple[str, ...]


class AgendaQueryDTO(_DTO):
    items: tuple[AgendaItemDTO, ...]
    coverage: tuple[AgendaCoverageDTO, ...]
    as_of: datetime
    window_end: datetime
    scope_basis: Literal["CURRENT_DURABLE"] = "CURRENT_DURABLE"
    total: int
    has_more: bool
    limitation_codes: tuple[str, ...]
