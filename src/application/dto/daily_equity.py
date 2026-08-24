"""Closed DTOs for Daily Equity materialization and activation facts."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.performance.daily_equity import (
    DailyEquityMaterializationReceipt,
    DailyEquitySnapshot,
    JournalActivation,
)
from domain.performance.enums import (
    DailyEquityCoverageStatus,
    DailyEquityMaterializationMode,
)


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class JournalActivationDTO(_DTO):
    activation_id: str
    journal_activation_at: datetime
    recorded_at: datetime
    actor: str
    idempotency_key: str
    algorithm_version: str

    @classmethod
    def from_domain(cls, value: JournalActivation) -> JournalActivationDTO:
        return cls.model_validate(value)


class DailyEquitySnapshotDTO(_DTO):
    daily_equity_snapshot_id: str
    account_ref: str
    currency: str
    valuation_at: datetime
    market_session_date: date
    equity_value: DecimalWire | None
    cash_value: DecimalWire | None
    gross_position_value: DecimalWire | None
    net_external_cash_flow_since_previous: DecimalWire | None
    valuation_basis: str
    source_snapshot_id: str
    source_snapshot_as_of: datetime
    source_fetched_at: datetime
    journal_activation_at: datetime | None
    coverage_status: DailyEquityCoverageStatus
    quality_status: DailyEquityCoverageStatus
    materialized_at: datetime
    warning_codes: tuple[str, ...]
    algorithm_version: str

    @classmethod
    def from_domain(cls, value: DailyEquitySnapshot) -> DailyEquitySnapshotDTO:
        return cls.model_validate(value)

    @property
    def snapshot_id(self) -> str:
        return self.source_snapshot_id


class DailyEquityMaterializationReceiptDTO(_DTO):
    receipt_id: str
    mode: DailyEquityMaterializationMode
    generated_at: datetime
    journal_activation_at: datetime | None
    account_refs: tuple[str, ...]
    source_snapshot_ids: tuple[str, ...]
    materialized_snapshot_ids: tuple[str, ...]
    candidate_count: int
    inserted_count: int
    duplicate_count: int
    skipped_count: int
    coverage_status: DailyEquityCoverageStatus
    warning_codes: tuple[str, ...]
    algorithm_version: str
    persisted: bool
    wall_clock_ms: int | None
    would_insert_count: int

    @classmethod
    def from_domain(
        cls,
        value: DailyEquityMaterializationReceipt,
    ) -> DailyEquityMaterializationReceiptDTO:
        return cls.model_validate(value)


class DailyEquityMaterializationInput(_DTO):
    mode: DailyEquityMaterializationMode = DailyEquityMaterializationMode.SHADOW
    account_refs: tuple[str, ...] = ()
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Daily Equity datetime must be timezone-aware")
        return value

    @field_validator("account_refs")
    @classmethod
    def unique_accounts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("account_refs must be unique non-blank values")
        return value

    @model_validator(mode="after")
    def ordered_window(self) -> DailyEquityMaterializationInput:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("Daily Equity start must be <= end")
        return self


__all__ = [
    "DailyEquityMaterializationInput",
    "DailyEquityMaterializationReceiptDTO",
    "DailyEquitySnapshotDTO",
    "JournalActivationDTO",
]
