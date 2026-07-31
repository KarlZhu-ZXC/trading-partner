"""Durable account/portfolio snapshot repository port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.portfolio.models import AccountSnapshot, PortfolioSnapshot


class AccountSnapshotRepository(Protocol):
    def append_account(self, snapshot: AccountSnapshot) -> AccountSnapshot: ...

    def get_account(self, snapshot_id: str) -> AccountSnapshot | None: ...

    def latest_accounts(self) -> tuple[AccountSnapshot, ...]: ...

    def list_account_history(
        self,
        *,
        account_ref: str,
        start: datetime,
        end: datetime,
    ) -> tuple[AccountSnapshot, ...]: ...

    def append_portfolio(self, snapshot: PortfolioSnapshot) -> PortfolioSnapshot: ...

    def get_portfolio(self, snapshot_id: str) -> PortfolioSnapshot | None: ...
