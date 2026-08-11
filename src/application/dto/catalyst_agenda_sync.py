"""Closed operational DTOs for explicit Catalyst Agenda synchronization."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.catalyst_agenda.calendar import (
    CatalystAgendaProviderSyncResult,
    CatalystAgendaSyncReceipt,
)
from domain.catalyst_agenda.enums import AgendaSyncProviderStatus, AgendaSyncStatus
from domain.common.enums import VendorId


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class CatalystAgendaSyncInput(_DTO):
    instrument_ids: tuple[str, ...] = Field(default=(), max_length=200)
    fred_release_ids: tuple[int, ...] = Field(default=(), max_length=50)
    window_days: int = Field(default=30, ge=1, le=180)
    as_of: datetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_scope(self) -> CatalystAgendaSyncInput:
        if any(not value.strip() or len(value) > 256 for value in self.instrument_ids):
            raise ValueError("instrument_ids must contain bounded non-blank IDs")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("instrument_ids must be unique")
        if any(type(value) is not int or value < 1 for value in self.fred_release_ids):
            raise ValueError("fred_release_ids must contain positive integers")
        if len(set(self.fred_release_ids)) != len(self.fred_release_ids):
            raise ValueError("fred_release_ids must be unique")
        return self


class CatalystAgendaProviderSyncResultDTO(_DTO):
    vendor: VendorId
    scope_ref: str
    status: AgendaSyncProviderStatus
    candidate_count: int
    error_code: str | None
    warning_codes: tuple[str, ...]

    @classmethod
    def from_domain(
        cls, value: CatalystAgendaProviderSyncResult
    ) -> CatalystAgendaProviderSyncResultDTO:
        return cls(
            vendor=value.vendor,
            scope_ref=value.scope_ref,
            status=value.status,
            candidate_count=value.candidate_count,
            error_code=value.error_code,
            warning_codes=value.warning_codes,
        )


class CatalystAgendaSyncReceiptDTO(_DTO):
    receipt_id: str
    status: AgendaSyncStatus
    as_of: datetime
    window_start: datetime
    window_end: datetime
    scope_count: int
    eligible_instrument_count: int
    succeeded_scope_count: int
    failed_scope_count: int
    candidate_count: int
    appended_count: int
    revised_count: int
    date_drift_count: int
    unchanged_count: int
    provider_results: tuple[CatalystAgendaProviderSyncResultDTO, ...]
    limitation_codes: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    schema_version: int
    execution_effect: bool

    @classmethod
    def from_domain(cls, value: CatalystAgendaSyncReceipt) -> CatalystAgendaSyncReceiptDTO:
        return cls(
            receipt_id=value.receipt_id,
            status=value.status,
            as_of=value.as_of,
            window_start=value.window_start,
            window_end=value.window_end,
            scope_count=value.scope_count,
            eligible_instrument_count=value.eligible_instrument_count,
            succeeded_scope_count=value.succeeded_scope_count,
            failed_scope_count=value.failed_scope_count,
            candidate_count=value.candidate_count,
            appended_count=value.appended_count,
            revised_count=value.revised_count,
            date_drift_count=value.date_drift_count,
            unchanged_count=value.unchanged_count,
            provider_results=tuple(
                CatalystAgendaProviderSyncResultDTO.from_domain(item)
                for item in value.provider_results
            ),
            limitation_codes=value.limitation_codes,
            started_at=value.started_at,
            completed_at=value.completed_at,
            schema_version=value.schema_version,
            execution_effect=value.execution_effect,
        )
