"""Closed application contracts for deterministic Trade Retro."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.retro.models import (
    TradeRetroExportReceipt,
    TradeRetroFinding,
    TradeRetroFindingReview,
    TradeRetroPlanEntry,
    TradeRetroPlanSnapshot,
    TradeRetroReviewRevision,
    TradeRetroRun,
    trade_retro_finding_key,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class TradeRetroFindingReviewInput(_DTO):
    finding_key: str = Field(pattern=r"^finding_[0-9a-f]{64}$")
    status: Literal["ACCEPTED", "DISPUTED", "RESOLVED"]
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def disputed_note(self) -> TradeRetroFindingReviewInput:
        if self.status == "DISPUTED" and (self.note is None or not self.note.strip()):
            raise ValueError("a disputed finding requires a note")
        return self


class TradeRetroWorkflowInput(_DTO):
    action: Literal["prepare", "run", "review", "export"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    start: datetime | None = None
    end: datetime | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    use_llm: bool = True
    expected_version: int | None = Field(default=None, ge=0)
    review_status: Literal["OPEN", "ACCEPTED", "DISPUTED", "RESOLVED"] | None = None
    note_markdown: str = Field(default="", max_length=20_000)
    action_items: tuple[str, ...] = Field(default=(), max_length=20)
    finding_reviews: tuple[TradeRetroFindingReviewInput, ...] = Field(
        default=(),
        max_length=100,
    )
    confirmed_by: Literal["user", "external_agent"] | None = None
    authorization_note: str | None = Field(default=None, min_length=1, max_length=4_000)

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def action_fields(self) -> TradeRetroWorkflowInput:
        review_fields_present = any(
            (
                self.expected_version is not None,
                self.review_status is not None,
                bool(self.note_markdown.strip()),
                bool(self.action_items),
                bool(self.finding_reviews),
                self.confirmed_by is not None,
                self.authorization_note is not None,
            )
        )
        if self.action in {"prepare", "run"}:
            if self.start is None or self.end is None:
                raise ValueError("prepare/run require start and end")
            if self.start >= self.end:
                raise ValueError("start must precede end")
            if self.run_id is not None:
                raise ValueError("run_id is only valid for review/export")
            if review_fields_present:
                raise ValueError("prepare/run do not accept review fields")
        elif self.action == "review":
            if self.run_id is None:
                raise ValueError("review requires run_id")
            if self.start is not None or self.end is not None:
                raise ValueError("review does not accept start/end")
            if self.expected_version is None or self.review_status is None:
                raise ValueError("review requires expected_version and review_status")
            if self.confirmed_by is None or self.authorization_note is None:
                raise ValueError("review requires confirmed_by and authorization_note")
        else:
            if self.run_id is None:
                raise ValueError("export requires run_id")
            if self.start is not None or self.end is not None:
                raise ValueError("export does not accept start/end")
            if not self.use_llm:
                raise ValueError("use_llm is not valid for export")
            if review_fields_present:
                raise ValueError("export does not accept review fields")
        return self


class TradeRetroReviewInput(_DTO):
    run_id: str = Field(pattern=r"^retro_")
    expected_version: int = Field(ge=0)
    status: Literal["OPEN", "ACCEPTED", "DISPUTED", "RESOLVED"]
    note_markdown: str = Field(default="", max_length=20_000)
    action_items: tuple[str, ...] = Field(default=(), max_length=20)
    finding_reviews: tuple[TradeRetroFindingReviewInput, ...] = Field(
        default=(),
        max_length=100,
    )
    confirmed_by: Literal["user", "external_agent"]
    authorization_note: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("action_items")
    @classmethod
    def clean_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item or len(item) > 500 for item in cleaned):
            raise ValueError("action items must be non-blank and at most 500 characters")
        return cleaned

    @model_validator(mode="after")
    def unique_findings(self) -> TradeRetroReviewInput:
        keys = tuple(item.finding_key for item in self.finding_reviews)
        if len(keys) != len(set(keys)):
            raise ValueError("finding_reviews must have unique finding_key values")
        return self


class TradeRetroHistoryInput(_DTO):
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=20, ge=1, le=100)


class TradeRetroPlanEntryDTO(_DTO):
    subject_id: str
    subject_title: str
    plan_id: str
    plan_version: int
    thesis_id: str
    instrument_id: str
    status: str
    stop_price: str | None
    max_position_percent: str
    condition_codes: tuple[str, ...]
    decision_records: tuple[tuple[str, str, str, str | None], ...]

    @classmethod
    def from_domain(cls, value: TradeRetroPlanEntry) -> TradeRetroPlanEntryDTO:
        return cls.model_validate(value)


class TradeRetroPlanSnapshotDTO(_DTO):
    snapshot_id: str
    period_start: datetime
    period_end: datetime
    captured_at: datetime
    entries: tuple[TradeRetroPlanEntryDTO, ...]
    schema_version: int

    @classmethod
    def from_domain(cls, value: TradeRetroPlanSnapshot) -> TradeRetroPlanSnapshotDTO:
        return cls(
            snapshot_id=value.snapshot_id,
            period_start=value.period_start,
            period_end=value.period_end,
            captured_at=value.captured_at,
            entries=tuple(TradeRetroPlanEntryDTO.from_domain(item) for item in value.entries),
            schema_version=value.schema_version,
        )


class TradeRetroFindingDTO(_DTO):
    finding_key: str
    code: str
    severity: str
    title: str
    detail: str
    instrument_id: str | None
    transaction_ids: tuple[str, ...]
    plan_id: str | None

    @classmethod
    def from_domain(cls, value: TradeRetroFinding) -> TradeRetroFindingDTO:
        return cls(
            finding_key=trade_retro_finding_key(value),
            code=value.code,
            severity=value.severity.value,
            title=value.title,
            detail=value.detail,
            instrument_id=value.instrument_id,
            transaction_ids=value.transaction_ids,
            plan_id=value.plan_id,
        )


class TradeRetroFindingReviewDTO(_DTO):
    finding_key: str
    status: str
    note: str | None

    @classmethod
    def from_domain(cls, value: TradeRetroFindingReview) -> TradeRetroFindingReviewDTO:
        return cls(
            finding_key=value.finding_key,
            status=value.status.value,
            note=value.note,
        )


class TradeRetroReviewRevisionDTO(_DTO):
    review_id: str
    run_id: str
    version: int
    status: str
    note_markdown: str
    action_items: tuple[str, ...]
    finding_reviews: tuple[TradeRetroFindingReviewDTO, ...]
    reviewed_by: str
    authorization_note: str
    created_at: datetime
    schema_version: int
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: TradeRetroReviewRevision) -> TradeRetroReviewRevisionDTO:
        return cls(
            review_id=value.review_id,
            run_id=value.run_id,
            version=value.version,
            status=value.status.value,
            note_markdown=value.note_markdown,
            action_items=value.action_items,
            finding_reviews=tuple(
                TradeRetroFindingReviewDTO.from_domain(item)
                for item in value.finding_reviews
            ),
            reviewed_by=value.reviewed_by,
            authorization_note=value.authorization_note,
            created_at=value.created_at,
            schema_version=value.schema_version,
            execution_effect=value.execution_effect,
        )


class TradeRetroRunDTO(_DTO):
    run_id: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    status: str
    plan_snapshot_id: str | None
    transaction_ids: tuple[str, ...]
    findings: tuple[TradeRetroFindingDTO, ...]
    warning_codes: tuple[str, ...]
    summary_markdown: str
    llm_provider: str | None
    llm_model: str | None
    algorithm_version: str
    schema_version: int
    execution_effect: bool
    latest_review: TradeRetroReviewRevisionDTO | None = None
    review_history: tuple[TradeRetroReviewRevisionDTO, ...] = ()

    @classmethod
    def from_domain(
        cls,
        value: TradeRetroRun,
        *,
        reviews: tuple[TradeRetroReviewRevision, ...] = (),
    ) -> TradeRetroRunDTO:
        return cls(
            run_id=value.run_id,
            period_start=value.period_start,
            period_end=value.period_end,
            generated_at=value.generated_at,
            status=value.status.value,
            plan_snapshot_id=value.plan_snapshot_id,
            transaction_ids=value.transaction_ids,
            findings=tuple(TradeRetroFindingDTO.from_domain(item) for item in value.findings),
            warning_codes=value.warning_codes,
            summary_markdown=value.summary_markdown,
            llm_provider=value.llm_provider,
            llm_model=value.llm_model,
            algorithm_version=value.algorithm_version,
            schema_version=value.schema_version,
            execution_effect=value.execution_effect,
            latest_review=(
                TradeRetroReviewRevisionDTO.from_domain(reviews[0]) if reviews else None
            ),
            review_history=tuple(
                TradeRetroReviewRevisionDTO.from_domain(item) for item in reviews
            ),
        )


class TradeRetroHistoryDTO(_DTO):
    runs: tuple[TradeRetroRunDTO, ...]


class TradeRetroExportReceiptDTO(_DTO):
    receipt_id: str
    run_id: str
    target_path: str
    content_sha256: str
    exported_at: datetime
    review_version: int | None

    @classmethod
    def from_domain(cls, value: TradeRetroExportReceipt) -> TradeRetroExportReceiptDTO:
        return cls.model_validate(value)
