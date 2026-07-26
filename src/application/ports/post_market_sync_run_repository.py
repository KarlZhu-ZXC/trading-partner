"""Persistence port for terminal post-market synchronization receipts."""

from datetime import date
from typing import Protocol

from domain.operations.models import PostMarketSyncRun


class PostMarketSyncRunRepository(Protocol):
    def get_for_session(self, session_date: date) -> PostMarketSyncRun | None: ...

    def get_latest(self) -> PostMarketSyncRun | None: ...

    def save(self, run: PostMarketSyncRun) -> PostMarketSyncRun: ...
