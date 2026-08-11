"""Provider port for normalized future catalyst calendars."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from application.dto.provider_routing import ProviderSuccess
from application.ports.category_provider import CategoryProvider
from domain.catalyst_agenda.calendar import CatalystCalendarBatch
from domain.instruments.models import Instrument


@runtime_checkable
class CatalystCalendarProvider(CategoryProvider, Protocol):
    async def get_catalyst_calendar(
        self,
        instrument: Instrument | None,
        *,
        start: date,
        end: date,
        as_of: datetime,
        release_ids: tuple[int, ...] = (),
    ) -> ProviderSuccess[CatalystCalendarBatch]: ...
