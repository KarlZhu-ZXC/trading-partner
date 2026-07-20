"""Provider cache store port (Phase 1D D5a)."""

from __future__ import annotations

from typing import Protocol

from application.dto.provider_state import CacheEntry


class ProviderCacheStore(Protocol):
    def get(self, key: str) -> CacheEntry | None:
        """Return the entry for ``key``, including expired rows, or None if absent."""
        ...

    def set(self, key: str, entry: CacheEntry) -> None:
        """Upsert ``entry`` under ``key``. Caller owns HIT/STALE/MISS policy."""
        ...

    def delete(self, key: str) -> None:
        """Delete ``key`` if present; missing keys are a no-op (idempotent)."""
        ...
