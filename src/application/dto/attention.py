"""Read-only Attention query DTOs. Independent of ReviewItem persistence ABI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.attention.enums import (
    ATTENTION_DEFAULT_LIMIT,
    ATTENTION_MAX_LIMIT,
    FORBIDDEN_NEXT_READ_OPERATIONS,
    READ_ONLY_NEXT_READ_TOOLS,
    AttentionClosureCode,
    AttentionCoverageSource,
    AttentionCoverageState,
    AttentionScope,
    AttentionSeverity,
    AttentionSourceType,
    AttentionStatus,
    AttentionTrackingKind,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class AttentionQueryInput(_DTO):
    case_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=ATTENTION_DEFAULT_LIMIT, ge=1, le=ATTENTION_MAX_LIMIT)


class AttentionNextReadDTO(_DTO):
    tool: str = Field(min_length=1, max_length=64)
    request: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _read_only_exact_request(self) -> AttentionNextReadDTO:
        if self.tool not in READ_ONLY_NEXT_READ_TOOLS:
            raise ValueError("next_read.tool must be a public read-only MCP tool")
        operation = self.request.get("operation")
        if isinstance(operation, str) and operation in FORBIDDEN_NEXT_READ_OPERATIONS:
            raise ValueError("next_read cannot recommend a write, sync, or evaluate operation")
        if self.tool == "broker_order_manage" and operation != "status":
            raise ValueError("broker_order_manage next_read may only use operation=status")
        if self.tool == "system_health" and self.request:
            raise ValueError("system_health next_read must use an empty request")
        return self


class AttentionClosureConditionDTO(_DTO):
    code: AttentionClosureCode
    description: str = Field(min_length=1, max_length=500)


class AttentionItemDTO(_DTO):
    key: str = Field(min_length=1, max_length=300)
    tracking_kind: AttentionTrackingKind
    review_item_id: str | None = Field(default=None, max_length=128)
    source_type: AttentionSourceType
    source_ref: str = Field(min_length=1, max_length=256)
    subject_id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    detail: str = Field(min_length=1, max_length=2_000)
    severity: AttentionSeverity
    recommended_action: str = Field(min_length=1, max_length=128)
    status: AttentionStatus
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    due_at: datetime | None = None
    occurrence_count: int | None = Field(default=None, ge=1)
    closure_condition: AttentionClosureConditionDTO
    next_read: AttentionNextReadDTO | None = None

    @model_validator(mode="after")
    def _tracking_rules(self) -> AttentionItemDTO:
        if self.tracking_kind == AttentionTrackingKind.LIVE_PROJECTION.value:
            if self.review_item_id is not None:
                raise ValueError("LIVE_PROJECTION cannot carry review_item_id")
            if self.status != AttentionStatus.OPEN.value:
                raise ValueError("LIVE_PROJECTION status must be OPEN")
        elif self.review_item_id is None:
            raise ValueError("REVIEW_ITEM requires review_item_id")
        return self


class AttentionCoverageDTO(_DTO):
    source: AttentionCoverageSource
    state: AttentionCoverageState
    observed_at: datetime | None = None
    limitation_codes: tuple[str, ...] = ()


class AttentionMetricsDTO(_DTO):
    open_count: int = Field(ge=0)
    acknowledged_count: int = Field(ge=0)
    overdue_count: int = Field(ge=0)
    unknown_execution_count: int = Field(ge=0)
    by_source: dict[str, int] = Field(default_factory=dict)


class AttentionDigestDTO(_DTO):
    generated_at: datetime
    mode: str = "durable_only_read"
    scope: AttentionScope
    subject_id: str | None = None
    case_id: str | None = None
    total_count: int = Field(ge=0)
    total_count_is_lower_bound: bool = False
    returned_count: int = Field(ge=0)
    truncated: bool
    highest_severity: AttentionSeverity | None = None
    limitations: tuple[str, ...] = ()
    coverage: tuple[AttentionCoverageDTO, ...] = ()
    items: tuple[AttentionItemDTO, ...] = ()
    metrics: AttentionMetricsDTO

    @model_validator(mode="after")
    def _scope_and_counts(self) -> AttentionDigestDTO:
        if self.mode != "durable_only_read":
            raise ValueError("AttentionDigest mode must be durable_only_read")
        if self.scope == AttentionScope.SUBJECT.value:
            if self.subject_id is None or self.case_id != self.subject_id:
                raise ValueError("subject scope requires matching subject_id and case_id")
        elif self.subject_id is not None or self.case_id is not None:
            raise ValueError("global AttentionDigest cannot carry a subject id")
        if self.returned_count != len(self.items):
            raise ValueError("returned_count must equal items length")
        if self.returned_count > self.total_count:
            raise ValueError("returned_count cannot exceed total_count")
        if self.truncated != (self.returned_count < self.total_count):
            raise ValueError("truncated must reflect limit clipping")
        return self


class AttentionHealthSummaryDTO(_DTO):
    generated_at: datetime
    basis: str = "materialized_review_items"
    live_projections_not_included: bool = True
    materialized_at: datetime | None = None
    open_review_item_count: int = Field(ge=0)
    acknowledged_review_item_count: int = Field(ge=0)
    highest_severity: AttentionSeverity | None = None
    catalyst_sync_receipt_missing: bool | None
    coverage_status: AttentionCoverageState
    limitation_codes: tuple[str, ...] = ()
