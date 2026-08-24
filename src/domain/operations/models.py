"""Durable terminal receipt for one US market session synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.operations.enums import (
    OperationalJobStatus,
    PostMarketSyncRunStatus,
    PostMarketSyncStepStatus,
)


def _unique_codes(values: tuple[str, ...], *, field: str) -> None:
    if any(not isinstance(item, str) for item in values):
        raise DataContractError(f"{field} contains a non-string value")
    if len(values) != len(set(values)):
        raise DataContractError(f"{field} must be unique")
    if any(not value or not value.strip() or len(value) > 128 for value in values):
        raise DataContractError(f"{field} contains an invalid value")


@dataclass(frozen=True, slots=True)
class OperationalJobRun:
    job_run_id: str
    job_name: str
    idempotency_key: str
    status: OperationalJobStatus
    attempt: int
    lease_owner_hash: str
    lease_expires_at: datetime
    heartbeat_at: datetime
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    result_code: str | None = None
    error_code: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("job_run_id", self.job_run_id, 160),
            ("job_name", self.job_name, 96),
            ("idempotency_key", self.idempotency_key, 160),
            ("lease_owner_hash", self.lease_owner_hash, 64),
        ):
            if not value.strip() or len(value) > maximum:
                raise DataContractError(f"{field_name} must be bounded nonblank text")
        if self.attempt < 1 or self.version < 1:
            raise DataContractError("Operational Job attempt/version must be positive")
        for time_name, time_value in (
            ("lease_expires_at", self.lease_expires_at),
            ("heartbeat_at", self.heartbeat_at),
            ("started_at", self.started_at),
            ("updated_at", self.updated_at),
        ):
            require_aware_datetime(time_value, field_name=time_name)
        if self.completed_at is not None:
            require_aware_datetime(self.completed_at, field_name="completed_at")
        terminal = self.status is not OperationalJobStatus.RUNNING
        if terminal != (self.completed_at is not None):
            raise DataContractError("Operational Job terminal state/time mismatch")
        if self.status is OperationalJobStatus.SUCCEEDED and self.error_code is not None:
            raise DataContractError("Successful Operational Job cannot carry an error")
        if (
            self.status
            in {OperationalJobStatus.FAILED, OperationalJobStatus.INTERRUPTED}
            and not self.error_code
        ):
            raise DataContractError("Failed/interrupted Operational Job requires error_code")


@dataclass(frozen=True, slots=True)
class OperationalJobClaim:
    run: OperationalJobRun
    claimed: bool


@dataclass(frozen=True, slots=True)
class PostMarketSyncRun:
    run_id: str
    market_session_date: date
    scheduled_for: datetime
    started_at: datetime
    completed_at: datetime
    status: PostMarketSyncRunStatus
    portfolio_status: PostMarketSyncStepStatus
    watchlist_status: PostMarketSyncStepStatus
    account_snapshot_ids: tuple[str, ...]
    watchlist_groups_synced: int | None
    watchlist_membership_relations_synced: int | None
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    attempt_count: int = 1

    def __post_init__(self) -> None:
        if not self.run_id or len(self.run_id) > 128:
            raise DataContractError("run_id must be a bounded non-blank string")
        for name, value in (
            ("scheduled_for", self.scheduled_for),
            ("started_at", self.started_at),
            ("completed_at", self.completed_at),
        ):
            require_aware_datetime(value, field_name=name)
        if self.completed_at < self.started_at:
            raise DataContractError("completed_at must be >= started_at")
        if self.attempt_count < 1:
            raise DataContractError("attempt_count must be positive")
        expected = (
            PostMarketSyncRunStatus.SUCCEEDED
            if self.portfolio_status is PostMarketSyncStepStatus.SUCCEEDED
            and self.watchlist_status is PostMarketSyncStepStatus.SUCCEEDED
            else PostMarketSyncRunStatus.FAILED
            if self.portfolio_status is PostMarketSyncStepStatus.FAILED
            and self.watchlist_status is PostMarketSyncStepStatus.FAILED
            else PostMarketSyncRunStatus.PARTIAL
        )
        if self.status is not expected:
            raise DataContractError("post-market sync status does not match step outcomes")
        for count in (
            self.watchlist_groups_synced,
            self.watchlist_membership_relations_synced,
        ):
            if count is not None and count < 0:
                raise DataContractError("watchlist counts must be nonnegative")
        if self.watchlist_status is PostMarketSyncStepStatus.SUCCEEDED and (
            self.watchlist_groups_synced is None
            or self.watchlist_membership_relations_synced is None
        ):
            raise DataContractError("successful watchlist step requires synchronization counts")
        _unique_codes(self.account_snapshot_ids, field="account_snapshot_ids")
        _unique_codes(self.warning_codes, field="warning_codes")
        _unique_codes(self.error_codes, field="error_codes")
        if self.status is PostMarketSyncRunStatus.SUCCEEDED and self.error_codes:
            raise DataContractError("successful post-market sync cannot contain errors")
        if self.status is not PostMarketSyncRunStatus.SUCCEEDED and not self.error_codes:
            raise DataContractError("imperfect post-market sync requires error codes")
