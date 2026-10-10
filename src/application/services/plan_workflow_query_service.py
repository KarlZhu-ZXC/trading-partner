"""Read-only dependency differences for confirmed investment intent."""

from dataclasses import asdict
from typing import Any

from application.ports.clock import Clock
from application.ports.monitor_repository import MonitorRepository
from application.services._research_support import UowFactory
from application.services.plan_monitor_coverage import plan_monitor_coverage
from domain.common.errors import DataContractError

_PLAN_FIELDS = (
    "thesis_id",
    "instrument_id",
    "status",
    "valid_from",
    "valid_until",
    "currency",
    "reference_price",
    "reference_price_at",
    "target_position_percent",
    "max_position_percent",
    "risk_budget_percent",
    "stop_price",
    "notes",
)


class PlanWorkflowQueryService:
    def __init__(self, uow_factory: UowFactory, monitors: MonitorRepository, clock: Clock):
        self._uow = uow_factory
        self._monitors = monitors
        self._clock = clock

    def get(self, subject_id: str, plan_id: str, version: int) -> dict[str, Any]:
        now = self._clock.now()
        with self._uow() as uow:
            uow.subjects.get(subject_id)
            plan = uow.trade_plans.get_current_by_subject(subject_id)
            if plan is None or plan.plan_id != plan_id or plan.version != version:
                raise DataContractError("Current Trade Plan changed; reload the Research Subject")
            if plan.subject_id != subject_id or plan.created_at > now:
                raise DataContractError("Trade Plan scope is unavailable")
            previous = uow.trade_plans.get_version(plan_id, version - 1) if version > 1 else None
            revisions = tuple(
                r
                for r in uow.revisions.list_by_thesis(plan.thesis_id)
                if r.subject_id == subject_id and r.confirmed_at <= now
            )
            baseline = max(
                (r for r in revisions if r.confirmed_at <= plan.created_at),
                key=lambda r: r.revision_no,
                default=None,
            )
            latest = max(revisions, key=lambda r: r.revision_no, default=None)
        changes = [
            {"field": field, "before": getattr(previous, field), "after": getattr(plan, field)}
            for field in _PLAN_FIELDS
            if previous is not None and getattr(previous, field) != getattr(plan, field)
        ]
        before = {c.condition_code: c for c in previous.conditions} if previous else {}
        after = {c.condition_code: c for c in plan.conditions}
        condition_changes = [
            {
                "condition_code": code,
                "change": "ADDED"
                if code not in before
                else "REMOVED"
                if code not in after
                else "CHANGED",
                "before": asdict(before[code]) if code in before else None,
                "after": asdict(after[code]) if code in after else None,
            }
            for code in sorted(before.keys() | after.keys())
            if before.get(code) != after.get(code)
        ]
        coverage = plan_monitor_coverage(plan, self._monitors, as_of=now)
        monitors_available = True
        try:
            monitors = tuple(
                m
                for m in self._monitors.list_current()
                if m.subject_id == subject_id and m.trade_plan_id == plan_id and m.created_at <= now
            )
        except Exception:  # noqa: BLE001 - confirmed Plan survives dependency read failure
            monitors = ()
            monitors_available = False
        return {
            "subject_id": subject_id,
            "plan_id": plan_id,
            "plan_version": version,
            "as_of": now,
            "previous_version": previous.version if previous else None,
            "previous_version_available": version == 1 or previous is not None,
            "field_changes": changes,
            "condition_changes": condition_changes,
            "thesis": {
                "thesis_id": plan.thesis_id,
                "revision_at_plan_creation": baseline.revision_id if baseline else None,
                "current_revision_id": latest.revision_id if latest else None,
                "review_needed": baseline is None or latest is None or baseline != latest,
                "basis": "CONFIRMATION_TIME_ORDER_ONLY",
            },
            "coverage": asdict(coverage),
            "monitors_available": monitors_available,
            "monitors": [
                {
                    "monitor_id": m.monitor_id,
                    "version": m.version,
                    "plan_version": m.trade_plan_version,
                    "status": m.status,
                    "valid_until": m.valid_until,
                    "binding": "EXACT" if m.trade_plan_version == version else "OTHER_VERSION",
                }
                for m in monitors
            ],
            "execution_effect": False,
        }
