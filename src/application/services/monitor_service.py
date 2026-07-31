"""Versioned Monitoring configuration, queries, and event resolution."""

from __future__ import annotations

from datetime import datetime

from application.dto.monitoring import (
    MonitorCreateInput,
    MonitorDashboardDTO,
    MonitorDashboardInput,
    MonitorDashboardItemDTO,
    MonitorDefinitionDTO,
    MonitorDetailDTO,
    MonitorEventDTO,
    MonitorEventListDTO,
    MonitorEventListInput,
    MonitorEventResolveInput,
    MonitorGetInput,
    MonitorLatestRunSummaryDTO,
    MonitorListDTO,
    MonitorListInput,
    MonitorRuleStateDTO,
    MonitorRunDTO,
    MonitorRunListDTO,
    MonitorRunListInput,
    MonitorUpdateInput,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.monitor_repository import MonitorRepository
from application.services._research_support import UowFactory
from application.services.monitor_schedule_service import MonitorScheduleService
from domain.common.errors import (
    DataContractError,
    IdempotencyConflict,
    MonitorEventNotFound,
    MonitorNotFound,
    MonitorVersionConflict,
)
from domain.common.ids import EntityIdPrefix
from domain.monitoring.enums import (
    MonitorEventAction,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEventResolution,
    MonitorRule,
    MonitorRun,
)
from domain.trade_plan.enums import TradePlanConditionMode, TradePlanStatus


class MonitorService:
    def __init__(
        self,
        repository: MonitorRepository,
        research_uow_factory: UowFactory,
        clock: Clock,
        id_generator: IdGenerator,
        schedule: MonitorScheduleService | None = None,
    ) -> None:
        self._repository = repository
        self._research_uow_factory = research_uow_factory
        self._clock = clock
        self._ids = id_generator
        self._schedule = schedule or MonitorScheduleService()

    def create(self, request: MonitorCreateInput) -> MonitorDetailDTO:
        rules, case_id, instrument_id, valid_until = self._resolve_definition_inputs(request)
        existing = self._repository.get_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            if not self._matches_create(
                existing, request, rules, case_id, instrument_id, valid_until
            ):
                raise IdempotencyConflict(
                    "idempotency_key belongs to a different monitor definition"
                )
            return self.get(MonitorGetInput(monitor_id=existing.monitor_id))
        self._validate_case(case_id)
        now = self._clock.now()
        value = MonitorDefinition(
            monitor_id=self._ids.new(EntityIdPrefix.MONITOR),
            version=1,
            name=request.name.strip(),
            case_id=case_id,
            primary_instrument_id=instrument_id,
            trade_plan_id=request.trade_plan_id,
            trade_plan_version=request.trade_plan_version,
            cadence=request.cadence,
            interval_minutes=request.interval_minutes,
            status=MonitorStatus.ACTIVE,
            rules=rules,
            confirmed_by=request.confirmed_by,
            idempotency_key=request.idempotency_key.strip(),
            created_at=now,
            valid_until=valid_until,
        )
        self._repository.create(value)
        return MonitorDetailDTO(
            monitor=MonitorDefinitionDTO.from_domain(value),
            rule_states=(),
        )

    def update(self, request: MonitorUpdateInput) -> MonitorDetailDTO:
        rules, case_id, instrument_id, valid_until = self._resolve_definition_inputs(request)
        replay = self._repository.get_by_idempotency_key(request.idempotency_key)
        if replay is not None:
            if replay.monitor_id != request.monitor_id or not self._matches_update(
                replay, request, rules, case_id, instrument_id, valid_until
            ):
                raise IdempotencyConflict("idempotency_key belongs to a different monitor update")
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
        self._validate_case(case_id)
        value = MonitorDefinition(
            monitor_id=current.monitor_id,
            version=current.version + 1,
            name=request.name.strip(),
            case_id=case_id,
            primary_instrument_id=instrument_id,
            trade_plan_id=request.trade_plan_id,
            trade_plan_version=request.trade_plan_version,
            cadence=request.cadence,
            interval_minutes=request.interval_minutes,
            status=request.status,
            rules=rules,
            confirmed_by=request.confirmed_by,
            idempotency_key=request.idempotency_key.strip(),
            created_at=self._clock.now(),
            valid_until=valid_until,
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

    def dashboard(self, request: MonitorDashboardInput) -> MonitorDashboardDTO:
        now = self._clock.now()
        items: list[MonitorDashboardItemDTO] = []
        for monitor in self._repository.list_current(request.status):
            latest = self._repository.latest_run_for_monitor(monitor.monitor_id)
            schedule = self._schedule.status(
                monitor,
                latest,
                now,
            )
            current_rule_codes = {item.rule_code for item in monitor.rules}
            states = tuple(
                MonitorRuleStateDTO.from_domain(item)
                for item in self._repository.get_rule_states(monitor.monitor_id)
                if item.rule_code in current_rule_codes
            )
            items.append(
                MonitorDashboardItemDTO(
                    monitor=MonitorDefinitionDTO.from_domain(monitor),
                    monitor_created_at=(
                        self._repository.get_created_at(monitor.monitor_id) or monitor.created_at
                    ),
                    monitor_updated_at=monitor.created_at,
                    rule_states=states,
                    latest_run=(
                        MonitorLatestRunSummaryDTO.from_domain(latest)
                        if latest is not None
                        else None
                    ),
                    last_run_at=latest.completed_at if latest is not None else None,
                    next_due_at=schedule.next_due_at,
                    due=schedule.due,
                    schedule_health=schedule.health,
                )
            )
        return MonitorDashboardDTO(generated_at=now, items=tuple(items))

    def list_runs(self, request: MonitorRunListInput) -> MonitorRunListDTO:
        values: tuple[MonitorRun, ...]
        if request.run_id is not None:
            value = self._repository.get_run(request.run_id)
            values = () if value is None else (value,)
        else:
            values = self._repository.list_runs(request.monitor_id, request.limit)
        return MonitorRunListDTO(runs=tuple(MonitorRunDTO.from_domain(item) for item in values))

    def list_events(self, request: MonitorEventListInput) -> MonitorEventListDTO:
        events = self._repository.list_events(request.monitor_id, request.limit)
        return MonitorEventListDTO(
            events=tuple(
                MonitorEventDTO.from_domain(item, self._repository.latest_resolution(item.event_id))
                for item in events
            )
        )

    def resolve_event(self, request: MonitorEventResolveInput) -> MonitorEventResolution:
        replay = self._repository.get_resolution_by_idempotency_key(request.idempotency_key)
        if replay is not None:
            if (
                replay.event_id != request.event_id
                or replay.action is not request.action
                or replay.note != request.note.strip()
                or replay.confirmed_by != request.confirmed_by
            ):
                raise IdempotencyConflict("idempotency_key belongs to a different event resolution")
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

    def _resolve_definition_inputs(
        self, request: MonitorCreateInput | MonitorUpdateInput
    ) -> tuple[tuple[MonitorRule, ...], str | None, str | None, datetime | None]:
        rules = list(item.to_domain() for item in request.rules)
        case_id = request.case_id
        instrument_id = request.primary_instrument_id
        valid_until = request.valid_until
        if request.trade_plan_id is not None:
            assert request.trade_plan_version is not None
            with self._research_uow_factory() as uow:
                plan = uow.trade_plans.get_version(
                    request.trade_plan_id, request.trade_plan_version
                )
            if plan is None:
                raise DataContractError("Specified Trade Plan version was not found")
            if plan.status is not TradePlanStatus.ACTIVE:
                raise DataContractError("Monitor compilation requires an ACTIVE Trade Plan")
            if case_id is not None and case_id != plan.case_id:
                raise DataContractError("Monitor case_id conflicts with Trade Plan")
            if instrument_id is not None and instrument_id != plan.instrument_id:
                raise DataContractError("Monitor primary_instrument_id conflicts with Trade Plan")
            case_id = plan.case_id
            instrument_id = plan.instrument_id
            if plan.valid_until is not None and (
                valid_until is None or plan.valid_until < valid_until
            ):
                valid_until = plan.valid_until
            if request.compile_trade_plan_conditions:
                for condition in plan.conditions:
                    if condition.mode is TradePlanConditionMode.MANUAL:
                        continue
                    assert condition.fact_type is not None
                    assert condition.metric_key is not None
                    assert condition.comparator is not None
                    assert condition.max_fact_age_seconds is not None
                    rules.append(
                        MonitorRule(
                            rule_code=condition.condition_code,
                            description=condition.description,
                            rule_type=MonitorRuleType.FACT_COMPARISON,
                            severity=MonitorSeverity(condition.severity),
                            instrument_id=condition.instrument_id,
                            price_threshold=None,
                            risk_status_threshold=None,
                            max_fact_age_seconds=condition.max_fact_age_seconds,
                            fact_type=condition.fact_type,
                            metric_key=condition.metric_key,
                            comparator=condition.comparator,
                            numeric_threshold=condition.threshold,
                            event_after=condition.event_after,
                        )
                    )
        if not rules:
            raise DataContractError("Monitor has no machine-evaluable rules")
        if len(rules) > 50:
            raise DataContractError("Monitor supports at most 50 rules")
        codes = [item.rule_code for item in rules]
        if len(codes) != len(set(codes)):
            raise DataContractError("Monitor rule_code values must be unique")
        return tuple(rules), case_id, instrument_id, valid_until

    @staticmethod
    def _matches_create(
        value: MonitorDefinition,
        request: MonitorCreateInput,
        rules: tuple[MonitorRule, ...],
        case_id: str | None,
        instrument_id: str | None,
        valid_until: datetime | None,
    ) -> bool:
        return (
            value.version == 1
            and value.name == request.name.strip()
            and value.case_id == case_id
            and value.primary_instrument_id == instrument_id
            and value.trade_plan_id == request.trade_plan_id
            and value.trade_plan_version == request.trade_plan_version
            and value.cadence is request.cadence
            and value.interval_minutes == request.interval_minutes
            and value.rules == rules
            and value.valid_until == valid_until
            and value.confirmed_by == request.confirmed_by
        )

    @staticmethod
    def _matches_update(
        value: MonitorDefinition,
        request: MonitorUpdateInput,
        rules: tuple[MonitorRule, ...],
        case_id: str | None,
        instrument_id: str | None,
        valid_until: datetime | None,
    ) -> bool:
        return (
            value.version == request.expected_version + 1
            and value.name == request.name.strip()
            and value.case_id == case_id
            and value.primary_instrument_id == instrument_id
            and value.trade_plan_id == request.trade_plan_id
            and value.trade_plan_version == request.trade_plan_version
            and value.cadence is request.cadence
            and value.interval_minutes == request.interval_minutes
            and value.status is request.status
            and value.rules == rules
            and value.valid_until == valid_until
            and value.confirmed_by == request.confirmed_by
        )
