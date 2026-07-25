"""SQLAlchemy persistence for Phase 2C Monitoring."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.common.errors import PersistenceError
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventAction,
    MonitorEventType,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorEventResolution,
    MonitorRule,
    MonitorRuleState,
    MonitorRun,
)
from domain.risk.enums import RiskOverallStatus
from infrastructure.persistence.models import (
    MonitorEventResolutionRow,
    MonitorEventRow,
    MonitorIdentityRow,
    MonitorRuleStateRow,
    MonitorRunRow,
    MonitorVersionRow,
)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _rules_to_json(rules: tuple[MonitorRule, ...]) -> str:
    return json.dumps(
        [
            {
                "rule_code": item.rule_code,
                "rule_type": item.rule_type.value,
                "severity": item.severity.value,
                "instrument_id": item.instrument_id,
                "price_threshold": (
                    str(item.price_threshold) if item.price_threshold is not None else None
                ),
                "risk_status_threshold": (
                    item.risk_status_threshold.value
                    if item.risk_status_threshold is not None
                    else None
                ),
                "max_fact_age_seconds": item.max_fact_age_seconds,
            }
            for item in rules
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def _rules_from_json(payload: str) -> tuple[MonitorRule, ...]:
    try:
        raw = json.loads(payload)
        if not isinstance(raw, list):
            raise ValueError("rules must be list")
        return tuple(
            MonitorRule(
                rule_code=str(item["rule_code"]),
                rule_type=MonitorRuleType(item["rule_type"]),
                severity=MonitorSeverity(item["severity"]),
                instrument_id=item.get("instrument_id"),
                price_threshold=(
                    Decimal(item["price_threshold"])
                    if item.get("price_threshold") is not None
                    else None
                ),
                risk_status_threshold=(
                    RiskOverallStatus(item["risk_status_threshold"])
                    if item.get("risk_status_threshold") is not None
                    else None
                ),
                max_fact_age_seconds=int(item["max_fact_age_seconds"]),
            )
            for item in raw
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PersistenceError(
            "Stored monitor rules are invalid", retryable=False, details={}
        ) from exc


def _definition(row: MonitorVersionRow) -> MonitorDefinition:
    return MonitorDefinition(
        monitor_id=row.monitor_id,
        version=row.version,
        name=row.name,
        case_id=row.case_id,
        primary_instrument_id=row.primary_instrument_id,
        cadence=MonitorCadence(row.cadence),
        status=MonitorStatus(row.status),
        rules=_rules_from_json(row.rules_json),
        valid_until=_dt(row.valid_until),
        confirmed_by=row.confirmed_by,
        idempotency_key=row.idempotency_key,
        created_at=datetime.fromisoformat(row.created_at),
        schema_version=row.schema_version,
    )


def _version_row(value: MonitorDefinition) -> MonitorVersionRow:
    return MonitorVersionRow(
        monitor_id=value.monitor_id,
        version=value.version,
        name=value.name,
        case_id=value.case_id,
        primary_instrument_id=value.primary_instrument_id,
        cadence=value.cadence.value,
        status=value.status.value,
        rules_json=_rules_to_json(value.rules),
        valid_until=(value.valid_until.isoformat() if value.valid_until is not None else None),
        confirmed_by=value.confirmed_by,
        idempotency_key=value.idempotency_key,
        created_at=value.created_at.isoformat(),
        schema_version=value.schema_version,
    )


def _state(row: MonitorRuleStateRow) -> MonitorRuleState:
    return MonitorRuleState(
        monitor_id=row.monitor_id,
        monitor_version=row.monitor_version,
        rule_code=row.rule_code,
        state=MonitorRuleStateValue(row.state),
        observed_value=_dec(row.observed_value),
        fact_as_of=_dt(row.fact_as_of),
        message=row.message,
        updated_at=datetime.fromisoformat(row.updated_at),
    )


def _event(row: MonitorEventRow) -> MonitorEvent:
    return MonitorEvent(
        event_id=row.event_id,
        monitor_id=row.monitor_id,
        monitor_version=row.monitor_version,
        rule_code=row.rule_code,
        event_type=MonitorEventType(row.event_type),
        severity=MonitorSeverity(row.severity),
        observed_value=_dec(row.observed_value),
        threshold_value=_dec(row.threshold_value),
        fact_as_of=_dt(row.fact_as_of),
        message=row.message,
        created_at=datetime.fromisoformat(row.created_at),
    )


def _resolution(row: MonitorEventResolutionRow) -> MonitorEventResolution:
    return MonitorEventResolution(
        resolution_id=row.resolution_id,
        event_id=row.event_id,
        action=MonitorEventAction(row.action),
        note=row.note,
        confirmed_by=row.confirmed_by,
        idempotency_key=row.idempotency_key,
        created_at=datetime.fromisoformat(row.created_at),
    )


def _persistence_error(exc: IntegrityError) -> PersistenceError:
    return PersistenceError(
        "Monitoring persistence conflict",
        retryable=True,
        details={"constraint": type(exc.orig).__name__},
    )


class SqlAlchemyMonitorRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, monitor: MonitorDefinition) -> MonitorDefinition:
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    MonitorIdentityRow(
                        monitor_id=monitor.monitor_id,
                        created_at=monitor.created_at.isoformat(),
                    )
                )
                session.add(_version_row(monitor))
            return monitor
        except IntegrityError as exc:
            raise _persistence_error(exc) from exc

    def append_version(self, monitor: MonitorDefinition) -> MonitorDefinition:
        try:
            with Session(self._engine) as session, session.begin():
                session.add(_version_row(monitor))
            return monitor
        except IntegrityError as exc:
            raise _persistence_error(exc) from exc

    def get_current(self, monitor_id: str) -> MonitorDefinition | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(MonitorVersionRow)
                .where(MonitorVersionRow.monitor_id == monitor_id)
                .order_by(MonitorVersionRow.version.desc())
                .limit(1)
            )
            return _definition(row) if row is not None else None

    def get_by_idempotency_key(self, key: str) -> MonitorDefinition | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(MonitorVersionRow).where(MonitorVersionRow.idempotency_key == key)
            )
            return _definition(row) if row is not None else None

    def list_current(
        self, status: MonitorStatus | None = None
    ) -> tuple[MonitorDefinition, ...]:
        latest = (
            select(
                MonitorVersionRow.monitor_id,
                func.max(MonitorVersionRow.version).label("latest_version"),
            )
            .group_by(MonitorVersionRow.monitor_id)
            .subquery()
        )
        statement = select(MonitorVersionRow).join(
            latest,
            (MonitorVersionRow.monitor_id == latest.c.monitor_id)
            & (MonitorVersionRow.version == latest.c.latest_version),
        )
        if status is not None:
            statement = statement.where(MonitorVersionRow.status == status.value)
        statement = statement.order_by(MonitorVersionRow.created_at, MonitorVersionRow.monitor_id)
        with Session(self._engine) as session:
            return tuple(_definition(row) for row in session.scalars(statement))

    def get_rule_states(self, monitor_id: str) -> tuple[MonitorRuleState, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(MonitorRuleStateRow)
                .where(MonitorRuleStateRow.monitor_id == monitor_id)
                .order_by(MonitorRuleStateRow.rule_code)
            )
            return tuple(_state(row) for row in rows)

    def record_evaluation(
        self,
        run: MonitorRun,
        states: tuple[MonitorRuleState, ...],
        events: tuple[MonitorEvent, ...],
    ) -> MonitorRun:
        with Session(self._engine) as session, session.begin():
            session.add(
                MonitorRunRow(
                    run_id=run.run_id,
                    requested_monitor_ids=run.requested_monitor_ids,
                    as_of=run.as_of.isoformat(),
                    started_at=run.started_at.isoformat(),
                    completed_at=run.completed_at.isoformat(),
                    status=run.status.value,
                    monitors_evaluated=run.monitors_evaluated,
                    rules_evaluated=run.rules_evaluated,
                    events_created=run.events_created,
                    warning_codes=run.warning_codes,
                    error_codes=run.error_codes,
                )
            )
            for value in states:
                row = session.get(MonitorRuleStateRow, (value.monitor_id, value.rule_code))
                if row is None:
                    row = MonitorRuleStateRow(
                        monitor_id=value.monitor_id,
                        rule_code=value.rule_code,
                        monitor_version=value.monitor_version,
                        state=value.state.value,
                        observed_value=(
                            str(value.observed_value)
                            if value.observed_value is not None
                            else None
                        ),
                        fact_as_of=(
                            value.fact_as_of.isoformat()
                            if value.fact_as_of is not None
                            else None
                        ),
                        message=value.message,
                        updated_at=value.updated_at.isoformat(),
                    )
                    session.add(row)
                else:
                    row.state = value.state.value
                    row.monitor_version = value.monitor_version
                    row.observed_value = (
                        str(value.observed_value)
                        if value.observed_value is not None
                        else None
                    )
                    row.fact_as_of = (
                        value.fact_as_of.isoformat() if value.fact_as_of is not None else None
                    )
                    row.message = value.message
                    row.updated_at = value.updated_at.isoformat()
            session.add_all(
                [
                    MonitorEventRow(
                        event_id=value.event_id,
                        monitor_id=value.monitor_id,
                        monitor_version=value.monitor_version,
                        rule_code=value.rule_code,
                        event_type=value.event_type.value,
                        severity=value.severity.value,
                        observed_value=(
                            str(value.observed_value)
                            if value.observed_value is not None
                            else None
                        ),
                        threshold_value=(
                            str(value.threshold_value)
                            if value.threshold_value is not None
                            else None
                        ),
                        fact_as_of=(
                            value.fact_as_of.isoformat()
                            if value.fact_as_of is not None
                            else None
                        ),
                        message=value.message,
                        created_at=value.created_at.isoformat(),
                    )
                    for value in events
                ]
            )
        return run

    def get_event(self, event_id: str) -> MonitorEvent | None:
        with Session(self._engine) as session:
            row = session.get(MonitorEventRow, event_id)
            return _event(row) if row is not None else None

    def list_events(
        self, monitor_id: str | None, limit: int
    ) -> tuple[MonitorEvent, ...]:
        statement = select(MonitorEventRow)
        if monitor_id is not None:
            statement = statement.where(MonitorEventRow.monitor_id == monitor_id)
        statement = statement.order_by(MonitorEventRow.created_at.desc()).limit(limit)
        with Session(self._engine) as session:
            return tuple(_event(row) for row in session.scalars(statement))

    def append_resolution(
        self, resolution: MonitorEventResolution
    ) -> MonitorEventResolution:
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    MonitorEventResolutionRow(
                        resolution_id=resolution.resolution_id,
                        event_id=resolution.event_id,
                        action=resolution.action.value,
                        note=resolution.note,
                        confirmed_by=resolution.confirmed_by,
                        idempotency_key=resolution.idempotency_key,
                        created_at=resolution.created_at.isoformat(),
                    )
                )
            return resolution
        except IntegrityError as exc:
            raise _persistence_error(exc) from exc

    def get_resolution_by_idempotency_key(
        self, key: str
    ) -> MonitorEventResolution | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(MonitorEventResolutionRow).where(
                    MonitorEventResolutionRow.idempotency_key == key
                )
            )
            return _resolution(row) if row is not None else None

    def latest_resolution(self, event_id: str) -> MonitorEventResolution | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(MonitorEventResolutionRow)
                .where(MonitorEventResolutionRow.event_id == event_id)
                .order_by(MonitorEventResolutionRow.created_at.desc())
                .limit(1)
            )
            return _resolution(row) if row is not None else None
