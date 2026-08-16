"""Closed DTOs for internal ReviewItem projection and human closure."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.review_item.models import ReviewItem, ReviewItemMetrics


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ReviewItemTransitionInput(_DTO):
    review_item_id: str = Field(min_length=1, max_length=128)
    status: str
    expected_version: int = Field(ge=1)
    actor: str
    authorization_note: str = Field(min_length=1, max_length=4_000)
    resolution_note: str | None = Field(default=None, min_length=1, max_length=2_000)
    resolution_ref: str | None = Field(default=None, min_length=1, max_length=256)
    due_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_resolution(self) -> ReviewItemTransitionInput:
        if self.status == "RESOLVED" and self.resolution_note is None:
            raise ValueError("RESOLVED ReviewItem requires resolution_note")
        if self.status != "RESOLVED" and (
            self.resolution_note is not None or self.resolution_ref is not None
        ):
            raise ValueError("only RESOLVED ReviewItem accepts resolution details")
        if self.status == "RESOLVED" and self.due_at is not None:
            raise ValueError("RESOLVED ReviewItem cannot adjust due_at")
        return self


class ReviewItemDTO(_DTO):
    review_item_id: str
    source_key: str
    source_type: str
    source_ref: str
    subject_id: str | None
    title: str
    detail: str
    severity: str
    recommended_action: str
    href: str
    status: str
    active_at_source: bool
    first_seen_at: datetime
    last_seen_at: datetime
    due_at: datetime | None
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None
    resolution_ref: str | None
    occurrence_count: int
    version: int

    @classmethod
    def from_domain(cls, value: ReviewItem) -> ReviewItemDTO:
        return cls(
            review_item_id=value.review_item_id,
            source_key=value.source_key,
            source_type=value.source_type.value,
            source_ref=value.source_ref,
            subject_id=value.subject_id,
            title=value.title,
            detail=value.detail,
            severity=value.severity.value,
            recommended_action=value.recommended_action,
            href=value.href,
            status=value.status.value,
            active_at_source=value.active_at_source,
            first_seen_at=value.first_seen_at,
            last_seen_at=value.last_seen_at,
            due_at=value.due_at,
            resolved_at=value.resolved_at,
            resolved_by=value.resolved_by,
            resolution_note=value.resolution_note,
            resolution_ref=value.resolution_ref,
            occurrence_count=value.occurrence_count,
            version=value.version,
        )


class ReviewItemMetricsDTO(_DTO):
    measured_at: datetime
    total_items: int
    open_count: int
    acknowledged_count: int
    resolved_count: int
    auto_resolved_count: int
    overdue_count: int
    recurring_count: int
    oldest_current_open_age_seconds: int | None
    median_open_to_ack_seconds: int | None
    median_open_to_close_seconds: int | None
    acknowledgment_sample_size: int
    closure_sample_size: int
    manual_closure_count: int
    auto_closure_count: int
    manual_resolution_rate: float | None
    recurrence_rate: float | None
    open_by_source: dict[str, int]

    @classmethod
    def from_domain(cls, value: ReviewItemMetrics) -> ReviewItemMetricsDTO:
        return cls(**{field: getattr(value, field) for field in cls.model_fields})
