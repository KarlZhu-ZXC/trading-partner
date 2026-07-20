"""Watchlist source provider protocol (Phase 2)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from application.dto.watchlist_source import (
    WatchlistSourceGroup,
    WatchlistSourceMembership,
)
from domain.watchlist.enums import WatchlistSource


@runtime_checkable
class WatchlistSourceProvider(Protocol):
    @property
    def source(self) -> WatchlistSource:
        """The upstream source this adapter talks to."""
        ...

    async def list_groups(self) -> tuple[WatchlistSourceGroup, ...]:
        """Read upstream watchlist groups."""

    async def list_memberships(
        self,
        group_name: str | None = None,
    ) -> tuple[WatchlistSourceMembership, ...]:
        """Read memberships in a group, or all groups when ``group_name`` is ``None``."""

    async def add_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
        display_name: str,
    ) -> WatchlistSourceMembership:
        """Add a membership (source-specific code) into a group."""

    async def remove_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
    ) -> WatchlistSourceMembership:
        """Remove a membership (source-specific code) from a group."""
