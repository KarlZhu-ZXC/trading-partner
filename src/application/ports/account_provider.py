"""Read-only account provider port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.portfolio.models import AccountSnapshot


@runtime_checkable
class AccountProvider(CategoryProvider, Protocol):
    async def get_account_snapshots(
        self, *, as_of: datetime
    ) -> ProviderSuccess[tuple[AccountSnapshot, ...]]: ...
