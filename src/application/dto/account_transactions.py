"""Closed read-only account transaction contracts for Phase 1L."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from application.dto.market import DecimalWire
from domain.common.enums import VendorId
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountActivityCoverageReceipt, AccountTransaction


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class AccountGetTransactionsInput(_DTO):
    providers: tuple[VendorId, ...] = ()
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(default=200, ge=1, le=1_000)

    @field_validator("providers")
    @classmethod
    def unique_providers(cls, value: tuple[VendorId, ...]) -> tuple[VendorId, ...]:
        if len(value) != len(set(value)):
            raise ValueError("providers must be unique")
        return value

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def ordered_window(self) -> AccountGetTransactionsInput:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must be <= end")
        return self


class AccountGetActivityCoverageInput(_DTO):
    providers: tuple[VendorId, ...] = ()
    account_refs: tuple[str, ...] = ()
    limit: int = Field(default=50, ge=1, le=500)

    @field_validator("providers", "account_refs")
    @classmethod
    def unique_values(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(value) != len(set(value)):
            raise ValueError("coverage filters must be unique")
        return value


class AccountTransactionDTO(_DTO):
    provider_transaction_id: str
    account_ref: str
    provider: VendorId
    instrument_id: str | None
    kind: AccountTransactionKind
    side: AccountTransactionSide | None
    quantity: DecimalWire | None
    price: DecimalWire | None
    fees: DecimalWire | None
    currency: str
    occurred_at: datetime
    cash_amount: DecimalWire | None
    source_type: str
    mapping_version: str

    @classmethod
    def from_domain(cls, value: AccountTransaction) -> AccountTransactionDTO:
        return cls.model_validate(value)


class AccountActivityCoverageReceiptDTO(_DTO):
    receipt_id: str
    provider: VendorId
    account_ref: str
    requested_start: datetime
    requested_end: datetime
    effective_start: datetime
    effective_end: datetime
    earliest_event_at: datetime | None
    latest_event_at: datetime | None
    event_count: int
    inserted_count: int
    duplicate_count: int
    snapshot_count: int
    earliest_snapshot_at: datetime | None
    latest_snapshot_at: datetime | None
    mapping_version: str
    supported_kinds: tuple[AccountTransactionKind, ...]
    unavailable_kinds: tuple[AccountTransactionKind, ...]
    status: AccountActivityCoverageStatus
    gap_codes: tuple[str, ...]
    fetched_at: datetime

    @classmethod
    def from_domain(
        cls, value: AccountActivityCoverageReceipt
    ) -> AccountActivityCoverageReceiptDTO:
        return cls.model_validate(value)


class AccountTransactionsDTO(_DTO):
    transactions: tuple[AccountTransactionDTO, ...]
    unavailable_providers: tuple[VendorId, ...] = ()
    coverage_receipts: tuple[AccountActivityCoverageReceiptDTO, ...] = ()


class AccountActivityCoverageDTO(_DTO):
    receipts: tuple[AccountActivityCoverageReceiptDTO, ...]
    overall_status: AccountActivityCoverageStatus
