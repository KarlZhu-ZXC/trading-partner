"""Durable Monitoring repository protocol."""

from typing import Protocol

from domain.monitoring.enums import MonitorStatus
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorEventResolution,
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
    ) -> MonitorRun: ...

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
