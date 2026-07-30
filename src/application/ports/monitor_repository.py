"""Durable Monitoring repository protocol."""

from datetime import datetime
from typing import Protocol

from domain.monitoring.enums import (
    MonitorNotificationChannel,
    MonitorNotificationStatus,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorEventResolution,
    MonitorNotificationMessage,
    MonitorNotificationOutboxEntry,
    MonitorRuleState,
    MonitorRun,
)


class MonitorRepository(Protocol):
    def create(self, monitor: MonitorDefinition) -> MonitorDefinition: ...

    def append_version(self, monitor: MonitorDefinition) -> MonitorDefinition: ...

    def get_current(self, monitor_id: str) -> MonitorDefinition | None: ...

    def get_by_idempotency_key(self, key: str) -> MonitorDefinition | None: ...

    def list_current(
        self, status: MonitorStatus | None = None
    ) -> tuple[MonitorDefinition, ...]: ...

    def get_rule_states(self, monitor_id: str) -> tuple[MonitorRuleState, ...]: ...

    def record_evaluation(
        self,
        run: MonitorRun,
        states: tuple[MonitorRuleState, ...],
        events: tuple[MonitorEvent, ...],
        notifications: tuple[MonitorNotificationMessage, ...],
    ) -> MonitorRun: ...

    def list_due_notifications(
        self,
        channel: MonitorNotificationChannel,
        as_of: datetime,
        limit: int,
    ) -> tuple[MonitorNotificationOutboxEntry, ...]: ...

    def record_notification_attempt(
        self,
        source_event_id: str,
        channel: MonitorNotificationChannel,
        *,
        status: MonitorNotificationStatus,
        attempted_at: datetime,
        next_attempt_at: datetime,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> MonitorNotificationOutboxEntry: ...

    def notification_counts(
        self, channel: MonitorNotificationChannel
    ) -> dict[MonitorNotificationStatus, int]: ...

    def last_notification_delivery_at(
        self, channel: MonitorNotificationChannel
    ) -> datetime | None: ...

    def get_run(self, run_id: str) -> MonitorRun | None: ...

    def list_runs(
        self, monitor_id: str | None, limit: int
    ) -> tuple[MonitorRun, ...]: ...

    def latest_run_for_monitor(self, monitor_id: str) -> MonitorRun | None: ...

    def get_event(self, event_id: str) -> MonitorEvent | None: ...

    def list_events(
        self, monitor_id: str | None, limit: int
    ) -> tuple[MonitorEvent, ...]: ...

    def append_resolution(
        self, resolution: MonitorEventResolution
    ) -> MonitorEventResolution: ...

    def get_resolution_by_idempotency_key(
        self, key: str
    ) -> MonitorEventResolution | None: ...

    def latest_resolution(self, event_id: str) -> MonitorEventResolution | None: ...
