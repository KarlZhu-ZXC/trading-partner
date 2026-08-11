"""Durable-only system data-quality DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from application.dto.market import DecimalWire
from domain.catalyst_agenda.enums import AgendaSyncStatus
from domain.common.enums import DataCategory, HealthState, Market, VendorId
from domain.monitoring.enums import MonitorCadence, MonitorRunStatus
from domain.portfolio.enums import AccountActivityCoverageStatus


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class DataQualityIssueDTO(_DTO):
    code: str = Field(min_length=1, max_length=128)
    severity: HealthState
    scope: Literal[
        "account_snapshot",
        "account_activity",
        "monitor",
        "provider_route",
        "research_state",
        "persistence",
        "catalyst_agenda",
    ]
    subject_ref: str | None = Field(default=None, max_length=128)
    observed_at: datetime | None = None
    detail: str = Field(min_length=1, max_length=500)
    recommended_action_code: str | None = Field(default=None, max_length=128)
    automatic_recovery_expected: bool = False


class AccountSnapshotQualityDTO(_DTO):
    snapshot_id: str
    account_ref: str
    provider: VendorId
    account_as_of: datetime
    fetched_at: datetime
    age_seconds: int = Field(ge=0)
    position_count: int = Field(ge=0)
    valued_position_count: int = Field(ge=0)
    timestamped_price_count: int = Field(ge=0)
    valuation_coverage_ratio: DecimalWire | None
    price_time_coverage_ratio: DecimalWire | None
    net_assets_available: bool
    degraded: bool
    warning_codes: tuple[str, ...]


class AccountActivityQualityDTO(_DTO):
    receipt_id: str
    account_ref: str
    provider: VendorId
    requested_start: datetime
    requested_end: datetime
    effective_start: datetime
    effective_end: datetime
    fetched_at: datetime
    event_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    mapping_version: str
    status: AccountActivityCoverageStatus
    unavailable_kinds: tuple[str, ...]
    gap_codes: tuple[str, ...]


class MonitorQualityDTO(_DTO):
    monitor_id: str
    monitor_version: int = Field(ge=1)
    name: str
    cadence: MonitorCadence
    primary_instrument_id: str | None
    latest_run_id: str | None
    latest_run_at: datetime | None
    latest_run_status: MonitorRunStatus | None
    latest_evaluated_version: int | None = Field(default=None, ge=1)
    current_version_evaluated: bool
    rule_count: int = Field(ge=0)
    latest_observation_count: int = Field(ge=0)
    not_evaluated_count: int = Field(ge=0)
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]


class ProviderRouteQualityDTO(_DTO):
    market: Market
    category: DataCategory
    window_start: datetime
    latest_at: datetime
    execution_count: int = Field(ge=1)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    cache_count: int = Field(ge=0)
    latest_selected_vendor: VendorId | None
    latest_error_code: str | None
    warning_codes: tuple[str, ...]


class CatalystAgendaSyncQualityDTO(_DTO):
    receipt_id: str
    status: AgendaSyncStatus
    as_of: datetime
    completed_at: datetime
    scope_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    appended_count: int = Field(ge=0)
    revised_count: int = Field(ge=0)
    date_drift_count: int = Field(ge=0)
    failed_scope_count: int = Field(ge=0)
    limitation_codes: tuple[str, ...] = Field(max_length=100)


class DataQualityCenterDTO(_DTO):
    status: HealthState
    generated_at: datetime
    mode: Literal["durable_only"] = "durable_only"
    account_snapshots: tuple[AccountSnapshotQualityDTO, ...]
    account_activity: tuple[AccountActivityQualityDTO, ...]
    monitors: tuple[MonitorQualityDTO, ...]
    provider_routes: tuple[ProviderRouteQualityDTO, ...]
    provider_route_window_truncated: bool
    catalyst_agenda_sync: CatalystAgendaSyncQualityDTO | None = None
    issues: tuple[DataQualityIssueDTO, ...]
    limitations: tuple[str, ...]
