"""WatchlistItem repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.common.enums import Market, WatchlistItemStatus
from domain.research.models import WatchlistItem


class WatchlistRepository(Protocol):
    def get(self, item_id: str) -> WatchlistItem: ...

    def list(
        self,
        *,
        market: Market | None = None,
        status: WatchlistItemStatus | None = None,
        subject_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[WatchlistItem, ...]: ...

    def add(self, item: WatchlistItem) -> None: ...

    def update_status(
        self,
        item_id: str,
        *,
        new_status: WatchlistItemStatus,
        triggered_at: datetime | None,
        triggered_reason: str | None,
        promoted_to_subject_id: str | None,
        expires_at: datetime | None,
    ) -> None: ...
