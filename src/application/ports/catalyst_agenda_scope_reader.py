"""Durable-only scope reader for Catalyst Agenda projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.catalyst_agenda.enums import AgendaScopeReason
from domain.common.enums import ResearchSubjectType
from domain.instruments.models import Instrument


@dataclass(frozen=True, slots=True)
class AgendaScopeEntry:
    instrument_id: str | None
    subject_id: str | None
    reasons: tuple[AgendaScopeReason, ...]
    subject_type: ResearchSubjectType | None = None


@dataclass(frozen=True, slots=True)
class AgendaScopeSnapshot:
    entries: tuple[AgendaScopeEntry, ...]
    basis: str = "CURRENT_DURABLE"


class CatalystAgendaScopeReader(Protocol):
    def read_current(self) -> AgendaScopeSnapshot: ...

    def subject_exists(self, subject_id: str) -> bool: ...

    def instrument_exists(self, instrument_id: str) -> bool: ...

    def get_instrument(self, instrument_id: str) -> Instrument | None: ...
