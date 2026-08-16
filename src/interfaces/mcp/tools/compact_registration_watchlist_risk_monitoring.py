"""Compact MCP registrations for Watchlist, Risk, and Monitoring capabilities."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compact import CapabilityRegistrar


def _register_watchlist_risk_monitoring(
    registry: CapabilityRegistrar,
    watchlist: SimpleNamespace,
    risk: SimpleNamespace,
    monitoring: SimpleNamespace,
) -> None:
    """Register durable Watchlist, Risk, and Monitor operations in order."""

    from .compact import (
        APPEND,
        EVALUATE,
        MANAGE,
        MANAGE_OPEN_WORLD,
        READ_DURABLE,
        READ_PROVIDER,
        _all_fields,
        _copy_handler,
        _register_dispatch_tool,
        _register_flat_dispatch_tool,
        _spec,
    )

    _register_dispatch_tool(
        registry,
        name="watchlist_get",
        description=(
            "Read durable Watchlist groups or items without contacting the upstream provider."
        ),
        variants=(
            _spec(
                "groups",
                watchlist.watchlist_get,
                ("include_inactive",),
                adapter_operation="groups",
                overrides={"refresh": False},
            ),
            _spec(
                "items",
                watchlist.watchlist_get,
                ("group_name", "include_inactive", "limit", "offset"),
                adapter_operation="items",
                overrides={"refresh": False},
            ),
        ),
        policy=READ_DURABLE,
    )
    _register_dispatch_tool(
        registry,
        name="watchlist_manage",
        description=(
            "Add or remove one active-source Watchlist membership with confirmation and "
            "idempotency."
        ),
        variants=(
            _spec("add", watchlist.watchlist_add, _all_fields(watchlist.watchlist_add)),
            _spec(
                "remove",
                watchlist.watchlist_remove,
                _all_fields(watchlist.watchlist_remove),
            ),
        ),
        policy=MANAGE_OPEN_WORLD,
    )
    _register_dispatch_tool(
        registry,
        name="portfolio_risk_get",
        description=(
            "Read the current Risk Policy or run a deterministic non-executing Risk v2 check."
        ),
        variants=(
            _spec("policy", risk.risk_policy_get),
            _spec(
                "check",
                risk.risk_check,
                _all_fields(risk.risk_check),
                overrides={"refresh_accounts": False},
            ),
        ),
        policy=READ_PROVIDER,
    )
    _copy_handler(registry, adapter=risk.risk_policy_update, policy=APPEND)
    _register_dispatch_tool(
        registry,
        name="monitor_read",
        description=(
            "Read the unified Monitor dashboard, definitions/current rule states, "
            "immutable run observations, or transition events."
        ),
        variants=(
            _spec(
                "definitions",
                monitoring.monitor_query,
                _all_fields(monitoring.monitor_query),
            ),
            _spec(
                "events",
                monitoring.monitor_event_list,
                _all_fields(monitoring.monitor_event_list),
            ),
            _spec(
                "dashboard",
                monitoring.monitor_dashboard,
                _all_fields(monitoring.monitor_dashboard),
            ),
            _spec(
                "runs",
                monitoring.monitor_runs,
                _all_fields(monitoring.monitor_runs),
            ),
        ),
        policy=READ_DURABLE,
    )
    _register_flat_dispatch_tool(
        registry,
        name="monitor_manage",
        description=(
            "Create/update a versioned Monitor or resolve one event; never mutate Thesis "
            "or positions. Optional composite judgment policies use deterministic facts "
            "plus a server-side bounded LLM; they never mutate confirmed state. A Trade "
            "Plan-bound Monitor may observe a condition reference "
            "instrument distinct from the plan execution instrument."
        ),
        variants=(
            _spec(
                "create",
                monitoring.monitor_create,
                _all_fields(monitoring.monitor_create),
            ),
            _spec(
                "update",
                monitoring.monitor_update,
                _all_fields(monitoring.monitor_update),
            ),
            _spec(
                "resolve_event",
                monitoring.monitor_event_resolve,
                _all_fields(monitoring.monitor_event_resolve),
            ),
        ),
        policy=MANAGE,
    )
    _copy_handler(registry, adapter=monitoring.monitor_evaluate, policy=EVALUATE)
