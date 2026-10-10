from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from application.services.plan_monitor_coverage import plan_monitor_coverage
from application.services.plan_workflow_query_service import PlanWorkflowQueryService
from conftest import FixedClock
from domain.common.errors import DataContractError
from domain.monitoring.enums import (
    MonitorCadence,
    MonitorRuleStateValue,
    MonitorRuleType,
    MonitorSeverity,
    MonitorStatus,
)
from domain.monitoring.models import MonitorDefinition, MonitorRule
from domain.trade_plan.enums import (
    TradePlanComparator,
    TradePlanConditionMode,
    TradePlanConditionPhase,
    TradePlanFactType,
    TradePlanStatus,
)
from domain.trade_plan.models import TradePlan, TradePlanCondition

NOW = datetime(2026, 10, 3, tzinfo=UTC)
SUFFIX = "00000000-0000-7000-8000-000000000001"


def plan_fixture():
    condition = TradePlanCondition(
        condition_code="ENTRY",
        phase=TradePlanConditionPhase.ENTRY,
        mode=TradePlanConditionMode.MONITORABLE,
        description="Synthetic price condition",
        severity="INFO",
        fact_type=TradePlanFactType.PRICE,
        metric_key="last",
        comparator=TradePlanComparator.LTE,
        threshold=Decimal("100"),
        unit="USD",
        instrument_id="equity:US:TEST",
        max_fact_age_seconds=3600,
    )
    return TradePlan(
        plan_id=f"trade_plan_{SUFFIX}",
        version=2,
        subject_id=f"case_{SUFFIX}",
        thesis_id=f"thesis_{SUFFIX}",
        instrument_id="equity:US:TEST",
        status=TradePlanStatus.ACTIVE,
        valid_from=NOW - timedelta(days=1),
        valid_until=None,
        currency="USD",
        reference_price=Decimal("100"),
        reference_price_at=NOW,
        target_position_percent=Decimal("5"),
        max_position_percent=Decimal("10"),
        risk_budget_percent=Decimal("1"),
        stop_price=Decimal("90"),
        conditions=(condition,),
        notes="Synthetic only",
        confirmed_by="user",
        created_at=NOW - timedelta(hours=2),
        idempotency_key="synthetic-plan",
    )


def monitor_fixture(plan):
    rule = MonitorRule(
        rule_code="ENTRY",
        rule_type=MonitorRuleType.FACT_COMPARISON,
        severity=MonitorSeverity.INFO,
        instrument_id=plan.instrument_id,
        price_threshold=None,
        risk_status_threshold=None,
        max_fact_age_seconds=3600,
        fact_type=TradePlanFactType.PRICE,
        metric_key="last",
        comparator=TradePlanComparator.LTE,
        numeric_threshold=Decimal("100"),
        description="Synthetic price condition",
    )
    return MonitorDefinition(
        monitor_id=f"monitor_{SUFFIX}",
        version=1,
        name="Synthetic",
        subject_id=plan.subject_id,
        primary_instrument_id=plan.instrument_id,
        cadence=MonitorCadence.ON_DEMAND,
        status=MonitorStatus.ACTIVE,
        rules=(rule,),
        confirmed_by="user",
        idempotency_key="monitor",
        created_at=NOW - timedelta(hours=1),
        trade_plan_id=plan.plan_id,
        trade_plan_version=plan.version,
    )


def repository(monitor, **changes):
    observation = SimpleNamespace(
        monitor_id=monitor.monitor_id,
        monitor_version=monitor.version,
        rule_code="ENTRY",
        state=MonitorRuleStateValue.QUIET,
        error_codes=(),
        fact_as_of=NOW,
    )
    for field, value in changes.items():
        setattr(observation, field, value)
    repo = MagicMock()
    repo.list_current.return_value = (monitor,)
    repo.get_version.return_value = monitor
    repo.latest_run_for_monitor_version.return_value = SimpleNamespace(
        run_id="monitor_run_synthetic", completed_at=NOW, observations=(observation,)
    )
    return repo


