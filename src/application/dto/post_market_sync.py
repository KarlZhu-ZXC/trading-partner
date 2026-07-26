"""Scheduler-friendly output for the post-market synchronization CLI."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from domain.operations.enums import PostMarketSyncRunStatus, PostMarketSyncStepStatus


class PostMarketSyncDisposition(StrEnum):
    EXECUTED = "EXECUTED"
    SKIPPED_NON_TRADING_DAY = "SKIPPED_NON_TRADING_DAY"
    SKIPPED_NOT_DUE = "SKIPPED_NOT_DUE"
    SKIPPED_ALREADY_COMPLETED = "SKIPPED_ALREADY_COMPLETED"


class PostMarketSyncHealth(StrEnum):
    HEALTHY = "HEALTHY"
    RECEIPT_MISSING = "RECEIPT_MISSING"
    RECEIPT_IMPERFECT = "RECEIPT_IMPERFECT"
    NO_DUE_SESSION = "NO_DUE_SESSION"


class PostMarketSyncResultDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: PostMarketSyncDisposition
    market_session_date: date | None = None
    scheduled_for: datetime | None = None
    run_id: str | None = None
    run_status: PostMarketSyncRunStatus | None = None
    portfolio_status: PostMarketSyncStepStatus | None = None
    watchlist_status: PostMarketSyncStepStatus | None = None
    account_snapshot_ids: tuple[str, ...] = ()
    holding_count: int = 0
    watchlist_groups_synced: int | None = None
    watchlist_membership_relations_synced: int | None = None
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()


class PostMarketSyncStatusDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    health: PostMarketSyncHealth
    expected_session_date: date | None = None
    expected_scheduled_for: datetime | None = None
    receipt_session_date: date | None = None
    run_status: PostMarketSyncRunStatus | None = None
    portfolio_status: PostMarketSyncStepStatus | None = None
    watchlist_status: PostMarketSyncStepStatus | None = None
    attempt_count: int | None = None
    warning_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.health in {
            PostMarketSyncHealth.HEALTHY,
            PostMarketSyncHealth.NO_DUE_SESSION,
        }
