"""Application port for strictly recognized owner-only broker exports."""

from __future__ import annotations

from typing import Protocol

from domain.attribution.reconciliation_models import BrokerRealizedStatement


class BrokerStatementParser(Protocol):
    def parse_realized_gain_loss(self, relative_path: str) -> BrokerRealizedStatement:
        """Parse one recognized broker export without exposing its raw payload."""
        ...
