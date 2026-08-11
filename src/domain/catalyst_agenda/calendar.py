"""Normalized future-calendar facts and append-only sync receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from domain.catalyst_agenda.enums import (
    AgendaDateCertainty,
    AgendaItemKind,
    AgendaSyncProviderStatus,
    AgendaSyncStatus,
)
from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime

_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _text(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise DataContractError(f"{field} must be non-blank text <= {maximum}")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CatalystCalendarCandidate:
    """Provider-normalized future event; never writes persistence directly."""

    vendor: VendorId
    instrument_id: str | None
    kind: AgendaItemKind
    title: str
    fiscal_period: str | None
    upstream_event_key: str
    window_start: datetime
    window_end: datetime
    timezone: str
    date_certainty: AgendaDateCertainty
    source_reference: str | None
    source_visible_at: datetime
    last_verified_at: datetime
    historical_vintage: bool

    def __post_init__(self) -> None:
        if self.vendor not in {VendorId.YFINANCE, VendorId.FRED}:
            raise DataContractError("calendar candidate vendor is unsupported")
        if self.vendor is VendorId.YFINANCE and self.instrument_id is None:
            raise DataContractError("Yahoo calendar candidate requires instrument_id")
        if self.vendor is VendorId.FRED and self.instrument_id is not None:
            raise DataContractError("FRED release candidate cannot claim an Instrument")
        _text(self.title, "title", 300)
        _text(self.upstream_event_key, "upstream_event_key", 300)
        _text(self.timezone, "timezone", 100)
        require_aware_datetime(self.window_start, field_name="window_start")
        require_aware_datetime(self.window_end, field_name="window_end")
        require_aware_datetime(self.source_visible_at, field_name="source_visible_at")
        require_aware_datetime(self.last_verified_at, field_name="last_verified_at")
        if self.window_end < self.window_start:
            raise DataContractError("calendar candidate window is reversed")
        if self.source_visible_at > self.last_verified_at:
            raise DataContractError("source_visible_at must be <= last_verified_at")


@dataclass(frozen=True, slots=True)
class CatalystCalendarBatch:
    vendor: VendorId
    start: date
    end: date
    candidates: tuple[CatalystCalendarCandidate, ...]
    limitation_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise DataContractError("calendar batch end must be >= start")
        if any(item.vendor is not self.vendor for item in self.candidates):
            raise DataContractError("calendar batch contains a foreign vendor candidate")
        for code in self.limitation_codes:
            if _CODE.fullmatch(code) is None:
                raise DataContractError("calendar limitation code is invalid")


@dataclass(frozen=True, slots=True)
class CatalystAgendaProviderSyncResult:
    vendor: VendorId
    scope_ref: str
    status: AgendaSyncProviderStatus
    candidate_count: int
    error_code: str | None = None
    warning_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.scope_ref, "scope_ref", 256)
        if self.candidate_count < 0:
            raise DataContractError("candidate_count must be non-negative")
        for code in (*self.warning_codes, *((self.error_code,) if self.error_code else ())):
            if _CODE.fullmatch(code) is None:
                raise DataContractError("sync result code is invalid")


@dataclass(frozen=True, slots=True)
class CatalystAgendaSyncReceipt:
    receipt_id: str
    idempotency_key: str
    request_fingerprint: str
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
    provider_results: tuple[CatalystAgendaProviderSyncResult, ...]
    limitation_codes: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    schema_version: int = 1
    execution_effect: bool = False

    def __post_init__(self) -> None:
        if not self.receipt_id.startswith("run_"):
            raise DataContractError("receipt_id must use run_ prefix")
        _text(self.idempotency_key, "idempotency_key", 128)
        if re.fullmatch(r"[0-9a-f]{64}", self.request_fingerprint) is None:
            raise DataContractError("request_fingerprint must be SHA-256")
        for count in (
            self.scope_count,
            self.eligible_instrument_count,
            self.succeeded_scope_count,
            self.failed_scope_count,
            self.candidate_count,
            self.appended_count,
            self.revised_count,
            self.date_drift_count,
            self.unchanged_count,
        ):
            if type(count) is not int or count < 0:
                raise DataContractError("sync counts must be non-negative integers")
        for instant, name in (
            (self.as_of, "as_of"),
            (self.window_start, "window_start"),
            (self.window_end, "window_end"),
            (self.started_at, "started_at"),
            (self.completed_at, "completed_at"),
        ):
            require_aware_datetime(instant, field_name=name)
        if self.window_end < self.window_start or self.completed_at < self.started_at:
            raise DataContractError("sync receipt time range is reversed")
        if self.execution_effect:
            raise DataContractError("Catalyst Agenda sync cannot execute trades")
        for code in self.limitation_codes:
            if _CODE.fullmatch(code) is None:
                raise DataContractError("sync limitation code is invalid")
