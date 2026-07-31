"""Persistence port for bounded, secret-safe Provider route history."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from application.dto.provider_route_history import ProviderRouteReceipt


class ProviderRouteHistoryStore(Protocol):
    @property
    def is_durable(self) -> bool:
        """Whether receipts survive process restart."""
        ...

    def append(self, receipt: ProviderRouteReceipt) -> None:
        """Append one receipt and enforce the store's bounded retention."""
        ...

    def list_since(
        self, since: datetime, *, limit: int
    ) -> tuple[ProviderRouteReceipt, ...]:
        """Return newest-first receipts recorded at or after ``since``."""
        ...
