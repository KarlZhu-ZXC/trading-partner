"""Provider router settings port (Phase 1D D6b2).

Application Protocol exposing only non-secret fields and helpers needed by
``ProviderRouterEngine``. The engine must not import the concrete settings
implementation class; production settings must satisfy this Protocol
structurally without extra config keys.
"""

from __future__ import annotations

from typing import Protocol

from domain.common.enums import DataCategory


class ProviderRouterSettings(Protocol):
    """Non-secret settings surface for ProviderRouterEngine orchestration."""

    enable_provider_cache: bool
    enable_circuit_breaker: bool
    auth_failure_fallback: bool

    provider_retry_max_attempts: int
    provider_retry_base_delay_seconds: float
    provider_retry_max_delay_seconds: float

    stale_guard_max_age_seconds: int
    stale_guard_respect_session: bool
    stale_guard_allow_closed_last_bar: bool

    def timeout_for(self, category: DataCategory) -> float:
        """Return provider call timeout seconds for ``category``."""
        ...

    def cache_ttl_for(self, category: DataCategory) -> int:
        """Return positive cache TTL seconds for ``category``."""
        ...
