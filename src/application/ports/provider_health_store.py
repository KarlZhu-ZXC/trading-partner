"""Provider health store port (Phase 1D D5a)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from application.dto.provider_state import ProviderHealthSnapshot
from domain.common.enums import CircuitState, DataCategory, VendorId


class ProviderHealthStore(Protocol):
    def record_success(
        self, vendor: VendorId, category: DataCategory, at: datetime
    ) -> None:
        """Increment success_count; state=OK; clear last_error_code."""
        ...

    def record_failure(
        self,
        vendor: VendorId,
        category: DataCategory,
        at: datetime,
        error_code: str,
    ) -> None:
        """Increment failure_count; state=ERROR; set last_error_code."""
        ...

    def set_circuit_state(
        self,
        vendor: VendorId,
        category: DataCategory,
        state: CircuitState,
        at: datetime,
    ) -> None:
        """Update circuit observation projection (not the process-local breaker)."""
        ...

    def get(self, vendor: VendorId, category: DataCategory) -> ProviderHealthSnapshot:
        """Return stored snapshot, or a zero virtual snapshot without writing."""
        ...
