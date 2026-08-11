"""Append-only Catalyst Agenda sync receipt persistence port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.catalyst_agenda.calendar import CatalystAgendaSyncReceipt


class CatalystAgendaSyncRepository(Protocol):
    def get_by_idempotency_key(self, key: str) -> CatalystAgendaSyncReceipt | None: ...

    def append(self, receipt: CatalystAgendaSyncReceipt) -> CatalystAgendaSyncReceipt: ...

    def latest(self) -> CatalystAgendaSyncReceipt | None: ...

    def list_since(
        self, since: datetime, *, limit: int = 20
    ) -> tuple[CatalystAgendaSyncReceipt, ...]: ...
