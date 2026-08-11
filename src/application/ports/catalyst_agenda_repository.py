"""Persistence port for Catalyst Agenda identities and append-only versions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.catalyst_agenda.models import CatalystAgendaIdentity, CatalystAgendaVersion


class CatalystAgendaRepository(Protocol):
    def get_by_idempotency_key(self, key: str) -> CatalystAgendaVersion | None: ...

    def get_by_logical_key(self, key: str) -> CatalystAgendaVersion | None: ...

    def get_current(self, agenda_item_id: str) -> CatalystAgendaVersion | None: ...

    def get_current_by_logical_key(self, logical_key: str) -> CatalystAgendaVersion | None: ...

    def append_initial(
        self, identity: CatalystAgendaIdentity, value: CatalystAgendaVersion
    ) -> CatalystAgendaVersion: ...

    def append_version(
        self, value: CatalystAgendaVersion, *, expected_version: int
    ) -> CatalystAgendaVersion: ...

    def list_visible(self, *, as_of: datetime) -> tuple[CatalystAgendaVersion, ...]: ...
