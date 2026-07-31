"""CLI-only preparation boundary for independent broker-statement reconciliation."""

from __future__ import annotations

from application.dto.performance_reconciliation import BrokerRealizedStatementDTO
from application.ports.broker_statement_parser import BrokerStatementParser


class PerformanceReconciliationService:
    def __init__(self, parser: BrokerStatementParser) -> None:
        self._parser = parser

    def inspect_schwab_realized_gain_loss(
        self, relative_path: str
    ) -> BrokerRealizedStatementDTO:
        """Return only redacted account summaries; raw rows never cross the Provider."""

        return BrokerRealizedStatementDTO.from_domain(
            self._parser.parse_realized_gain_loss(relative_path)
        )
