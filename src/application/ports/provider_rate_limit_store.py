"""Provider fixed-window rate-limit store port (Phase 1D D5a)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from application.dto.provider_state import ProviderRateLimitSnapshot
from domain.common.enums import DataCategory, VendorId


class ProviderRateLimitStore(Protocol):
    def try_reserve(
        self,
        *,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
        window_seconds: int,
        limit_count: int,
        at: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        """Atomically reserve one slot when the fixed-window limit permits it.

        A newly created row starts at one.  On conflict the request count is
        incremented only when it is strictly below ``limit_count``.  A full
        window returns ``None`` and leaves its counter unchanged.
        """
        ...

    def get(
        self,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
    ) -> ProviderRateLimitSnapshot | None:
        """Return the snapshot for the exact window, or None if absent."""
        ...
