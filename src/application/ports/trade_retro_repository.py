"""Persistence port for immutable Trade Retro records."""

from datetime import datetime
from typing import Protocol

from domain.retro.models import (
    TradeRetroExportReceipt,
    TradeRetroPlanSnapshot,
    TradeRetroReviewRevision,
    TradeRetroRun,
)


class TradeRetroRepository(Protocol):
    def append_plan_snapshot(self, value: TradeRetroPlanSnapshot) -> TradeRetroPlanSnapshot: ...

    def get_plan_snapshot_by_idempotency_key(
        self, key: str
    ) -> TradeRetroPlanSnapshot | None: ...

    def get_plan_snapshot(self, snapshot_id: str) -> TradeRetroPlanSnapshot | None: ...

    def latest_plan_snapshot_for_period(
        self, *, period_start: datetime, period_end: datetime
    ) -> TradeRetroPlanSnapshot | None: ...

    def append_run(self, value: TradeRetroRun) -> TradeRetroRun: ...

    def get_run(self, run_id: str) -> TradeRetroRun | None: ...

    def get_run_by_idempotency_key(self, key: str) -> TradeRetroRun | None: ...

    def list_runs(self, limit: int) -> tuple[TradeRetroRun, ...]: ...

    def append_review(
        self, value: TradeRetroReviewRevision
    ) -> TradeRetroReviewRevision: ...

    def get_review_by_idempotency_key(
        self, key: str
    ) -> TradeRetroReviewRevision | None: ...

    def latest_review(self, run_id: str) -> TradeRetroReviewRevision | None: ...

    def list_reviews(
        self, run_id: str, *, limit: int = 100
    ) -> tuple[TradeRetroReviewRevision, ...]: ...

    def append_export(self, value: TradeRetroExportReceipt) -> TradeRetroExportReceipt: ...

    def get_export_by_idempotency_key(
        self, key: str
    ) -> TradeRetroExportReceipt | None: ...
