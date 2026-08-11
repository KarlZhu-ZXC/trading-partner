from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from application.dto.catalyst_agenda import AgendaItemDTO
from application.services.catalyst_agenda_notification_service import (
    CatalystAgendaNotificationService,
)
from application.services.notification_service import NotificationService
from conftest import FixedClock
from domain.catalyst_agenda.enums import (
    AgendaDateCertainty,
    AgendaItemKind,
    AgendaItemStatus,
    AgendaSourceType,
)
from domain.catalyst_agenda.models import CatalystAgendaVersion
from domain.notifications.enums import NotificationSourceType
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository

NOW = datetime(2026, 8, 9, 8, tzinfo=UTC)


def _version(
    *,
    version: int = 1,
    status: AgendaItemStatus = AgendaItemStatus.UPCOMING,
    recorded_at: datetime = NOW,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> CatalystAgendaVersion:
    start = window_start or NOW + timedelta(days=2)
    end = window_end or start
    return CatalystAgendaVersion(
        agenda_item_id="agenda_00000000-0000-7000-8000-000000000020",
        version=version,
        supersedes_version=None if version == 1 else version - 1,
        instrument_id="equity:US:NVDA",
        subject_id=None,
        kind=AgendaItemKind.EARNINGS,
        title="NVDA earnings",
        fiscal_period="FY2026Q2",
        upstream_event_key="NVDA:FY2026Q2",
        window_start=start,
        window_end=end,
        timezone="America/New_York",
        date_certainty=AgendaDateCertainty.CONFIRMED,
        status=status,
        source_type=AgendaSourceType.PROVIDER,
        source_vendor="YAHOO",
        source_reference="calendar",
        source_visible_at=recorded_at,
        last_verified_at=recorded_at,
        expected_question="Did data-center growth remain durable?",
        linked_event_id=None,
        linked_report_id=None,
        revision_note=None,
        created_by="system",
        confirmed_by="system",
        authorization_note="provider_sync:agenda_sync_1",
        idempotency_key=f"agenda-version-{version}",
        request_fingerprint=str(version) * 64,
        historical_vintage=False,
        recorded_at=recorded_at,
    )


class _AgendaRepository:
    def __init__(self, values: tuple[CatalystAgendaVersion, ...]) -> None:
        self.values = values

    def list_visible(self, *, as_of: datetime) -> tuple[CatalystAgendaVersion, ...]:
        return tuple(value for value in self.values if value.recorded_at <= as_of)


def _notification_service(engine, clock: FixedClock) -> NotificationService:
    return NotificationService(
        SqlAlchemyMonitorRepository(engine),
        None,
        clock,
        enabled=False,
        configured=False,
    )


@pytest.mark.asyncio
async def test_daily_agenda_preview_and_send_are_mobile_first_and_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    clock = FixedClock(NOW)
    notifications = _notification_service(engine, clock)
    item = AgendaItemDTO.from_domain(_version())
    agenda = MagicMock()
    agenda.query.return_value = SimpleNamespace(
        ok=True,
        data=SimpleNamespace(
            items=(item,),
            coverage=(SimpleNamespace(status="UNAVAILABLE"),),
            limitation_codes=("AGENDA_COVERAGE_UNAVAILABLE",),
        ),
        errors=(),
    )
    service = CatalystAgendaNotificationService(
        agenda,
        _AgendaRepository((_version(),)),
        notifications,
        clock,
    )

    preview = service.preview_daily()
    first = await service.enqueue_daily()
    replay = await service.enqueue_daily()

    agenda.query.return_value = SimpleNamespace(
        ok=True,
        data=SimpleNamespace(items=(), coverage=(), limitation_codes=()),
        errors=(),
    )
    changed_same_day = await service.enqueue_daily()

    assert "NVDA earnings" in preview.body
    assert "|" not in preview.body
    assert preview.coverage_gap_count == 1
    assert first.notification_id == replay.notification_id
    assert changed_same_day.notification_id == first.notification_id
    entry = notifications.recent_entries(1)[0]
    assert entry.source_type is NotificationSourceType.SYSTEM
    assert entry.confirmed_by is None
    engine.dispose()


@pytest.mark.asyncio
async def test_change_notifications_only_cover_reschedules_and_cancellations() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    clock = FixedClock(NOW)
    notifications = _notification_service(engine, clock)
    previous = _version(recorded_at=NOW - timedelta(hours=2))
    rescheduled = replace(
        _version(version=2, recorded_at=NOW - timedelta(hours=1)),
        window_start=NOW + timedelta(days=3),
        window_end=NOW + timedelta(days=3),
    )
    agenda = MagicMock()
    service = CatalystAgendaNotificationService(
        agenda,
        _AgendaRepository((previous, rescheduled)),
        notifications,
        clock,
    )

    first = await service.enqueue_changes()
    replay = await service.enqueue_changes()

    assert len(first) == 1
    assert first[0].change_type == "日期变更"
    assert replay[0].notification_id == first[0].notification_id
    assert len(notifications.recent_entries()) == 1
    engine.dispose()
