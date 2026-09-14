"""Read-only cross-source review digest wire contract."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TodayReviewSubjectDTO(_DTO):
    subject_id: str
    title: str
    href: str


class TodayReviewReasonDTO(_DTO):
    reason_id: str
    source_type: str
    source_id: str
    subject_id: str | None
    title: str
    detail: str
    occurred_at: datetime
    due_at: datetime | None
    href: str
    severity: str


class TodayReviewGroupDTO(_DTO):
    group_id: str
    instrument_id: str | None
    title: str
    status: Literal["ACTIVE", "DEFERRED", "REVIEWED"]
    priority: Literal["ERROR", "DUE", "ATTENTION"]
    subjects: tuple[TodayReviewSubjectDTO, ...]
    reasons: tuple[TodayReviewReasonDTO, ...]
    review_due_at: datetime | None


class TodayReviewDTO(_DTO):
    as_of: datetime
    timezone: str
    groups: tuple[TodayReviewGroupDTO, ...]
    unscoped: tuple[TodayReviewReasonDTO, ...]
    coverage: dict[str, str]
    warnings: tuple[str, ...]
