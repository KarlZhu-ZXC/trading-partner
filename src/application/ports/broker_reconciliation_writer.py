"""Application port for owner-only broker reconciliation draft artifacts."""

from __future__ import annotations

from typing import Protocol

from domain.attribution.reconciliation_models import BrokerRealizedReconciliation


class BrokerReconciliationWriter(Protocol):
    def write_draft(self, value: BrokerRealizedReconciliation) -> str:
        """Persist a redacted immutable draft and return its safe relative reference."""
        ...
