"""Strict loader for the declarative Phase 1 dialogue and longitudinal evals."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from domain.common.errors import DataContractError

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


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DataContractError("Phase 1 evaluation catalog is unreadable") from None
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise DataContractError("Phase 1 evaluation schema_version must be 1")
    return value


def validate_dialogue_catalog(path: Path, public_tools: frozenset[str]) -> None:
    payload = _load(path)
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != DIALOGUE_CASE_COUNT:
        raise DataContractError(
            f"Phase 1 dialogue catalog must contain exactly {DIALOGUE_CASE_COUNT} cases"
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
                        not isinstance(operation, str) or not operation
                        for operation in operations
                    )
                    or len(set(operations)) != len(operations)
                ):
                    raise DataContractError(
                        "Dialogue required_operations must contain unique nonblank strings"
                    )
        if not isinstance(assertions, list) or not assertions:
            raise DataContractError("Dialogue assertions must be nonempty")
        if (
            not isinstance(forbidden, list)
            or "trade_execution" not in forbidden
            or not forbidden
        ):
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
