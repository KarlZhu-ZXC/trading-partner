"""SQLAlchemy persistence for Phase 2C Monitoring."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domain.common.diagnostics import ProviderFailureDiagnostic
from domain.common.errors import PersistenceError
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorEventAction,
    MonitorEventType,
    MonitorJudgmentConclusion,
    MonitorNotificationChannel,
    MonitorNotificationStatus,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorRunStatus,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import (
    MonitorDefinition,
    MonitorEvent,
    MonitorEventResolution,
    MonitorJudgment,
    MonitorJudgmentPolicy,
    MonitorRule,
    MonitorRuleState,
    MonitorRun,
    MonitorRunObservation,
)
from domain.notifications.enums import (
    NotificationChannel,
    NotificationSourceType,
    NotificationStatus,
)
from domain.notifications.models import NotificationMessage, NotificationOutboxEntry
from domain.risk.enums import RiskOverallStatus
from domain.trade_plan.enums import TradePlanComparator, TradePlanFactType
from infrastructure.persistence.orm import (
    MonitorEventResolutionRow,
    MonitorEventRow,
    MonitorIdentityRow,
    MonitorJudgmentRow,
    MonitorRuleStateRow,
    MonitorRunObservationRow,
    MonitorRunRow,
    MonitorVersionRow,
    NotificationOutboxRow,
)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _diagnostics_to_json(values: tuple[ProviderFailureDiagnostic, ...]) -> str:
    return json.dumps(
        [
            {
                "provider": item.provider,
                "stage": item.stage,
                "error_code": item.error_code,
                "retryable": item.retryable,
                "attempt_count": item.attempt_count,
                "error_type": item.error_type,
                "status_class": item.status_class,
                "status_code": item.status_code,
            }
            for item in values
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def _diagnostics_from_json(payload: str) -> tuple[ProviderFailureDiagnostic, ...]:
    try:
        raw = json.loads(payload)
        if not isinstance(raw, list):
            raise ValueError("diagnostics must be list")
        return tuple(ProviderFailureDiagnostic(**item) for item in raw if isinstance(item, dict))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PersistenceError(
            "Stored Monitor Provider diagnostics are invalid", retryable=False
        ) from exc


def _rules_to_json(rules: tuple[MonitorRule, ...]) -> str:
    return json.dumps(
        [
            {
                "rule_code": item.rule_code,
                "description": item.description,
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
                "fact_type": item.fact_type.value if item.fact_type is not None else None,
                "metric_key": item.metric_key,
                "comparator": (item.comparator.value if item.comparator is not None else None),
                "numeric_threshold": (
                    str(item.numeric_threshold) if item.numeric_threshold is not None else None
                ),
                "recovery_threshold": (
                    str(item.recovery_threshold) if item.recovery_threshold is not None else None
                ),
                "technical_interval": item.technical_interval,
                "event_after": (
                    item.event_after.isoformat() if item.event_after is not None else None
                ),
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
                description=item.get("description"),
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
                fact_type=(
                    TradePlanFactType(item["fact_type"])
                    if item.get("fact_type") is not None
                    else None
                ),
                metric_key=item.get("metric_key"),
                comparator=(
                    TradePlanComparator(item["comparator"])
                    if item.get("comparator") is not None
                    else None
                ),
                numeric_threshold=(
                    Decimal(item["numeric_threshold"])
                    if item.get("numeric_threshold") is not None
                    else None
                ),
                recovery_threshold=(
                    Decimal(item["recovery_threshold"])
                    if item.get("recovery_threshold") is not None
                    else None
                ),
                technical_interval=item.get("technical_interval"),
                event_after=_dt(item.get("event_after")),
            )
            for item in raw
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PersistenceError(
            "Stored monitor rules are invalid", retryable=False, details={}
        ) from exc


def _judgment_policy_to_json(value: MonitorJudgmentPolicy | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        {
            "playbook": value.playbook,
            "reference_instrument_ids": value.reference_instrument_ids,
            "relative_strength_pairs": value.relative_strength_pairs,
            "confirmed_state_json": value.confirmed_state_json,
            "prompt_version": value.prompt_version,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _judgment_policy_from_json(payload: str | None) -> MonitorJudgmentPolicy | None:
    if payload is None:
        return None
    try:
        raw = json.loads(payload)
        return MonitorJudgmentPolicy(
            playbook=raw["playbook"],
            reference_instrument_ids=tuple(raw["reference_instrument_ids"]),
            relative_strength_pairs=tuple(
                tuple(item) for item in raw.get("relative_strength_pairs", ())
            ),
            confirmed_state_json=raw.get("confirmed_state_json", "{}"),
            prompt_version=raw.get("prompt_version", "monitor-judgment-v1"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PersistenceError(
            "Stored Monitor judgment policy is invalid", retryable=False
        ) from exc


def _definition(row: MonitorVersionRow) -> MonitorDefinition:
    return MonitorDefinition(
        monitor_id=row.monitor_id,
        version=row.version,
        name=row.name,
        subject_id=row.subject_id,
        primary_instrument_id=row.primary_instrument_id,
        trade_plan_id=row.trade_plan_id,
        trade_plan_version=row.trade_plan_version,
        cadence=MonitorCadence(row.cadence),
        status=MonitorStatus(row.status),
        rules=_rules_from_json(row.rules_json),
        judgment_policy=_judgment_policy_from_json(row.judgment_policy_json),
        valid_until=_dt(row.valid_until),
        interval_minutes=row.interval_minutes,
        confirmed_by=row.confirmed_by,
        idempotency_key=row.idempotency_key,
        created_at=datetime.fromisoformat(row.created_at),
        schema_version=row.schema_version,
    )


def _judgment(row: MonitorJudgmentRow) -> MonitorJudgment:
    return MonitorJudgment(
        judgment_id=row.judgment_id,
        run_id=row.run_id,
        monitor_id=row.monitor_id,
        monitor_version=row.monitor_version,
        status=cast(Literal["SUCCEEDED", "SKIPPED", "FAILED"], row.status),
        urgency=cast(Literal["WATCH", "ACTION", "URGENT"] | None, row.urgency),
        phase=row.phase,
        market_state=row.market_state,
        divergence=cast(Literal["BULLISH", "BEARISH", "NONE"] | None, row.divergence),
        conclusion=(MonitorJudgmentConclusion(row.conclusion) if row.conclusion else None),
        quantity_min=row.quantity_min,
        quantity_max=row.quantity_max,
        summary=row.summary,
        evidence_feature_ids=row.evidence_feature_ids,
        next_trigger=row.next_trigger,
        invalidation=row.invalidation,
        feature_signature=row.feature_signature,
        result_fingerprint=row.result_fingerprint,
        provider=row.provider,
        model=row.model,
        reasoning_effort=row.reasoning_effort,
        prompt_version=row.prompt_version,
        warning_codes=row.warning_codes,
        error_codes=row.error_codes,
        created_at=datetime.fromisoformat(row.created_at),
        web_search_used=row.web_search_used,
        web_source_urls=row.web_source_urls,
    )


def _version_row(value: MonitorDefinition) -> MonitorVersionRow:
    return MonitorVersionRow(
        monitor_id=value.monitor_id,
        version=value.version,
        name=value.name,
        subject_id=value.subject_id,
        primary_instrument_id=value.primary_instrument_id,
        trade_plan_id=value.trade_plan_id,
        trade_plan_version=value.trade_plan_version,
        cadence=value.cadence.value,
        interval_minutes=value.interval_minutes,
        status=value.status.value,
        rules_json=_rules_to_json(value.rules),
        judgment_policy_json=_judgment_policy_to_json(value.judgment_policy),
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


def _observation(row: MonitorRunObservationRow) -> MonitorRunObservation:
    return MonitorRunObservation(
        run_id=row.run_id,
        monitor_id=row.monitor_id,
        monitor_version=row.monitor_version,
        rule_code=row.rule_code,
        instrument_id=row.instrument_id,
        severity=MonitorSeverity(row.severity),
        state=MonitorRuleStateValue(row.state),
        observed_value=_dec(row.observed_value),
        threshold_value=_dec(row.threshold_value),
        distance_value=_dec(row.distance_value),
        distance_percent=_dec(row.distance_percent),
        fact_as_of=_dt(row.fact_as_of),
        fact_age_seconds=row.fact_age_seconds,
        warning_codes=row.warning_codes,
        error_codes=row.error_codes,
        message=row.message,
        diagnostics=_diagnostics_from_json(row.diagnostics_json),
    )


def _notification_entry(
    row: NotificationOutboxRow,
) -> NotificationOutboxEntry:
    return NotificationOutboxEntry(
        notification_id=row.notification_id,
        source_type=NotificationSourceType(row.source_type),
        source_id=row.source_id,
        channel=NotificationChannel(row.channel),
        title=row.title,
        body=row.body,
        status=NotificationStatus(row.status),
        attempt_count=row.attempt_count,
        next_attempt_at=datetime.fromisoformat(row.next_attempt_at),
        created_at=datetime.fromisoformat(row.created_at),
        last_attempt_at=_dt(row.last_attempt_at),
        delivered_at=_dt(row.delivered_at),
        provider_message_id=row.provider_message_id,
        last_error_code=row.last_error_code,
        idempotency_key=row.idempotency_key,
        confirmed_by=row.confirmed_by,
        authorization_note=row.authorization_note,
        expires_at=_dt(row.expires_at),
    )


def _run(
    row: MonitorRunRow,
    observations: tuple[MonitorRunObservation, ...],
    *,
    scoped_monitor_id: str | None = None,
) -> MonitorRun:
    if scoped_monitor_id is None:
        selected_monitor_ids = row.selected_monitor_ids
        monitors_evaluated = row.monitors_evaluated
        rules_evaluated = row.rules_evaluated
        warning_codes = row.warning_codes
        error_codes = row.error_codes
        status = MonitorRunStatus(row.status)
    else:
        selected_monitor_ids = (scoped_monitor_id,)
        monitors_evaluated = 1 if observations else 0
        rules_evaluated = len(observations)
        warning_codes = tuple(
            dict.fromkeys(code for item in observations for code in item.warning_codes)
        )
        error_codes = tuple(
            dict.fromkeys(code for item in observations for code in item.error_codes)
        )
        status = (
            MonitorRunStatus.FAILED
            if not observations
            else MonitorRunStatus.PARTIAL
            if error_codes
            or any(item.state is MonitorRuleStateValue.NOT_EVALUATED for item in observations)
            else MonitorRunStatus.SUCCEEDED
        )
    return MonitorRun(
        run_id=row.run_id,
        requested_monitor_ids=row.requested_monitor_ids,
        selected_monitor_ids=selected_monitor_ids,
        cadence=MonitorCadence(row.cadence) if row.cadence is not None else None,
        as_of=datetime.fromisoformat(row.as_of),
        started_at=datetime.fromisoformat(row.started_at),
        completed_at=datetime.fromisoformat(row.completed_at),
        status=status,
        monitors_evaluated=monitors_evaluated,
        rules_evaluated=rules_evaluated,
        events_created=row.events_created,
        warning_codes=warning_codes,
        error_codes=error_codes,
        observation_history_complete=row.observation_history_complete,
        observations=observations,
        execution_effect=False,
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

    def get_version(self, monitor_id: str, version: int) -> MonitorDefinition | None:
        """Read one immutable Monitor version; never substitute the current version."""

        with Session(self._engine) as session:
            row = session.get(MonitorVersionRow, (monitor_id, version))
            return _definition(row) if row is not None else None

    def get_created_at(self, monitor_id: str) -> datetime | None:
        with Session(self._engine) as session:
            value = session.scalar(
                select(MonitorIdentityRow.created_at).where(
                    MonitorIdentityRow.monitor_id == monitor_id
                )
            )
            return datetime.fromisoformat(value) if value is not None else None

    def get_by_idempotency_key(self, key: str) -> MonitorDefinition | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(MonitorVersionRow).where(MonitorVersionRow.idempotency_key == key)
            )
            return _definition(row) if row is not None else None

    def list_current(self, status: MonitorStatus | None = None) -> tuple[MonitorDefinition, ...]:
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
        notifications: tuple[NotificationMessage, ...],
        judgments: tuple[MonitorJudgment, ...] = (),
    ) -> MonitorRun:
        with Session(self._engine) as session, session.begin():
            session.add(
                MonitorRunRow(
                    run_id=run.run_id,
                    requested_monitor_ids=run.requested_monitor_ids,
                    selected_monitor_ids=run.selected_monitor_ids,
                    cadence=run.cadence.value if run.cadence is not None else None,
                    as_of=run.as_of.isoformat(),
                    started_at=run.started_at.isoformat(),
                    completed_at=run.completed_at.isoformat(),
                    status=run.status.value,
                    monitors_evaluated=run.monitors_evaluated,
                    rules_evaluated=run.rules_evaluated,
                    events_created=run.events_created,
                    warning_codes=run.warning_codes,
                    error_codes=run.error_codes,
                    observation_history_complete=run.observation_history_complete,
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
                            str(value.observed_value) if value.observed_value is not None else None
                        ),
                        fact_as_of=(
                            value.fact_as_of.isoformat() if value.fact_as_of is not None else None
                        ),
                        message=value.message,
                        updated_at=value.updated_at.isoformat(),
                    )
                    session.add(row)
                else:
                    row.state = value.state.value
                    row.monitor_version = value.monitor_version
                    row.observed_value = (
                        str(value.observed_value) if value.observed_value is not None else None
                    )
                    row.fact_as_of = (
                        value.fact_as_of.isoformat() if value.fact_as_of is not None else None
                    )
                    row.message = value.message
                    row.updated_at = value.updated_at.isoformat()
            session.add_all(
                [
                    MonitorRunObservationRow(
                        run_id=value.run_id,
                        monitor_id=value.monitor_id,
                        monitor_version=value.monitor_version,
                        rule_code=value.rule_code,
                        instrument_id=value.instrument_id,
                        severity=value.severity.value,
                        state=value.state.value,
                        observed_value=(
                            str(value.observed_value) if value.observed_value is not None else None
                        ),
                        threshold_value=(
                            str(value.threshold_value)
                            if value.threshold_value is not None
                            else None
                        ),
                        distance_value=(
                            str(value.distance_value) if value.distance_value is not None else None
                        ),
                        distance_percent=(
                            str(value.distance_percent)
                            if value.distance_percent is not None
                            else None
                        ),
                        fact_as_of=(
                            value.fact_as_of.isoformat() if value.fact_as_of is not None else None
                        ),
                        fact_age_seconds=value.fact_age_seconds,
                        warning_codes=value.warning_codes,
                        error_codes=value.error_codes,
                        message=value.message,
                        diagnostics_json=_diagnostics_to_json(value.diagnostics),
                    )
                    for value in run.observations
                ]
            )
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
                            str(value.observed_value) if value.observed_value is not None else None
                        ),
                        threshold_value=(
                            str(value.threshold_value)
                            if value.threshold_value is not None
                            else None
                        ),
                        fact_as_of=(
                            value.fact_as_of.isoformat() if value.fact_as_of is not None else None
                        ),
                        message=value.message,
                        created_at=value.created_at.isoformat(),
                    )
                    for value in events
                ]
            )
            session.add_all(
                [
                    MonitorJudgmentRow(
                        judgment_id=value.judgment_id,
                        run_id=value.run_id,
                        monitor_id=value.monitor_id,
                        monitor_version=value.monitor_version,
                        status=value.status,
                        urgency=value.urgency,
                        phase=value.phase,
                        market_state=value.market_state,
                        divergence=value.divergence,
                        conclusion=(value.conclusion.value if value.conclusion else None),
                        quantity_min=value.quantity_min,
                        quantity_max=value.quantity_max,
                        summary=value.summary,
                        evidence_feature_ids=value.evidence_feature_ids,
                        next_trigger=value.next_trigger,
                        invalidation=value.invalidation,
                        feature_signature=value.feature_signature,
                        result_fingerprint=value.result_fingerprint,
                        provider=value.provider,
                        model=value.model,
                        reasoning_effort=value.reasoning_effort,
                        prompt_version=value.prompt_version,
                        warning_codes=value.warning_codes,
                        error_codes=value.error_codes,
                        created_at=value.created_at.isoformat(),
                        web_search_used=value.web_search_used,
                        web_source_urls=value.web_source_urls,
                    )
                    for value in judgments
                ]
            )
            session.add_all(
                [
                    NotificationOutboxRow(
                        notification_id=value.notification_id,
                        source_type=value.source_type.value,
                        source_id=value.source_id,
                        channel=value.channel.value,
                        title=value.title,
                        body=value.body,
                        status=NotificationStatus.PENDING.value,
                        attempt_count=0,
                        next_attempt_at=value.created_at.isoformat(),
                        created_at=value.created_at.isoformat(),
                        idempotency_key=value.idempotency_key,
                        confirmed_by=value.confirmed_by,
                        authorization_note=value.authorization_note,
                        expires_at=(
                            value.expires_at.isoformat() if value.expires_at is not None else None
                        ),
                    )
                    for value in notifications
                ]
            )
        return run

    def latest_judgment(self, monitor_id: str) -> MonitorJudgment | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(MonitorJudgmentRow)
                .where(MonitorJudgmentRow.monitor_id == monitor_id)
                .order_by(MonitorJudgmentRow.created_at.desc())
                .limit(1)
            )
            return _judgment(row) if row is not None else None

    def list_judgments(self, monitor_id: str, limit: int) -> tuple[MonitorJudgment, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(MonitorJudgmentRow)
                .where(MonitorJudgmentRow.monitor_id == monitor_id)
                .order_by(MonitorJudgmentRow.created_at.desc())
                .limit(limit)
            )
            return tuple(_judgment(row) for row in rows)

    def list_due_notifications(
        self,
        channel: MonitorNotificationChannel,
        as_of: datetime,
        limit: int,
    ) -> tuple[NotificationOutboxEntry, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(NotificationOutboxRow)
                .where(
                    NotificationOutboxRow.channel == channel.value,
                    NotificationOutboxRow.status == MonitorNotificationStatus.PENDING.value,
                    NotificationOutboxRow.next_attempt_at <= as_of.isoformat(),
                )
                .order_by(
                    NotificationOutboxRow.next_attempt_at,
                    NotificationOutboxRow.created_at,
                )
                .limit(limit)
            )
            return tuple(_notification_entry(row) for row in rows)

    def record_notification_attempt(
        self,
        notification_id: str,
        channel: MonitorNotificationChannel,
        *,
        status: MonitorNotificationStatus,
        attempted_at: datetime,
        next_attempt_at: datetime,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> NotificationOutboxEntry:
        with Session(self._engine) as session, session.begin():
            row = session.get(NotificationOutboxRow, notification_id)
            if row is None or row.channel != channel.value:
                raise PersistenceError(
                    "Monitor notification outbox entry was not found",
                    retryable=False,
                    details={"notification_id": notification_id},
                )
            row.status = status.value
            row.attempt_count += 1
            row.last_attempt_at = attempted_at.isoformat()
            row.next_attempt_at = next_attempt_at.isoformat()
            row.delivered_at = (
                attempted_at.isoformat() if status is MonitorNotificationStatus.DELIVERED else None
            )
            row.provider_message_id = provider_message_id
            row.last_error_code = error_code
            session.flush()
            result = _notification_entry(row)
        return result

    def notification_counts(
        self, channel: MonitorNotificationChannel
    ) -> dict[MonitorNotificationStatus, int]:
        with Session(self._engine) as session:
            rows = session.execute(
                select(
                    NotificationOutboxRow.status,
                    func.count(),
                )
                .where(NotificationOutboxRow.channel == channel.value)
                .group_by(NotificationOutboxRow.status)
            )
            return {MonitorNotificationStatus(status): int(count) for status, count in rows}

    def last_notification_delivery_at(self, channel: MonitorNotificationChannel) -> datetime | None:
        with Session(self._engine) as session:
            value = session.scalar(
                select(func.max(NotificationOutboxRow.delivered_at)).where(
                    NotificationOutboxRow.channel == channel.value,
                    NotificationOutboxRow.status == MonitorNotificationStatus.DELIVERED.value,
                )
            )
            return _dt(value)

    # Generic NotificationOutboxRepository implementation. These methods are
    # deliberately separate from the MonitorRepository read/write vocabulary;
    # the concrete adapter may implement both ports without making the
    # application layer depend on SQLAlchemy.
    def enqueue(self, message: NotificationMessage) -> NotificationOutboxEntry:
        try:
            with Session(self._engine) as session, session.begin():
                session.add(
                    NotificationOutboxRow(
                        notification_id=message.notification_id,
                        source_type=message.source_type.value,
                        source_id=message.source_id,
                        channel=message.channel.value,
                        title=message.title,
                        body=message.body,
                        status=NotificationStatus.PENDING.value,
                        attempt_count=0,
                        next_attempt_at=message.created_at.isoformat(),
                        created_at=message.created_at.isoformat(),
                        idempotency_key=message.idempotency_key,
                        confirmed_by=message.confirmed_by,
                        authorization_note=message.authorization_note,
                        expires_at=(
                            message.expires_at.isoformat()
                            if message.expires_at is not None
                            else None
                        ),
                    )
                )
                session.flush()
                row = session.get(NotificationOutboxRow, message.notification_id)
                if row is None:  # pragma: no cover - SQLAlchemy identity invariant
                    raise PersistenceError(
                        "Notification outbox entry was not persisted",
                        retryable=False,
                        details={},
                    )
                return _notification_entry(row)
        except IntegrityError as exc:
            raise _persistence_error(exc) from exc

    def get_notification_by_idempotency_key(self, key: str) -> NotificationOutboxEntry | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(NotificationOutboxRow).where(NotificationOutboxRow.idempotency_key == key)
            )
            return _notification_entry(row) if row is not None else None

    def list_due(
        self,
        channel: NotificationChannel,
        as_of: datetime,
        limit: int,
    ) -> tuple[NotificationOutboxEntry, ...]:
        return self.list_due_notifications(channel, as_of, limit)

    def list_recent(
        self,
        channel: NotificationChannel,
        limit: int,
    ) -> tuple[NotificationOutboxEntry, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(NotificationOutboxRow)
                .where(NotificationOutboxRow.channel == channel.value)
                .order_by(NotificationOutboxRow.created_at.desc())
                .limit(limit)
            )
            return tuple(_notification_entry(row) for row in rows)

    def record_attempt(
        self,
        notification_id: str,
        channel: NotificationChannel,
        *,
        status: NotificationStatus,
        attempted_at: datetime,
        next_attempt_at: datetime,
        provider_message_id: str | None,
        error_code: str | None,
    ) -> NotificationOutboxEntry:
        return self.record_notification_attempt(
            notification_id,
            channel,
            status=status,
            attempted_at=attempted_at,
            next_attempt_at=next_attempt_at,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )

    def counts(self, channel: NotificationChannel) -> dict[NotificationStatus, int]:
        return self.notification_counts(channel)

    def last_delivery_at(self, channel: NotificationChannel) -> datetime | None:
        return self.last_notification_delivery_at(channel)

    def get_run(self, run_id: str) -> MonitorRun | None:
        with Session(self._engine) as session:
            row = session.get(MonitorRunRow, run_id)
            if row is None:
                return None
            observations = tuple(
                _observation(item)
                for item in session.scalars(
                    select(MonitorRunObservationRow)
                    .where(MonitorRunObservationRow.run_id == run_id)
                    .order_by(
                        MonitorRunObservationRow.monitor_id,
                        MonitorRunObservationRow.rule_code,
                    )
                )
            )
            return _run(row, observations)

    def list_runs(self, monitor_id: str | None, limit: int) -> tuple[MonitorRun, ...]:
        statement = select(MonitorRunRow)
        if monitor_id is not None:
            statement = (
                statement.join(
                    MonitorRunObservationRow,
                    MonitorRunObservationRow.run_id == MonitorRunRow.run_id,
                )
                .where(MonitorRunObservationRow.monitor_id == monitor_id)
                .distinct()
            )
        statement = statement.order_by(MonitorRunRow.completed_at.desc()).limit(limit)
        with Session(self._engine) as session:
            rows = tuple(session.scalars(statement))
            values: list[MonitorRun] = []
            for row in rows:
                observation_statement = select(MonitorRunObservationRow).where(
                    MonitorRunObservationRow.run_id == row.run_id
                )
                if monitor_id is not None:
                    observation_statement = observation_statement.where(
                        MonitorRunObservationRow.monitor_id == monitor_id
                    )
                observations = tuple(
                    _observation(item)
                    for item in session.scalars(
                        observation_statement.order_by(
                            MonitorRunObservationRow.monitor_id,
                            MonitorRunObservationRow.rule_code,
                        )
                    )
                )
                values.append(_run(row, observations, scoped_monitor_id=monitor_id))
            return tuple(values)

    def latest_run_for_monitor(self, monitor_id: str) -> MonitorRun | None:
        values = self.list_runs(monitor_id, 1)
        return values[0] if values else None

    def latest_run_for_monitor_version(
        self, monitor_id: str, version: int
    ) -> MonitorRun | None:
        with Session(self._engine) as session:
            run_id = session.scalar(
                select(MonitorRunObservationRow.run_id)
                .where(
                    MonitorRunObservationRow.monitor_id == monitor_id,
                    MonitorRunObservationRow.monitor_version == version,
                )
                .join(
                    MonitorRunRow,
                    MonitorRunRow.run_id == MonitorRunObservationRow.run_id,
                )
                .order_by(MonitorRunRow.completed_at.desc())
                .limit(1)
            )
        return self.get_run(run_id) if run_id is not None else None

    def list_events_for_monitor_version(
        self, monitor_id: str, version: int, limit: int = 100
    ) -> tuple[MonitorEvent, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(MonitorEventRow)
                .where(
                    MonitorEventRow.monitor_id == monitor_id,
                    MonitorEventRow.monitor_version == version,
                )
                .order_by(MonitorEventRow.created_at.desc())
                .limit(limit)
            )
            return tuple(_event(row) for row in rows)

    def get_event(self, event_id: str) -> MonitorEvent | None:
        with Session(self._engine) as session:
            row = session.get(MonitorEventRow, event_id)
            return _event(row) if row is not None else None

    def list_events(self, monitor_id: str | None, limit: int) -> tuple[MonitorEvent, ...]:
        statement = select(MonitorEventRow)
        if monitor_id is not None:
            statement = statement.where(MonitorEventRow.monitor_id == monitor_id)
        statement = statement.order_by(MonitorEventRow.created_at.desc()).limit(limit)
        with Session(self._engine) as session:
            return tuple(_event(row) for row in session.scalars(statement))

    def append_resolution(self, resolution: MonitorEventResolution) -> MonitorEventResolution:
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

    def get_resolution_by_idempotency_key(self, key: str) -> MonitorEventResolution | None:
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
