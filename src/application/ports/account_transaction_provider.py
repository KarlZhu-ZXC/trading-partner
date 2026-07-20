"""Optional read-only historical account transaction provider port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from domain.portfolio.models import AccountTransaction


@runtime_checkable
class AccountTransactionProvider(Protocol):
    def is_configured(self) -> bool: ...

    async def get_account_transactions(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> ProviderSuccess[tuple[AccountTransaction, ...]]: ...
