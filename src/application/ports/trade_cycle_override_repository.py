"""Persistence port for append-only Trade Cycle manual overrides."""

from __future__ import annotations

from typing import Protocol

from domain.portfolio.trade_cycle_overrides import TradeCycleOverrideRevision


class TradeCycleOverrideRepository(Protocol):
    def append(
        self,
        value: TradeCycleOverrideRevision,
        *,
        expected_version: int | None = None,
    ) -> TradeCycleOverrideRevision: ...

    def get_by_idempotency_key(self, key: str) -> TradeCycleOverrideRevision | None: ...

    def get_latest(self, root_cycle_id: str) -> TradeCycleOverrideRevision | None: ...

    def list(
        self,
        *,
        root_cycle_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[TradeCycleOverrideRevision, ...]: ...
