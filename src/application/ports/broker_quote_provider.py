"""Read-only broker quote port for deterministic order previews."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.execution.models import BrokerQuoteObservation


class BrokerQuoteProvider(Protocol):
    async def get_quote(
        self, *, instrument_id: str, as_of: datetime
    ) -> BrokerQuoteObservation: ...
