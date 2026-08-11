"""Durable broker-order intent and receipt boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.execution.models import BrokerOrderIntent


class BrokerOrderRepository(Protocol):
    def create_preview(self, value: BrokerOrderIntent) -> BrokerOrderIntent: ...
    def get(self, order_intent_id: str) -> BrokerOrderIntent | None: ...
    def get_by_preview_idempotency_key(self, key: str) -> BrokerOrderIntent | None: ...
    def claim_submit(
        self,
        *,
        order_intent_id: str,
        now: datetime,
        submit_idempotency_key: str,
        confirmed_by: str,
        submitted_via: str,
        authorization_note: str,
    ) -> BrokerOrderIntent: ...
    def mark_submitted(
        self,
        *,
        order_intent_id: str,
        broker_order_id: str,
        submitted_at: datetime,
        provider_status: str,
    ) -> BrokerOrderIntent: ...
    def mark_rejected(
        self, *, order_intent_id: str, code: str, now: datetime
    ) -> BrokerOrderIntent: ...
    def mark_unknown(
        self, *, order_intent_id: str, code: str, now: datetime
    ) -> BrokerOrderIntent: ...
    def mark_cancelled(
        self, *, order_intent_id: str, now: datetime
    ) -> BrokerOrderIntent: ...
