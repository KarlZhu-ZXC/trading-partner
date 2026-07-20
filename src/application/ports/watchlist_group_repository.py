"""Watchlist hub group repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.watchlist.enums import WatchlistSource
from domain.watchlist.models import WatchlistGroup


class WatchlistGroupRepository(Protocol):
    def get(self, group_id: str) -> WatchlistGroup: ...

    def get_by_source_key(
        self,
        source: WatchlistSource,
        source_group_key: str,
    ) -> WatchlistGroup | None: ...

    def list(
        self,
        *,
        source: WatchlistSource | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WatchlistGroup, ...]: ...

    def upsert(self, group: WatchlistGroup) -> WatchlistGroup: ...

    def mark_inactive(
        self,
        group_id: str,
        *,
        removed_at: datetime,
    ) -> None: ...

