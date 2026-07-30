"""Select and evaluate due Monitor groups without an LLM scheduler."""

from __future__ import annotations

from application.dto.monitor_dispatch import (
    MonitorDispatchDisposition,
    MonitorDispatchDTO,
)
from application.dto.monitoring import MonitorEvaluateInput, MonitorRunDTO
from application.ports.clock import Clock
from application.ports.monitor_repository import MonitorRepository
from application.services.monitor_evaluation_service import MonitorEvaluationService
from application.services.monitor_notification_service import MonitorNotificationService
from application.services.monitor_schedule_service import MonitorScheduleService
from domain.monitoring.enums import MonitorCadence, MonitorStatus

_SCHEDULED_CADENCES = (
    MonitorCadence.A_SHARE_POST_MARKET,
    MonitorCadence.US_POST_MARKET,
    MonitorCadence.INTERVAL,
)


class MonitorDispatchService:
    def __init__(
        self,
        repository: MonitorRepository,
        evaluator: MonitorEvaluationService,
        notifications: MonitorNotificationService,
        schedule: MonitorScheduleService,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._evaluator = evaluator
        self._notifications = notifications
        self._schedule = schedule
        self._clock = clock

    async def run_due(self) -> MonitorDispatchDTO:
        return await self._run_due(frozenset(_SCHEDULED_CADENCES))

    async def run_due_intervals(self) -> MonitorDispatchDTO:
        """Compatibility entry used by focused interval tests and callers."""
        return await self._run_due(frozenset({MonitorCadence.INTERVAL}))

    async def _run_due(
        self,
        cadences: frozenset[MonitorCadence],
    ) -> MonitorDispatchDTO:
        now = self._clock.now()
        candidates = tuple(
            item
            for item in self._repository.list_current(MonitorStatus.ACTIVE)
            if item.cadence in cadences
            and (item.valid_until is None or now <= item.valid_until)
        )
        due = tuple(
            item
            for item in candidates
            if self._schedule.status(
                item,
                self._repository.latest_run_for_monitor(item.monitor_id),
                now,
            ).due
        )
        runs: list[MonitorRunDTO] = []
        for cadence in _SCHEDULED_CADENCES:
            if cadence not in cadences:
                continue
            monitor_ids = tuple(
                item.monitor_id for item in due if item.cadence is cadence
            )
            if not monitor_ids:
                continue
            run = await self._evaluator.evaluate(
                MonitorEvaluateInput(
                    monitor_ids=monitor_ids,
                    cadence=cadence,
                    as_of=now,
                )
            )
            runs.append(MonitorRunDTO.from_domain(run))

        next_due_values = tuple(
            status.next_due_at
            for item in candidates
            if (
                status := self._schedule.status(
                    item,
                    self._repository.latest_run_for_monitor(item.monitor_id),
                    now,
                )
            ).next_due_at
            is not None
        )
        notification_delivery = await self._notifications.flush_pending()
        return MonitorDispatchDTO(
            disposition=(
                MonitorDispatchDisposition.EXECUTED
                if runs
                else MonitorDispatchDisposition.NO_DUE_MONITORS
            ),
            checked_at=now,
            due_monitor_ids=tuple(item.monitor_id for item in due),
            next_due_at=min(next_due_values) if next_due_values else None,
            run=runs[0] if len(runs) == 1 else None,
            runs=tuple(runs),
            notification_delivery=notification_delivery,
        )
