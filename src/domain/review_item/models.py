"""Validated ReviewItem projections and durable current state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.review_item.enums import ReviewItemSeverity, ReviewItemSourceType, ReviewItemStatus


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{field} must be non-blank text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataContractError(f"{field} length must be <= {maximum}")
    return normalized


def _optional(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


@dataclass(frozen=True, slots=True)
class ReviewItemProjection:
    source_key: str
    source_type: ReviewItemSourceType
    source_ref: str
    title: str
    detail: str
    severity: ReviewItemSeverity
    recommended_action: str
    href: str
    subject_id: str | None = None
    due_at: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.source_key, "source_key", 300)
        if not isinstance(self.source_type, ReviewItemSourceType):
            raise DataContractError("source_type is invalid")
        _text(self.source_ref, "source_ref", 256)
        _text(self.title, "title", 500)
        _text(self.detail, "detail", 2_000)
        if not isinstance(self.severity, ReviewItemSeverity):
            raise DataContractError("severity is invalid")
        _text(self.recommended_action, "recommended_action", 128)
        _text(self.href, "href", 500)
        _optional(self.subject_id, "subject_id", 128)
        if self.due_at is not None:
            require_aware_datetime(self.due_at, field_name="due_at")


@dataclass(frozen=True, slots=True)
class ReviewItem:
    review_item_id: str
    source_key: str
    source_type: ReviewItemSourceType
    source_ref: str
    subject_id: str | None
    title: str
    detail: str
    severity: ReviewItemSeverity
    recommended_action: str
    href: str
    status: ReviewItemStatus
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

    def __post_init__(self) -> None:
        if not self.review_item_id.startswith("review_item_"):
            raise DataContractError("review_item_id must use review_item_ prefix")
        _text(self.source_key, "source_key", 300)
        if not isinstance(self.source_type, ReviewItemSourceType):
            raise DataContractError("source_type is invalid")
        _text(self.source_ref, "source_ref", 256)
        _optional(self.subject_id, "subject_id", 128)
        _text(self.title, "title", 500)
        _text(self.detail, "detail", 2_000)
        if not isinstance(self.severity, ReviewItemSeverity):
            raise DataContractError("severity is invalid")
        _text(self.recommended_action, "recommended_action", 128)
        _text(self.href, "href", 500)
        if not isinstance(self.status, ReviewItemStatus):
            raise DataContractError("status is invalid")
        require_aware_datetime(self.first_seen_at, field_name="first_seen_at")
        require_aware_datetime(self.last_seen_at, field_name="last_seen_at")
        if self.last_seen_at < self.first_seen_at:
            raise DataContractError("last_seen_at cannot precede first_seen_at")
        if self.due_at is not None:
            require_aware_datetime(self.due_at, field_name="due_at")
        if self.resolved_at is not None:
            require_aware_datetime(self.resolved_at, field_name="resolved_at")
        _optional(self.resolved_by, "resolved_by", 100)
        _optional(self.resolution_note, "resolution_note", 2_000)
        _optional(self.resolution_ref, "resolution_ref", 256)
        if self.occurrence_count < 1:
            raise DataContractError("occurrence_count must be positive")
        if self.version < 1:
            raise DataContractError("version must be positive")
        is_resolved = self.status in {
            ReviewItemStatus.RESOLVED,
            ReviewItemStatus.AUTO_RESOLVED,
        }
        if is_resolved != (self.resolved_at is not None and self.resolved_by is not None):
            raise DataContractError("ReviewItem resolution state is inconsistent")
        if self.status is ReviewItemStatus.RESOLVED and self.resolution_note is None:
            raise DataContractError("manually resolved ReviewItem requires resolution_note")


@dataclass(frozen=True, slots=True)
class ReviewItemMetrics:
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

    def __post_init__(self) -> None:
        require_aware_datetime(self.measured_at, field_name="measured_at")
        counts = (
            self.total_items,
            self.open_count,
            self.acknowledged_count,
            self.resolved_count,
            self.auto_resolved_count,
            self.overdue_count,
            self.recurring_count,
            self.acknowledgment_sample_size,
            self.closure_sample_size,
            self.manual_closure_count,
            self.auto_closure_count,
        )
        if any(value < 0 for value in counts):
            raise DataContractError("ReviewItem metric counts cannot be negative")
        if (
            self.open_count
            + self.acknowledged_count
            + self.resolved_count
            + self.auto_resolved_count
            != self.total_items
        ):
            raise DataContractError("ReviewItem current status counts must equal total_items")
        if self.overdue_count > self.open_count + self.acknowledged_count:
            raise DataContractError("ReviewItem overdue_count exceeds unresolved items")
        if self.recurring_count > self.total_items:
            raise DataContractError("ReviewItem recurring_count exceeds total_items")
        durations = (
            self.oldest_current_open_age_seconds,
            self.median_open_to_ack_seconds,
            self.median_open_to_close_seconds,
        )
        if any(value is not None and value < 0 for value in durations):
            raise DataContractError("ReviewItem metric durations cannot be negative")
        if (self.acknowledgment_sample_size == 0) != (self.median_open_to_ack_seconds is None):
            raise DataContractError("ReviewItem acknowledgment median/sample mismatch")
        if (self.closure_sample_size == 0) != (self.median_open_to_close_seconds is None):
            raise DataContractError("ReviewItem closure median/sample mismatch")
        if (self.open_count + self.acknowledged_count == 0) != (
            self.oldest_current_open_age_seconds is None
        ):
            raise DataContractError("ReviewItem oldest-open metric/sample mismatch")
        if self.closure_sample_size != (self.manual_closure_count + self.auto_closure_count):
            raise DataContractError("ReviewItem closure modes do not cover closure sample")
        if (self.closure_sample_size == 0) != (self.manual_resolution_rate is None):
            raise DataContractError("ReviewItem manual resolution rate/sample mismatch")
        if (self.total_items == 0) != (self.recurrence_rate is None):
            raise DataContractError("ReviewItem recurrence rate/sample mismatch")
        for rate in (self.manual_resolution_rate, self.recurrence_rate):
            if rate is not None and not 0 <= rate <= 1:
                raise DataContractError("ReviewItem metric rates must be between zero and one")
        if any(value < 0 for value in self.open_by_source.values()):
            raise DataContractError("ReviewItem open_by_source counts cannot be negative")
        if sum(self.open_by_source.values()) != self.open_count + self.acknowledged_count:
            raise DataContractError("ReviewItem open_by_source total is inconsistent")
