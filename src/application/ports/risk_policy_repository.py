"""Append-only repository protocol for risk policy snapshots."""

from __future__ import annotations

from typing import Protocol

from domain.risk.models import RiskPolicy


class RiskPolicyRepository(Protocol):
    def get_current(self) -> RiskPolicy | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> RiskPolicy | None: ...

    def append(self, policy: RiskPolicy) -> RiskPolicy: ...