@pytest.mark.parametrize(
    "change",
    [
        {"state": MonitorRuleStateValue.NOT_EVALUATED},
        {"monitor_id": "another-monitor"},
        {"monitor_version": 2},
        {"fact_as_of": NOW - timedelta(hours=2)},
        {"fact_as_of": NOW + timedelta(seconds=1)},
        {"fact_as_of": None},
        {"error_codes": ("FACT_UNAVAILABLE",)},
    ],
)
def test_unusable_or_other_batch_observations_are_not_coverage(change):
    plan = plan_fixture()
    repo = repository(monitor_fixture(plan), **change)
    coverage = plan_monitor_coverage(plan, repo, as_of=NOW)
    assert coverage.conditions[0].state == "UNAVAILABLE"
    assert coverage.conditions[0].configured and not coverage.conditions[0].observed


@pytest.mark.parametrize(
    "change",
    [
        {"trade_plan_version": 1},
        {"status": MonitorStatus.PAUSED},
        {"valid_until": NOW},
    ],
)
def test_old_inactive_or_expired_monitors_do_not_cover_new_plan(change):
    plan = plan_fixture()
    monitor = replace(monitor_fixture(plan), **change)
    assert (
        plan_monitor_coverage(plan, repository(monitor), as_of=NOW).conditions[0].state
        == "UNCONFIGURED"
    )


def test_rule_meaning_and_manual_are_separate_from_observation():
    plan = plan_fixture()
    monitor = monitor_fixture(plan)
    repo = repository(monitor)
    assert plan_monitor_coverage(plan, repo, as_of=NOW).conditions[0].state == "OBSERVED"
    altered = replace(monitor, rules=(replace(monitor.rules[0], numeric_threshold=Decimal("99")),))
    assert (
        plan_monitor_coverage(plan, repository(altered), as_of=NOW).conditions[0].state
        == "UNCONFIGURED"
    )
    repo.latest_run_for_monitor_version.return_value = None
    assert plan_monitor_coverage(plan, repo, as_of=NOW).conditions[0].state == "CONFIGURED"
    manual = TradePlanCondition(
        condition_code="REVIEW",
        phase=TradePlanConditionPhase.REVIEW,
        mode=TradePlanConditionMode.MANUAL,
        description="Read report",
        severity="INFO",
    )
    assert (
        plan_monitor_coverage(replace(plan, conditions=(manual,)), repo, as_of=NOW)
        .conditions[0]
        .state
        == "MANUAL"
    )


def test_dependency_read_keeps_old_binding_and_reports_changed_thesis_and_conditions():
    plan = plan_fixture()
    old = replace(
        plan, version=1, conditions=(replace(plan.conditions[0], threshold=Decimal("99")),)
    )
    monitor = replace(monitor_fixture(plan), trade_plan_version=1)
    uow = MagicMock()
    uow.__enter__.return_value = uow
    uow.trade_plans.get_current_by_subject.return_value = plan
    uow.trade_plans.get_version.return_value = old
    uow.revisions.list_by_thesis.return_value = (
        SimpleNamespace(
            subject_id=plan.subject_id,
            revision_no=1,
            revision_id="rev_old",
            confirmed_at=plan.created_at,
        ),
        SimpleNamespace(
            subject_id=plan.subject_id, revision_no=2, revision_id="rev_new", confirmed_at=NOW
        ),
    )
    repo = repository(monitor)
    service = PlanWorkflowQueryService(lambda: uow, repo, FixedClock(NOW))
    result = service.get(plan.subject_id, plan.plan_id, 2)
    assert result["thesis"]["review_needed"]
    assert result["condition_changes"][0]["change"] == "CHANGED"
    assert result["monitors"][0]["binding"] == "OTHER_VERSION"
    assert result["coverage"]["conditions"][0]["state"] == "UNCONFIGURED"
    uow.commit.assert_not_called()
    repo.append_version.assert_not_called()
    with pytest.raises(DataContractError):
        service.get(plan.subject_id, plan.plan_id, 1)
    repo.list_current.side_effect = RuntimeError("do not disclose")
    degraded = service.get(plan.subject_id, plan.plan_id, 2)
    assert degraded["coverage"]["unavailable"] and not degraded["monitors_available"]
    assert "do not disclose" not in str(degraded)
