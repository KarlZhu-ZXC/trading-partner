"""Current local-week review projection, never a performance score."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from application.dto.today_review import TodayReviewGroupDTO


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WeeklyConfirmedViewDTO(_DTO):
    subject_id: str
    subject_title: str
    thesis_id: str
    revision_id: str
    revision_no: int
    confirmed_at: datetime
    kind: Literal["INITIAL", "CHANGED", "RECONFIRMED", "COMPARISON_UNAVAILABLE"]
    before_statement: str | None
    after_statement: str
    changed_fields: tuple[str, ...]
    href: str


class WeeklyDecisionDTO(_DTO):
    subject_id: str
    subject_title: str
    decision_id: str
    decision_type: str
    title: str
    rationale: str
    recorded_at: datetime
    review_due_at: datetime | None
    href: str


class WeeklyQuestionDTO(_DTO):
    subject_id: str
    question_id: str
    text: str
    asked_at: datetime
    status: str
    href: str


class WeeklyReviewDTO(_DTO):
    as_of: datetime
    week_start: datetime
    week_end: datetime
    timezone: str
    confirmed_views: tuple[WeeklyConfirmedViewDTO, ...]
    decisions: tuple[WeeklyDecisionDTO, ...]
    open_questions: tuple[WeeklyQuestionDTO, ...]
    due_groups: tuple[TodayReviewGroupDTO, ...]
    unresolved_groups: tuple[TodayReviewGroupDTO, ...]
    coverage: dict[str, str]
    warnings: tuple[str, ...]
