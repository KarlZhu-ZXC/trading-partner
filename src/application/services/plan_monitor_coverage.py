"""Durable exact-version coverage shared by workflow reads and Scorecard."""

from dataclasses import dataclass
from datetime import datetime

from application.ports.monitor_repository import MonitorRepository
from domain.monitoring.enums import MonitorRuleStateValue, MonitorRuleType, MonitorStatus
from domain.monitoring.models import MonitorRule
from domain.scorecard.models import ScorecardSourceRef
from domain.trade_plan.models import TradePlan, TradePlanCondition


@dataclass(frozen=True, slots=True)
class ConditionCoverage:
    condition_code: str
    state: str
    configured: bool
    observed: bool
    unavailable: bool


@dataclass(frozen=True, slots=True)
class PlanMonitorCoverage:
    conditions: tuple[ConditionCoverage, ...]
    source_refs: tuple[ScorecardSourceRef, ...]
    unavailable: bool = False


def matches_condition(rule: MonitorRule, condition: TradePlanCondition) -> bool:
    return (
        rule.rule_code == condition.condition_code
        and rule.rule_type is MonitorRuleType.FACT_COMPARISON
        and rule.severity.value == condition.severity
        and rule.recovery_threshold is None
        and rule.fact_type == condition.fact_type
        and rule.metric_key == condition.metric_key
        and rule.comparator == condition.comparator
        and rule.numeric_threshold == condition.threshold
        and rule.instrument_id == condition.instrument_id
        and rule.max_fact_age_seconds == condition.max_fact_age_seconds
        and rule.event_after == condition.event_after
        and rule.technical_interval in (None, "1d")
    )


def plan_monitor_coverage(
    plan: TradePlan,
    repository: MonitorRepository,
    *,
    as_of: datetime,
) -> PlanMonitorCoverage:
    configured: set[str] = set()
    observed: set[str] = set()
    unavailable: set[str] = set()
    refs = [ScorecardSourceRef("TRADE_PLAN", plan.plan_id, plan.version)]
    failed = False
    try:
        definitions = repository.list_current()
    except Exception:  # noqa: BLE001 - failure is never empty coverage
        definitions = ()
        failed = True
    for monitor in definitions:
        if (
            monitor.trade_plan_id != plan.plan_id
            or monitor.trade_plan_version != plan.version
            or monitor.status is not MonitorStatus.ACTIVE
            or monitor.created_at > as_of
            or (monitor.valid_until is not None and monitor.valid_until <= as_of)
        ):
            continue
        try:
            exact = repository.get_version(monitor.monitor_id, monitor.version)
            if exact != monitor:
                failed = True
                continue
            refs.append(ScorecardSourceRef("MONITOR", monitor.monitor_id, monitor.version))
            matched = {
                c.condition_code: c
                for c in plan.conditions
                if c.mode.value == "MONITORABLE"
                and any(matches_condition(r, c) for r in monitor.rules)
            }
            configured.update(matched)
            run = repository.latest_run_for_monitor_version(monitor.monitor_id, monitor.version)
            if run is None:
                continue
            refs.append(ScorecardSourceRef("MONITOR_RUN", run.run_id))
            for code, condition in matched.items():
                items = tuple(
                    o
                    for o in run.observations
                    if o.monitor_id == monitor.monitor_id
                    and o.monitor_version == monitor.version
                    and o.rule_code == code
                )
                valid = run.completed_at <= as_of and any(
                    o.state is not MonitorRuleStateValue.NOT_EVALUATED
                    and not o.error_codes
                    and o.fact_as_of is not None
                    and o.fact_as_of <= as_of
                    and (as_of - o.fact_as_of).total_seconds()
                    <= (condition.max_fact_age_seconds or 0)
                    for o in items
                )
                (observed if valid else unavailable).add(code)
        except Exception:  # noqa: BLE001 - retain independent conditions, disclose incomplete read
            failed = True
    return PlanMonitorCoverage(
        conditions=tuple(
            ConditionCoverage(
                c.condition_code,
                "MANUAL"
                if c.mode.value == "MANUAL"
                else "OBSERVED"
                if c.condition_code in observed
                else "UNAVAILABLE"
                if failed or c.condition_code in unavailable
                else "CONFIGURED"
                if c.condition_code in configured
                else "UNCONFIGURED",
                c.condition_code in configured,
                c.condition_code in observed,
                c.mode.value != "MANUAL" and (failed or c.condition_code in unavailable),
            )
            for c in plan.conditions
        ),
        source_refs=tuple(dict.fromkeys(refs)),
        unavailable=failed,
    )
