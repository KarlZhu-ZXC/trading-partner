"""Versioned Monitoring configuration, queries, and event resolution."""

from __future__ import annotations

from application.dto.monitoring import (
    MonitorCreateInput,
    MonitorDefinitionDTO,
    MonitorDetailDTO,
    MonitorEventDTO,
    MonitorEventListDTO,
    MonitorEventListInput,
    MonitorEventResolveInput,
    MonitorGetInput,
    MonitorListDTO,
    MonitorListInput,
    MonitorRuleStateDTO,
    MonitorUpdateInput,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.monitor_repository import MonitorRepository
from application.services._research_support import UowFactory
from domain.common.errors import (
    DataContractError,
    IdempotencyConflict,
    MonitorEventNotFound,
    MonitorNotFound,
    MonitorVersionConflict,
)
from domain.common.ids import EntityIdPrefix
from domain.monitoring.enums import MonitorEventAction, MonitorStatus
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEventResolution,
)


class MonitorService:
    def __init__(
        self,
        repository: MonitorRepository,
        research_uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._repository = repository
        self._research_uow_factory = research_uow_factory
        self._clock = clock
        self._ids = id_generator

    def create(self, request: MonitorCreateInput) -> MonitorDetailDTO:
        existing = self._repository.get_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            if not self._matches_create(existing, request):
                raise IdempotencyConflict(
                    "idempotency_key belongs to a different monitor definition"
                )
            return self.get(MonitorGetInput(monitor_id=existing.monitor_id))
        self._validate_case(request.case_id)
        now = self._clock.now()
        value = MonitorDefinition(
            monitor_id=self._ids.new(EntityIdPrefix.MONITOR),
            version=1,
            name=request.name.strip(),
            case_id=request.case_id,
            primary_instrument_id=request.primary_instrument_id,
            cadence=request.cadence,
            status=MonitorStatus.ACTIVE,
            rules=tuple(item.to_domain() for item in request.rules),
            confirmed_by=request.confirmed_by,
            idempotency_key=request.idempotency_key.strip(),
            created_at=now,
        )
        self._repository.create(value)
        return MonitorDetailDTO(
            monitor=MonitorDefinitionDTO.from_domain(value),
            rule_states=(),
        )

    def update(self, request: MonitorUpdateInput) -> MonitorDetailDTO:
        replay = self._repository.get_by_idempotency_key(request.idempotency_key)
        if replay is not None:
            if replay.monitor_id != request.monitor_id or not self._matches_update(
                replay, request
            ):
                raise IdempotencyConflict(
                    "idempotency_key belongs to a different monitor update"
                )
            return self.get(MonitorGetInput(monitor_id=replay.monitor_id))
        current = self._require(request.monitor_id)
        if current.version != request.expected_version:
            raise MonitorVersionConflict(
                "expected_version does not match current monitor",
                details={
                    "expected_version": request.expected_version,
                    "current_version": current.version,
                },
            )
        self._validate_case(request.case_id)
        value = MonitorDefinition(
            monitor_id=current.monitor_id,
            version=current.version + 1,
            name=request.name.strip(),
            case_id=request.case_id,
            primary_instrument_id=request.primary_instrument_id,
            cadence=request.cadence,
            status=request.status,
            rules=tuple(item.to_domain() for item in request.rules),
            confirmed_by=request.confirmed_by,
            idempotency_key=request.idempotency_key.strip(),
            created_at=self._clock.now(),
        )
        self._repository.append_version(value)
        return self.get(MonitorGetInput(monitor_id=value.monitor_id))

    def get(self, request: MonitorGetInput) -> MonitorDetailDTO:
        monitor = self._require(request.monitor_id)
        current_rule_codes = {item.rule_code for item in monitor.rules}
        states = tuple(
            item
            for item in self._repository.get_rule_states(monitor.monitor_id)
            if item.rule_code in current_rule_codes
        )
        return MonitorDetailDTO(
            monitor=MonitorDefinitionDTO.from_domain(monitor),
            rule_states=tuple(MonitorRuleStateDTO.from_domain(item) for item in states),
        )

    def list(self, request: MonitorListInput) -> MonitorListDTO:
        return MonitorListDTO(
            monitors=tuple(
                MonitorDefinitionDTO.from_domain(item)
                for item in self._repository.list_current(request.status)
            )
        )

    def list_events(self, request: MonitorEventListInput) -> MonitorEventListDTO:
        events = self._repository.list_events(request.monitor_id, request.limit)
        return MonitorEventListDTO(
            events=tuple(
                MonitorEventDTO.from_domain(
                    item, self._repository.latest_resolution(item.event_id)
                )
                for item in events
            )
        )

    def resolve_event(
        self, request: MonitorEventResolveInput
    ) -> MonitorEventResolution:
        replay = self._repository.get_resolution_by_idempotency_key(
            request.idempotency_key
        )
        if replay is not None:
            if (
                replay.event_id != request.event_id
                or replay.action is not request.action
                or replay.note != request.note.strip()
                or replay.confirmed_by != request.confirmed_by
            ):
                raise IdempotencyConflict(
                    "idempotency_key belongs to a different event resolution"
                )
            return replay
        if self._repository.get_event(request.event_id) is None:
            raise MonitorEventNotFound("Monitor event was not found")
        latest = self._repository.latest_resolution(request.event_id)
        if latest is not None:
            if latest.action is MonitorEventAction.RESOLVE:
                raise DataContractError("Monitor event is already resolved")
            if request.action is MonitorEventAction.ACKNOWLEDGE:
                raise DataContractError("Monitor event is already acknowledged")
        value = MonitorEventResolution(
            resolution_id=self._ids.new(EntityIdPrefix.MONITOR_RESOLUTION),
            event_id=request.event_id,
            action=request.action,
            note=request.note.strip(),
            confirmed_by=request.confirmed_by,
            idempotency_key=request.idempotency_key.strip(),
            created_at=self._clock.now(),
        )
        return self._repository.append_resolution(value)

    def _require(self, monitor_id: str) -> MonitorDefinition:
        value = self._repository.get_current(monitor_id)
        if value is None:
            raise MonitorNotFound("Monitor was not found", details={"monitor_id": monitor_id})
        return value

    def _validate_case(self, case_id: str | None) -> None:
        if case_id is None:
            return
        with self._research_uow_factory() as uow:
            uow.cases.get(case_id)

    @staticmethod
    def _matches_create(value: MonitorDefinition, request: MonitorCreateInput) -> bool:
        return (
            value.version == 1
            and value.name == request.name.strip()
            and value.case_id == request.case_id
            and value.primary_instrument_id == request.primary_instrument_id
            and value.cadence is request.cadence
            and value.rules == tuple(item.to_domain() for item in request.rules)
            and value.confirmed_by == request.confirmed_by
        )

    @staticmethod
    def _matches_update(value: MonitorDefinition, request: MonitorUpdateInput) -> bool:
        return (
            value.version == request.expected_version + 1
            and value.name == request.name.strip()
            and value.case_id == request.case_id
            and value.primary_instrument_id == request.primary_instrument_id
            and value.cadence is request.cadence
            and value.status is request.status
            and value.rules == tuple(item.to_domain() for item in request.rules)
            and value.confirmed_by == request.confirmed_by
        )
