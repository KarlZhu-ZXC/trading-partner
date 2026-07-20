"""Provider fixed-window rate-limit store port (Phase 1D D5a)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from application.dto.provider_state import ProviderRateLimitSnapshot
from domain.common.enums import DataCategory, VendorId


class ProviderRateLimitStore(Protocol):
    def consume(
        self,
        *,
        vendor: VendorId,
        category: DataCategory,
        window_start: datetime,
        window_seconds: int,
        limit_count: int,
        at: datetime,
    ) -> ProviderRateLimitSnapshot:
        """Atomically create or increment the fixed-window counter; return snapshot.

        Allowed/denied is decided by the rate limiter policy using
        ``request_count <= limit_count``; this store only persists counts.
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
