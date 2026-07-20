"""DecisionRecord repository port (Phase 1C C2b)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.research.models import DecisionRecord


class DecisionRecordRepository(Protocol):
    def add(
        self,
        decision: DecisionRecord,
        *,
        idempotency_key: str,
        idempotency_payload_sha256: str,
    ) -> None: ...

    def get(self, decision_id: str) -> DecisionRecord: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> DecisionRecord | None: ...

    def list_by_case(
        self, case_id: str, *, as_of: datetime | None = None
    ) -> tuple[DecisionRecord, ...]: ...
