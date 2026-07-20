"""Watchlist hub mutation repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.watchlist.enums import WatchlistMutationStatus
from domain.watchlist.models import WatchlistMutation


class WatchlistMutationRepository(Protocol):
    def get(self, mutation_id: str) -> WatchlistMutation: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> WatchlistMutation | None: ...

    def add(self, mutation: WatchlistMutation) -> None: ...

    def update_status(
        self,
        mutation_id: str,
        *,
        status: WatchlistMutationStatus,
        completed_at: datetime,
        error_code: str | None,
    ) -> None: ...

