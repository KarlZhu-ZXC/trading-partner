"""Closed DTOs for durable review of one exact Observation revision."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.external_note.models import ExternalNoteReview


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExternalNoteReviewTransitionInput(_DTO):
    review_id: str = Field(min_length=1, max_length=128)
    status: str
    expected_version: int = Field(ge=1)
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    due_at: datetime | None = None
    actor: str
    authorization_note: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_shape(self) -> ExternalNoteReviewTransitionInput:
        terminal = self.status in {"ADOPTED", "NO_ACTION"}
        if terminal and (self.subject_id is None or self.decision_id is None):
            raise ValueError("ADOPTED and NO_ACTION require subject_id and decision_id")
        if not terminal and self.decision_id is not None:
            raise ValueError("non-terminal review cannot link a decision")
        if self.status != "DEFERRED" and self.due_at is not None:
            raise ValueError("only DEFERRED review accepts due_at")
        return self


class ExternalNoteReviewDTO(_DTO):
    review_id: str
    note_revision_id: str
    note_id: str
    version: int
    status: str
    subject_id: str | None
    decision_id: str | None
    due_at: datetime | None
    actor: str
    authorization_note: str
    idempotency_key: str
    created_at: datetime

    @classmethod
    def from_domain(cls, value: ExternalNoteReview) -> ExternalNoteReviewDTO:
        return cls(
            review_id=value.review_id,
            note_revision_id=value.note_revision_id,
            note_id=value.note_id,
            version=value.version,
            status=value.status.value,
            subject_id=value.subject_id,
            decision_id=value.decision_id,
            due_at=value.due_at,
            actor=value.actor,
            authorization_note=value.authorization_note,
            idempotency_key=value.idempotency_key,
            created_at=value.created_at,
        )
