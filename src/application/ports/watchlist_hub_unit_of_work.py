"""Watchlist hub Unit of Work protocol."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from application.ports.audit_log_writer import AuditLogWriter
from application.ports.watchlist_group_repository import WatchlistGroupRepository
from application.ports.watchlist_membership_repository import (
    WatchlistMembershipRepository,
)
from application.ports.watchlist_mutation_repository import (
    WatchlistMutationRepository,
)


class WatchlistHubUnitOfWork(Protocol):
    def __enter__(self) -> WatchlistHubUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    @property
    def groups(self) -> WatchlistGroupRepository: ...

    @property
    def memberships(self) -> WatchlistMembershipRepository: ...

    @property
    def mutations(self) -> WatchlistMutationRepository: ...

    @property
    def audit(self) -> AuditLogWriter: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
