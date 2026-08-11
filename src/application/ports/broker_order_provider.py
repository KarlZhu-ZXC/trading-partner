"""Closed Schwab order-write port; no generic HTTP escape hatch."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from domain.execution.models import (
    BrokerExecutionAccountState,
    BrokerOrderStatusObservation,
    BrokerOrderSubmission,
)


class BrokerOrderProvider(Protocol):
    async def get_account_state(
        self, *, account_ref: str, observed_at: datetime
    ) -> BrokerExecutionAccountState: ...

    async def place_order(
        self, *, account_ref: str, order_payload: Mapping[str, object]
    ) -> BrokerOrderSubmission: ...

    async def get_order(
        self, *, account_ref: str, broker_order_id: str, observed_at: datetime
    ) -> BrokerOrderStatusObservation: ...

    async def cancel_order(self, *, account_ref: str, broker_order_id: str) -> None: ...
