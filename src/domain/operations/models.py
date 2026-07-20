"""Durable terminal receipt for one US market session synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from domain.common.errors import DataContractError
from domain.common.time import require_aware_datetime
from domain.operations.enums import PostMarketSyncRunStatus, PostMarketSyncStepStatus


def _unique_codes(values: tuple[str, ...], *, field: str) -> None:
    if any(not isinstance(item, str) for item in values):
        raise DataContractError(f"{field} contains a non-string value")
    if len(values) != len(set(values)):
        raise DataContractError(f"{field} must be unique")
    if any(not value or not value.strip() or len(value) > 128 for value in values):
        raise DataContractError(f"{field} contains an invalid value")


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
