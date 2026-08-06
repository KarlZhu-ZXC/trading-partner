"""Test-only validation for the declarative acceptance catalogs and release boundary."""

from __future__ import annotations

import json
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.common.errors import DataContractError
from infrastructure.persistence import orm as _orm  # noqa: F401
from infrastructure.persistence.metadata import Base

EXPECTED_CATEGORY_COUNTS = {
    "continuity": 13,
    "thesis": 11,
    "challenge": 8,
    "a_share": 10,
    "us_market": 9,
    "us_research": 8,
    "us_context": 8,
    "portfolio": 10,
    "workflows": 7,
    "failure": 5,
}
DIALOGUE_CASE_COUNT = sum(EXPECTED_CATEGORY_COUNTS.values())

FORBIDDEN_EVAL_TOOLS = frozenset(
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
EXPECTED_MIGRATION_HEADS = frozenset({"0029_dukascopy_light_oil_cfd"})


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DataContractError("Evaluation catalog is unreadable") from None
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise DataContractError("Evaluation schema_version must be 1")
    return value


def validate_dialogue_catalog(path: Path, public_tools: frozenset[str]) -> None:
    payload = _load(path)
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != DIALOGUE_CASE_COUNT:
        raise DataContractError(
            f"Dialogue catalog must contain exactly {DIALOGUE_CASE_COUNT} cases"
        )
    ids: set[str] = set()
    categories: Counter[str] = Counter()
    covered_tools: set[str] = set()
    for item in cases:
        if not isinstance(item, dict):
            raise DataContractError("Dialogue case must be an object")
        case_id = item.get("id")
        category = item.get("category")
        prompt = item.get("prompt")
        tools = item.get("required_tools")
        assertions = item.get("assertions")
        forbidden = item.get("forbidden_claims")
        required_operations = item.get("required_operations")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise DataContractError("Dialogue ids must be unique nonblank strings")
        if not isinstance(category, str) or category not in EXPECTED_CATEGORY_COUNTS:
            raise DataContractError("Dialogue category is invalid")
        if not isinstance(prompt, str) or not prompt.strip():
            raise DataContractError("Dialogue prompt must be nonblank")
        if not isinstance(tools, list) or not tools or any(not isinstance(x, str) for x in tools):
            raise DataContractError("Dialogue required_tools must be nonempty strings")
        tool_set = frozenset(tools)
        if not tool_set <= public_tools or tool_set & FORBIDDEN_EVAL_TOOLS:
            raise DataContractError("Dialogue references a non-public or forbidden tool")
        if required_operations is not None:
            if not isinstance(required_operations, dict) or not required_operations:
                raise DataContractError("Dialogue required_operations must be a nonempty object")
            if not set(required_operations) <= tool_set:
                raise DataContractError(
                    "Dialogue required_operations must reference required_tools"
                )
            for operations in required_operations.values():
                if (
                    not isinstance(operations, list)
                    or not operations
                    or any(
                        not isinstance(operation, str) or not operation for operation in operations
                    )
                    or len(set(operations)) != len(operations)
                ):
                    raise DataContractError(
                        "Dialogue required_operations must contain unique nonblank strings"
                    )
        if not isinstance(assertions, list) or not assertions:
            raise DataContractError("Dialogue assertions must be nonempty")
        if not isinstance(forbidden, list) or "trade_execution" not in forbidden:
            raise DataContractError("Dialogue must forbid trade execution")
        ids.add(case_id)
        categories[category] += 1
        covered_tools.update(tool_set)
    if dict(categories) != EXPECTED_CATEGORY_COUNTS:
        raise DataContractError("Dialogue category distribution is invalid")
    if covered_tools != set(public_tools):
        raise DataContractError("Dialogue catalog must cover every public tool")


def validate_longitudinal_catalog(path: Path) -> None:
    payload = _load(path)
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise DataContractError("Longitudinal catalog must contain exactly three Cases")
    case_ids: set[str] = set()
    linked = 0
    required_collections = (
        "supporting_evidence",
        "contrary_evidence",
        "assumptions",
        "invalidations",
        "formal_reviews",
        "user_resolutions",
    )
    for item in cases:
        if not isinstance(item, dict):
            raise DataContractError("Longitudinal Case must be an object")
        case_id = item.get("case_id")
        revisions = item.get("revisions")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise DataContractError("Longitudinal case_id must be unique")
        if not isinstance(revisions, list) or len(revisions) < 3:
            raise DataContractError("Longitudinal Case requires at least three revisions")
        for field in required_collections:
            value = item.get(field)
            if not isinstance(value, list) or not value:
                raise DataContractError(f"Longitudinal Case requires {field}")
        if item.get("account_position_link") is True:
            linked += 1
        case_ids.add(case_id)
    if linked < 1:
        raise DataContractError("At least one longitudinal Case requires an account link")


@dataclass(frozen=True, slots=True)
class DeliveryAuditReceipt:
    public_tool_count: int
    table_count: int
    migration_head: str
    dialogue_count: int = DIALOGUE_CASE_COUNT
    longitudinal_case_count: int = 3


def audit_delivery(project_root: Path, public_tools: frozenset[str]) -> DeliveryAuditReceipt:
    root = project_root.resolve()
    if len(public_tools) != 28 or public_tools & FORBIDDEN_EVAL_TOOLS:
        raise DataContractError("Public tool surface is invalid")
    tables = frozenset(Base.metadata.tables)
    if tables & FORBIDDEN_TABLES:
        raise DataContractError("Forbidden execution/backtest tables are present")
    for head in EXPECTED_MIGRATION_HEADS:
        path = root / "migrations" / "versions" / f"{head}.py"
        if not path.is_file():
            raise DataContractError(f"Migration head is missing: {head}")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = tuple(
        str(item).lower() for item in pyproject.get("project", {}).get("dependencies", ())
    )
    if any(
        forbidden in dependency
        for forbidden in FORBIDDEN_RUNTIME_DEPENDENCIES
        for dependency in dependencies
    ):
        raise DataContractError("Forbidden agent/runtime dependency is present")
    validate_dialogue_catalog(root / "evals" / "phase1-dialogues.v1.json", public_tools)
    validate_longitudinal_catalog(root / "evals" / "phase1-longitudinal-cases.v1.json")
    return DeliveryAuditReceipt(
        public_tool_count=len(public_tools),
        table_count=len(tables),
        migration_head=",".join(sorted(EXPECTED_MIGRATION_HEADS)),
    )
