"""Phase 1E E1 contract tests: architecture guards and protocol async surfaces."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from application.ports.a_share_providers import (
    A_SHARE_CAPITAL_METRIC_PROTOCOLS,
    A_SHARE_RUNTIME_PROTOCOLS,
    AShareDailyFlowProvider,
    AShareQuoteProvider,
)
from application.ports.category_provider import CategoryProvider
from application.services.criticality_policy import CriticalityPolicy
from domain.common.enums import DataCategory, VendorId

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"

FORBIDDEN_RUNTIME = frozenset({"pandas", "mootdx", "stockstats"})


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_no_forbidden_runtime_imports_in_src() -> None:
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        for imp in _imports(path):
            top = imp.split(".", 1)[0]
            if top in FORBIDDEN_RUNTIME:
                violations.append(f"{path}: {imp}")
            if imp == "references" or imp.startswith("references."):
                violations.append(f"{path}: {imp}")
    assert not violations, "Forbidden runtime imports:\n" + "\n".join(violations)


def test_criticality_policy_complete_for_all_categories() -> None:
    table = CriticalityPolicy.default_table()
    assert set(table) == set(DataCategory)


def test_vendor_id_and_data_category_validation_accepts_new_members() -> None:
    for raw in (
        "sina",
        "cninfo",
        "ths",
        "cls",
        "sse",
        "szse",
        "hkex",
        "iwencai",
        "market_structure",
        "research_reports",
        "interactive_qa",
        "corporate_actions",
    ):
        if raw in {c.value for c in DataCategory}:
            assert DataCategory(raw)
        if raw in {v.value for v in VendorId}:
            assert VendorId(raw)


def test_protocols_extend_category_provider_and_are_async() -> None:
    # Protocols with property members cannot use issubclass(); verify MRO /
    # bases and runtime_checkable flag instead.
    assert CategoryProvider in AShareQuoteProvider.__mro__
    assert CategoryProvider in AShareDailyFlowProvider.__mro__
    for proto in A_SHARE_RUNTIME_PROTOCOLS:
        assert inspect.isclass(proto)
        assert CategoryProvider in proto.__mro__
        assert getattr(proto, "_is_runtime_protocol", False) is True
        methods = [
            name
            for name, member in vars(proto).items()
            if callable(member) and not name.startswith("_")
        ]
        assert methods, proto.__name__
        for name in methods:
            assert inspect.iscoroutinefunction(getattr(proto, name)), f"{proto.__name__}.{name}"


def test_eight_capital_protocols_only() -> None:
    assert len(A_SHARE_CAPITAL_METRIC_PROTOCOLS) == 8
    # Ensure fat capital protocol is not defined in the ports module AST.
    ports_path = SRC / "application" / "ports" / "a_share_providers.py"
    tree = ast.parse(ports_path.read_text(encoding="utf-8"))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "AShareCapitalProvider" not in class_names
    for name in (
        "AShareIntradayFlowProvider",
        "AShareDailyFlowProvider",
        "AShareNorthboundProvider",
        "AShareDragonTigerProvider",
        "AShareMarginProvider",
        "AShareBlockTradeProvider",
        "AShareShareholderProvider",
        "AShareChipProvider",
    ):
        assert name in class_names


def test_domain_a_share_has_no_pydantic() -> None:
    root = SRC / "domain" / "a_share"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        for imp in _imports(path):
            top = imp.split(".", 1)[0]
            if top in {"pydantic", "pydantic_settings", "sqlalchemy", "mcp"}:
                violations.append(f"{path}: {imp}")
    assert not violations
