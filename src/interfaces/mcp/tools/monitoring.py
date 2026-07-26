"""Compact Monitoring operation adapters."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from application.dto.monitoring import (
    MonitorCadenceInput,
    MonitorCreateInput,
    MonitorEvaluateInput,
    MonitorEventActionInput,
    MonitorEventListInput,
    MonitorEventResolveInput,
    MonitorGetInput,
    MonitorListInput,
    MonitorRuleInput,
    MonitorStatusInput,
    MonitorUpdateInput,
)
from bootstrap import ApplicationContainer
from domain.monitoring.enums import MonitorCadence
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_monitoring_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact Monitoring operation adapters."""

    # --------------------------------------------------------- Phase 2C Monitoring
    def monitor_create(
        name: str,
        confirmed_by: str,
        idempotency_key: str,
        rules: tuple[MonitorRuleInput, ...] = (),
        case_id: str | None = None,
        primary_instrument_id: str | None = None,
        cadence: MonitorCadenceInput = MonitorCadence.ON_DEMAND,
        valid_until: datetime | None = None,
        trade_plan_id: str | None = None,
        trade_plan_version: int | None = None,
        compile_trade_plan_conditions: bool = False,
    ) -> dict[str, Any]:
        """Create a monitor from explicit rules and/or one confirmed Trade Plan version."""
        try:
            request = MonitorCreateInput.model_validate(
                {
                    "name": name,
                    "case_id": case_id,
                    "primary_instrument_id": primary_instrument_id,
                    "cadence": cadence,
                    "rules": rules,
                    "valid_until": valid_until,
                    "trade_plan_id": trade_plan_id,
                    "trade_plan_version": trade_plan_version,
                    "compile_trade_plan_conditions": compile_trade_plan_conditions,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.monitor_tool_coordinator.create(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def monitor_query(
        monitor_id: str | None = None,
        status: MonitorStatusInput | None = None,
    ) -> dict[str, Any]:
        """Restore one monitor, or filter by ACTIVE/PAUSED/ARCHIVED (case-insensitive)."""
        if monitor_id is None:
            return monitor_list(status)
        try:
            request = MonitorGetInput(monitor_id=monitor_id)
            return container.monitor_tool_coordinator.get(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def monitor_list(status: MonitorStatusInput | None = None) -> dict[str, Any]:
        """List current monitor versions, optionally filtered by status."""
        try:
            request = MonitorListInput.model_validate({"status": status})
            return container.monitor_tool_coordinator.list(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def monitor_update(
        monitor_id: str,
        expected_version: int,
        name: str,
        cadence: MonitorCadenceInput,
        status: MonitorStatusInput,
        confirmed_by: str,
        idempotency_key: str,
        rules: tuple[MonitorRuleInput, ...] = (),
        case_id: str | None = None,
        primary_instrument_id: str | None = None,
        valid_until: datetime | None = None,
        trade_plan_id: str | None = None,
        trade_plan_version: int | None = None,
        compile_trade_plan_conditions: bool = False,
    ) -> dict[str, Any]:
        """Append a confirmed monitor version, including pause/archive changes."""
        try:
            request = MonitorUpdateInput.model_validate(
                {
                    "monitor_id": monitor_id,
                    "expected_version": expected_version,
                    "name": name,
                    "case_id": case_id,
                    "primary_instrument_id": primary_instrument_id,
                    "cadence": cadence,
                    "status": status,
                    "rules": rules,
                    "valid_until": valid_until,
                    "trade_plan_id": trade_plan_id,
                    "trade_plan_version": trade_plan_version,
                    "compile_trade_plan_conditions": compile_trade_plan_conditions,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.monitor_tool_coordinator.update(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def monitor_evaluate(
        monitor_ids: tuple[str, ...] = (),
        cadence: MonitorCadenceInput | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluate active monitors and persist only rule-state transitions."""
        try:
            request = MonitorEvaluateInput.model_validate(
                {"monitor_ids": monitor_ids, "cadence": cadence, "as_of": as_of}
            )
            return (await container.monitor_tool_coordinator.evaluate(request)).model_dump(
                mode="json"
            )
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def monitor_event_list(
        monitor_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List durable monitor transition events with latest resolution."""
        try:
            request = MonitorEventListInput(monitor_id=monitor_id, limit=limit)
            return container.monitor_tool_coordinator.list_events(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    def monitor_event_resolve(
        event_id: str,
        action: MonitorEventActionInput,
        note: str,
        confirmed_by: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Acknowledge or resolve one event; never mutate Thesis or positions."""
        try:
            request = MonitorEventResolveInput.model_validate(
                {
                    "event_id": event_id,
                    "action": action,
                    "note": note,
                    "confirmed_by": confirmed_by,
                    "idempotency_key": idempotency_key,
                }
            )
            return container.monitor_tool_coordinator.resolve_event(request).model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        monitor_create=monitor_create,
        monitor_query=monitor_query,
        monitor_update=monitor_update,
        monitor_evaluate=monitor_evaluate,
        monitor_event_list=monitor_event_list,
        monitor_event_resolve=monitor_event_resolve,
    )
