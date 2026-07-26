"""Compact final Phase 1 public-surface, schema, dependency, and eval audit."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from domain.common.errors import DataContractError
from infrastructure.evaluation.phase1_catalog import (
    validate_dialogue_catalog,
    validate_longitudinal_catalog,
)
from infrastructure.persistence import models as _models  # noqa: F401
from infrastructure.persistence.metadata import Base

FORBIDDEN_PUBLIC = frozenset(
    {
        "order_place",
        "order_modify",
        "order_cancel",
        "trade_unlock",
        "evidence_create",
        "report_create",
        "event_create",
    }
)
FORBIDDEN_TABLES = frozenset(
    {
        "strategies",
        "backtest_runs",
        "backtest_trades",
        "orders",
        "fills",
        "execution_approvals",
    }
)
FORBIDDEN_RUNTIME_DEPENDENCIES = ("tradingagents", "langgraph", "minimax", "grok")
_PHASE1_MIGRATION_HEADS = frozenset({"0022_workflow_execution_replay"})


@dataclass(frozen=True, slots=True)
class Phase1DeliveryAuditReceipt:
    public_tool_count: int
    table_count: int
    migration_head: str
    dialogue_count: int = 89
    longitudinal_case_count: int = 3


class Phase1DeliveryAuditor:
    def audit(
        self, project_root: Path, public_tools: frozenset[str]
    ) -> Phase1DeliveryAuditReceipt:
        root = project_root.resolve()
        if len(public_tools) != 28 or public_tools & FORBIDDEN_PUBLIC:
            raise DataContractError("Public tool surface is invalid")
        tables = frozenset(Base.metadata.tables)
        if tables & FORBIDDEN_TABLES:
            raise DataContractError("Forbidden execution/backtest tables are present")
        for head in _PHASE1_MIGRATION_HEADS:
            path = root / "migrations" / "versions" / f"{head}.py"
            if not path.is_file():
                raise DataContractError(f"Phase 1 migration head is missing: {head}")
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = tuple(
            str(item).lower()
            for item in pyproject.get("project", {}).get("dependencies", ())
        )
        if any(
            forbidden in dependency
            for forbidden in FORBIDDEN_RUNTIME_DEPENDENCIES
            for dependency in dependencies
        ):
            raise DataContractError("Forbidden agent/runtime dependency is present")
        validate_dialogue_catalog(
            root / "evals" / "phase1-dialogues.v1.json", public_tools
        )
        validate_longitudinal_catalog(
            root / "evals" / "phase1-longitudinal-cases.v1.json"
        )
        return Phase1DeliveryAuditReceipt(
            public_tool_count=len(public_tools),
            table_count=len(tables),
            migration_head=",".join(sorted(_PHASE1_MIGRATION_HEADS)),
        )
