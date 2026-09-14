"""Explicit intent for the quick, non-executing review workflow."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class QuickReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    review_token: str = Field(min_length=1, max_length=128)
    baseline_decision_id: str | None = Field(default=None, max_length=128)
    action: Literal["maintain", "defer"]
    rationale: str = Field(min_length=1, max_length=8000)
    review_due_at: AwareDatetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    confirmed: Literal[True]
