"""Persistence ports for Journal activation and Daily Equity projections."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.performance.daily_equity import (
    DailyEquityMaterializationWriteResult,
    DailyEquitySnapshot,
    JournalActivation,
)


class JournalActivationRepository(Protocol):
    def get_activation(self) -> JournalActivation | None: ...

    def activate(self, value: JournalActivation) -> JournalActivation: ...


class DailyEquityRepository(Protocol):
    def append(self, value: DailyEquitySnapshot) -> DailyEquitySnapshot: ...

    def append_many(
        self,
        values: tuple[DailyEquitySnapshot, ...],
    ) -> DailyEquityMaterializationWriteResult: ...

    def get(self, daily_equity_snapshot_id: str) -> DailyEquitySnapshot | None: ...

    def get_by_source_snapshot(
        self,
        *,
        source_snapshot_id: str,
        algorithm_version: str = "daily_equity_v1",
    ) -> DailyEquitySnapshot | None: ...

    def list(
        self,
        *,
        account_refs: tuple[str, ...] = (),
        currencies: tuple[str, ...] = (),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[DailyEquitySnapshot, ...]: ...


# Explicit aliases make the contract discoverable without introducing a
# second implementation or a second source of truth.
DailyEquitySnapshotRepository = DailyEquityRepository
