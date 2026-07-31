"""Watchlist hub membership repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.watchlist.models import WatchlistMembership


class WatchlistMembershipRepository(Protocol):
    def get(self, membership_id: str) -> WatchlistMembership: ...

    def get_by_code(
        self,
        group_id: str,
        provider_code: str,
    ) -> WatchlistMembership | None: ...

    def list(
        self,
        *,
        group_id: str,
        include_inactive: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[WatchlistMembership, ...]: ...

    def count(self, *, group_id: str, include_inactive: bool = False) -> int: ...

    def upsert(self, membership: WatchlistMembership) -> WatchlistMembership: ...

    def mark_inactive(
        self,
        membership_id: str,
        *,
        removed_at: datetime,
    ) -> None: ...

    def mark_inactive_not_seen(
        self,
        *,
        group_id: str,
        seen_provider_codes: tuple[str, ...],
        removed_at: datetime,
    ) -> int: ...
