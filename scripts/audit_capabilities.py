"""Audit the canonical capability graph without starting the application.

The command intentionally builds a synthetic graph only.  It never loads
``AppSettings``, opens a database, reads a runtime directory, or invokes a
registered handler::

    uv run python scripts/audit_capabilities.py --output /tmp/capability-audit.json

An optional ``--usage`` file may contain an aggregate Console export.  Only the
small, documented aggregate row shape is copied into the report; unknown names
and all other fields are ignored.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, NamedTuple
from unittest.mock import MagicMock

SYNTHETIC_NOW: Final[datetime] = datetime(2026, 1, 1, tzinfo=UTC)
SCHEMA_VERSION: Final[str] = "capability-audit-v1"
SOURCE_COVERAGE: Final[str] = "static_partial"
OBSERVED: Final[str] = "OBSERVED"
NOT_OBSERVED: Final[str] = "NOT_OBSERVED"
KEEP_REVIEW_REQUIRED: Final[str] = "KEEP_REVIEW_REQUIRED"

_SOURCE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx"})
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".next",
        ".turbo",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "generated",
        "node_modules",
    }
)
_EXCLUDED_NAME_PARTS = (".min.", ".map")
_SOURCE_ROOTS = (Path("src"), Path("console") / "app")

_USAGE_SOURCES = frozenset({"MCP", "CONSOLE", "AGENT", "INTERNAL"})
_USAGE_OUTCOMES = frozenset(
    {"OK", "TOOL_FAILURE", "INVALID_REQUEST", "DENIED", "ERROR", "CANCELLED"}
)


class OperationKey(NamedTuple):
    capability: str
    operation: str | None


def _json_bytes(value: Any) -> int:
    """Return the byte length of the deterministic UTF-8 JSON projection."""

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _synthetic_container() -> MagicMock:
    """Create the smallest graph-only container accepted by the factory."""

    container = MagicMock(name="synthetic-capability-audit")
    container.settings = SimpleNamespace(mcp_server_name="synthetic-capability-audit")
    container.context = MagicMock(name="synthetic-context")
    container.context.clock = MagicMock(name="synthetic-clock")
    container.context.clock.now.return_value = SYNTHETIC_NOW
    container.services = MagicMock(name="synthetic-services")
    return container


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _iter_source_files(project_root: Path) -> Iterable[Path]:
    """Yield source files under the two explicitly audited source roots."""

    for relative_root in _SOURCE_ROOTS:
        root = project_root / relative_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(project_root)
            if any(part in _EXCLUDED_PARTS for part in relative.parts):
                continue
            if any(part in relative.name for part in _EXCLUDED_NAME_PARTS):
                continue
            if any(part in {"test", "tests", "__tests__"} for part in relative.parts) or re.search(
                r"(?:\.test|\.spec)\.[^.]+$", relative.name
            ):
                continue
            yield path


def _relative_source_path(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _literal_strings(node: ast.AST) -> tuple[str, ...]:
    return tuple(
        value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(value := item.value, str)
    )


def _node_reference_line(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 1))


def _static_context_role(path: Path, project_root: Path) -> str:
    relative = _relative_source_path(path, project_root)
    if relative in {
        "src/interfaces/capabilities/catalog.py",
        "src/interfaces/console/catalog.py",
        "src/interfaces/mcp/progressive.py",
        "src/interfaces/mcp/tool_inventory.py",
    }:
        return "catalogue"
    if relative == "src/interfaces/capabilities/factory.py" or (
        relative.startswith("src/interfaces/mcp/tools/compact_registration_")
    ):
        return "registration"
    return "other_static_literal"


def _record_static_reference(
    refs: dict[OperationKey, set[tuple[str, int, str, str]]],
    *,
    path: Path,
    project_root: Path,
    line: int,
    capability: str,
    operation: str | None,
) -> None:
    refs[OperationKey(capability, operation)].add(
        (
            _relative_source_path(path, project_root),
            line,
            "static_literal",
            _static_context_role(path, project_root),
        )
    )


class _PythonStaticVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: Path,
        project_root: Path,
        operations_by_capability: Mapping[str, frozenset[str | None]],
        refs: dict[OperationKey, set[tuple[str, int, str, str]]],
    ) -> None:
        self.path = path
        self.project_root = project_root
        self.operations_by_capability = operations_by_capability
        self.refs = refs

    def _record_node(self, node: ast.AST) -> None:
        values = set(_literal_strings(node))
        call_name: str | None = None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
        capabilities = {value for value in values if value in self.operations_by_capability}
        if call_name is not None and call_name in self.operations_by_capability:
            capabilities.add(call_name)
        if not capabilities:
            return
        line = _node_reference_line(node)
        for capability in sorted(capabilities):
            known_operations = self.operations_by_capability[capability]
            # A direct function call or direct tool literal identifies its one
            # operation.  Grouped tools require an exact operation literal too.
            if None in known_operations:
                _record_static_reference(
                    self.refs,
                    path=self.path,
                    project_root=self.project_root,
                    line=line,
                    capability=capability,
                    operation=None,
                )
            operations = {
                value for value in values if value in known_operations and value is not None
            }
            for operation in sorted(operations):
                _record_static_reference(
                    self.refs,
                    path=self.path,
                    project_root=self.project_root,
                    line=line,
                    capability=capability,
                    operation=operation,
                )

    def visit_Call(self, node: ast.Call) -> None:
        self._record_node(node)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        self._record_node(node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        self._record_node(node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_node(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_node(node)
        self.generic_visit(node)


def _strip_js_comments(source: str) -> str:
    """Remove JS comments while preserving strings and line numbers."""

    result: list[str] = []
    index = 0
    length = len(source)
    quote: str | None = None
    escaped = False
    while index < length:
        char = source[index]
        next_char = source[index + 1] if index + 1 < length else ""
        if quote is not None:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            result.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            while index < length and source[index] != "\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index < length:
                if source[index] == "*" and index + 1 < length and source[index + 1] == "/":
                    index += 2
                    break
                if source[index] == "\n":
                    result.append("\n")
                index += 1
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _js_string_literals(source: str) -> Iterable[tuple[int, int, str]]:
    pattern = re.compile(r"(?P<quote>['\"`])(?P<body>(?:\\.|(?!\1).)*?)(?P=quote)")
    for match in pattern.finditer(source):
        body = match.group("body")
        try:
            value = bytes(body, "utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            value = body
        yield match.start(), match.end(), value


def _scan_javascript_file(
    path: Path,
    project_root: Path,
    operations_by_capability: Mapping[str, frozenset[str | None]],
    refs: dict[OperationKey, set[tuple[str, int, str, str]]],
) -> None:
    source = _strip_js_comments(path.read_text(encoding="utf-8"))
    literals = tuple(_js_string_literals(source))
    for index, (start, end, value) in enumerate(literals):
        capability = value if value in operations_by_capability else None
        if capability is None:
            continue
        if None in operations_by_capability[capability]:
            _record_static_reference(
                refs,
                path=path,
                project_root=project_root,
                line=source.count("\n", 0, start) + 1,
                capability=capability,
                operation=None,
            )
        next_capability_start = len(source)
        for next_start, _, next_value in literals[index + 1 :]:
            if next_value in operations_by_capability:
                next_capability_start = next_start
                break
        # Keep matching local to one expression.  This captures the common
        # invoke("cap", {operation: "op"}) shape without claiming every
        # operation on a long JSX line belongs to the first capability.
        context_end = min(next_capability_start, end + 512)
        context = source[end:context_end]
        operation_match = re.search(r"\boperation\s*[:=]\s*['\"`]([^'\"`]+)['\"`]", context)
        if operation_match is None:
            line_end = source.find("\n", end)
            line_end = len(source) if line_end == -1 else line_end
            operation_match = re.search(
                r"\boperation\s*[:=]\s*['\"`]([^'\"`]+)['\"`]",
                source[end:line_end],
            )
        if operation_match is None:
            continue
        operation = operation_match.group(1)
        if operation not in operations_by_capability[capability]:
            continue
        operation_offset = end + operation_match.start(1)
        _record_static_reference(
            refs,
            path=path,
            project_root=project_root,
            line=source.count("\n", 0, operation_offset) + 1,
            capability=capability,
            operation=operation,
        )


def _scan_static_references(
    project_root: Path,
    operation_keys: Iterable[OperationKey],
) -> dict[OperationKey, tuple[dict[str, Any], ...]]:
    """Find literal source references, with comments and generated code removed."""

    operations_by_capability: dict[str, set[str | None]] = defaultdict(set)
    for key in operation_keys:
        operations_by_capability[key.capability].add(key.operation)
    frozen_operations = {
        name: frozenset(values) for name, values in operations_by_capability.items()
    }
    refs: dict[OperationKey, set[tuple[str, int, str, str]]] = defaultdict(set)
    for path in _iter_source_files(project_root):
        if path.suffix.lower() == ".py":
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            _PythonStaticVisitor(
                path=path,
                project_root=project_root,
                operations_by_capability=frozen_operations,
                refs=refs,
            ).visit(tree)
        else:
            try:
                _scan_javascript_file(path, project_root, frozen_operations, refs)
            except (OSError, UnicodeDecodeError):
                continue
    return {
        key: tuple(
            {
                "path": path,
                "line": line,
                "kind": kind,
                "context": context,
            }
            for path, line, kind, context in sorted(
                values,
                key=lambda item: (item[0], item[1], item[2], item[3]),
            )
        )
        for key, values in refs.items()
    }


def _source_reference(value: Any, project_root: Path, *, kind: str) -> dict[str, Any] | None:
    if not callable(value):
        return None
    try:
        target = inspect.unwrap(value)
        path = inspect.getsourcefile(target)
        lines, line = inspect.getsourcelines(target)
    except (OSError, TypeError):
        return None
    if not path or not lines:
        return None
    source_path = Path(path).resolve()
    try:
        relative = source_path.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None
    if not (relative.startswith("src/") or relative.startswith("console/app/")):
        return None
    return {
        "kind": kind,
        "path": relative,
        "line": int(line),
        "symbol": getattr(target, "__qualname__", getattr(target, "__name__", "")),
    }


def _implementation_references(
    invoke: Any,
    project_root: Path,
) -> tuple[dict[str, Any], ...]:
    """Resolve registration and adapter functions from a registered closure."""

    result: set[tuple[str, int, str, str]] = set()
    seen: set[int] = set()

    def visit(value: Any, kind: str, depth: int) -> None:
        if depth > 4 or id(value) in seen:
            return
        seen.add(id(value))
        if callable(value):
            reference = _source_reference(value, project_root, kind=kind)
            if reference is not None:
                result.add(
                    (
                        str(reference["path"]),
                        int(reference["line"]),
                        str(reference["symbol"]),
                        str(reference["kind"]),
                    )
                )
            try:
                unwrapped = inspect.unwrap(value)
            except (OSError, TypeError):
                unwrapped = value
            if unwrapped is not value:
                visit(unwrapped, "unwrapped", depth + 1)
            closure = getattr(value, "__closure__", None)
            if closure:
                for cell in closure:
                    try:
                        contents = cell.cell_contents
                    except ValueError:
                        continue
                    visit(contents, "closure", depth + 1)
            defaults = getattr(value, "__defaults__", None) or ()
            for default in defaults:
                visit(default, "default", depth + 1)
            keyword_defaults = getattr(value, "__kwdefaults__", None) or {}
            for default in keyword_defaults.values():
                visit(default, "default", depth + 1)
            return
        if is_dataclass(value) and not isinstance(value, type):
            for item in fields(value):
                if item.name in {"adapter", "fn", "function", "tool"}:
                    try:
                        visit(getattr(value, item.name), "adapter", depth + 1)
                    except AttributeError:
                        continue
            return
        # RegisteredTool and small closure holder objects use these names.  Do
        # not walk arbitrary objects: MagicMock and model instances can expose
        # private runtime state that has no place in an audit artifact.
        for name in ("adapter", "fn", "function", "tool"):
            try:
                child = getattr(value, name)
            except (AttributeError, TypeError):
                continue
            if callable(child) or is_dataclass(child):
                visit(child, "adapter", depth + 1)

    visit(invoke, "registered_operation", 0)
    return tuple(
        {
            "kind": kind,
            "path": path,
            "line": line,
            "symbol": symbol,
        }
        for path, line, symbol, kind in sorted(result)
    )


def _tool_wire_payload(tool: Any) -> dict[str, Any]:
    """Serialize one MCP Tool without retaining a live SDK object."""

    model_dump = getattr(tool, "model_dump", None)
    if callable(model_dump):
        try:
            payload = model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(payload, dict):
                return payload
        except (TypeError, ValueError):
            pass
    annotations = getattr(tool, "annotations", None)
    if annotations is not None and hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump(mode="json")
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", None),
        "inputSchema": getattr(tool, "inputSchema", {}),
        "outputSchema": getattr(tool, "outputSchema", None),
        "annotations": annotations,
    }


def _tool_measurements(tools: Sequence[Any]) -> dict[str, Any]:
    entries = []
    payloads: list[dict[str, Any]] = []
    for tool in tools:
        payload = _tool_wire_payload(tool)
        payloads.append(payload)
        input_schema = getattr(tool, "inputSchema", {})
        entries.append(
            {
                "name": str(getattr(tool, "name", "")),
                "input_schema_bytes": _json_bytes(input_schema),
                "wire_bytes": _json_bytes(payload),
            }
        )
    return {
        "entrypoint_count": len(entries),
        "input_schema_bytes": sum(item["input_schema_bytes"] for item in entries),
        "wire_bytes": _json_bytes(payloads),
        "wire_bytes_sum": sum(item["wire_bytes"] for item in entries),
        "entrypoints": entries,
    }


def _discovery_measurements(
    registry: Any,
) -> dict[str, Any]:
    from interfaces.capabilities.catalog import CATEGORY_CAPABILITIES
    from interfaces.mcp.progressive import SHORT_DESCRIPTIONS

    names = tuple(name for name in SHORT_DESCRIPTIONS if name in registry.policies)
    full = [{"tool": name, "description": SHORT_DESCRIPTIONS[name]} for name in names]
    categories: dict[str, Any] = {}
    for category, capabilities in CATEGORY_CAPABILITIES.items():
        items = [
            {"tool": name, "description": SHORT_DESCRIPTIONS[name]}
            for name in names
            if name in capabilities
        ]
        categories[str(category)] = {
            "count": len(items),
            "bytes": _json_bytes(items),
        }
    return {
        "full": {"count": len(full), "bytes": _json_bytes(full)},
        "categories": categories,
    }


def _read_usage(
    path: Path | None,
    operation_keys: set[OperationKey],
) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "initialized": None, "rows": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping):
        rows = raw.get("rows", [])
        initialized = raw.get("initialized") if type(raw.get("initialized")) is bool else None
    elif isinstance(raw, list):
        rows = raw
        initialized = None
    else:
        raise ValueError("usage JSON must be an object or array")
    if not isinstance(rows, list):
        raise ValueError("usage JSON rows must be an array")
    safe_rows: list[dict[str, Any]] = []
    ignored = 0
    for row in rows:
        if not isinstance(row, Mapping):
            ignored += 1
            continue
        capability = row.get("capability")
        raw_operation = row.get("operation")
        if raw_operation == "__direct__":
            operation: str | None = None
        elif isinstance(raw_operation, str):
            operation = raw_operation
        else:
            ignored += 1
            continue
        key = OperationKey(
            capability if isinstance(capability, str) else "",
            operation,
        )
        if key not in operation_keys:
            ignored += 1
            continue
        day = row.get("day")
        if day is not None:
            if not isinstance(day, str):
                ignored += 1
                continue
            try:
                usage_day = date.fromisoformat(day)
            except ValueError:
                ignored += 1
                continue
            if usage_day > datetime.now(UTC).date():
                ignored += 1
                continue
        source = row.get("source")
        outcome = row.get("outcome")
        calls = row.get("calls")
        total_elapsed_ms = row.get("total_elapsed_ms")
        max_elapsed_ms = row.get("max_elapsed_ms")
        if (
            not isinstance(source, str)
            or source not in _USAGE_SOURCES
            or not isinstance(outcome, str)
            or outcome not in _USAGE_OUTCOMES
            or type(calls) is not int
            or calls < 1
            or type(total_elapsed_ms) is not int
            or total_elapsed_ms < 0
            or type(max_elapsed_ms) is not int
            or max_elapsed_ms < 0
        ):
            ignored += 1
            continue
        safe_rows.append(
            {
                "source": source,
                "capability": capability,
                "operation": operation,
                "outcome": outcome,
                "calls": calls,
                "total_elapsed_ms": total_elapsed_ms,
                "max_elapsed_ms": max_elapsed_ms,
            }
        )
        if day is not None:
            safe_rows[-1]["day"] = usage_day.isoformat()
    safe_rows.sort(
        key=lambda row: (
            row.get("day", ""),
            row["source"],
            row["capability"],
            row["operation"],
            row["outcome"],
        )
    )
    result: dict[str, Any] = {
        "provided": True,
        "initialized": initialized,
        "rows": safe_rows,
    }
    observed_days = sorted({row["day"] for row in safe_rows if "day" in row})
    dated_rows = sum("day" in row for row in safe_rows)
    result["window"] = {
        "observed_from": observed_days[0] if observed_days else None,
        "observed_to": observed_days[-1] if observed_days else None,
        "observed_day_count": len(observed_days),
        "calendar_span_days": (
            (date.fromisoformat(observed_days[-1]) - date.fromisoformat(observed_days[0])).days + 1
            if observed_days
            else None
        ),
        "dated_row_count": dated_rows,
        "undated_row_count": len(safe_rows) - dated_rows,
        "sources": sorted({row["source"] for row in safe_rows}),
        "basis": "accepted_dated_aggregate_rows",
        "completeness": "best_effort",
    }
    if ignored:
        result["ignored_rows"] = ignored
    return result


def _usage_index(usage: Mapping[str, Any]) -> set[OperationKey]:
    return {
        OperationKey(row["capability"], row["operation"])
        for row in usage.get("rows", [])
        if isinstance(row, Mapping)
        and isinstance(row.get("capability"), str)
        and (isinstance(row.get("operation"), str) or row.get("operation") is None)
    }


def _category_for(capability: str) -> str:
    from interfaces.capabilities.catalog import CATEGORY_CAPABILITIES

    for category, capabilities in CATEGORY_CAPABILITIES.items():
        if capability in capabilities:
            return str(category)
    return "uncategorized"


def _build_report(project_root: Path, usage_path: Path | None = None) -> dict[str, Any]:
    """Build one deterministic report from synthetic graph metadata."""

    from interfaces.capabilities.factory import create_capability_registry
    from interfaces.mcp.chart_artifacts import persist_chart_png
    from interfaces.mcp.server import create_mcp_server

    container = _synthetic_container()
    registry = create_capability_registry(container, chart_persister=persist_chart_png)
    descriptors = tuple(registry.operation_descriptors())
    operation_keys = {OperationKey(item.capability, item.operation) for item in descriptors}
    static_refs = _scan_static_references(project_root, operation_keys)
    usage = _read_usage(usage_path, operation_keys)
    observed_keys = _usage_index(usage)

    async def list_tools() -> list[Any]:
        server = create_mcp_server(container)
        return list(await server.list_tools())

    tools = asyncio.run(list_tools())
    surface = _tool_measurements(tools)
    hypothetical_tools = [
        tool
        for tool in tools
        if getattr(tool, "name", "") not in {"research_judgment_propose", "technical_render_chart"}
    ]
    hypothetical = _tool_measurements(hypothetical_tools)
    hypothetical.update(
        {
            "dropped_entrypoints": [
                "research_judgment_propose",
                "technical_render_chart",
            ],
            "routing_quality_impact": "NOT_MEASURED",
            "recommendation": "KEEP_CURRENT_SURFACE_PENDING_EVIDENCE",
        }
    )

    operations: list[dict[str, Any]] = []
    for descriptor in descriptors:
        key = OperationKey(descriptor.capability, descriptor.operation)
        status = OBSERVED if key in observed_keys else NOT_OBSERVED
        operations.append(
            {
                "capability": descriptor.capability,
                "operation": descriptor.operation,
                "category": _category_for(descriptor.capability),
                "status": status,
                "disposition": OBSERVED if status == OBSERVED else KEEP_REVIEW_REQUIRED,
                "source_evidence": {
                    "implementation_references": _implementation_references(
                        registry._operations[(descriptor.capability, descriptor.operation)].invoke,
                        project_root,
                    ),
                    "static_literal_references": static_refs.get(key, ()),
                },
            }
        )

    capabilities: list[dict[str, Any]] = []
    for capability in registry.registered_capabilities:
        name = capability.tool.name
        capability_operations = [item for item in operations if item["capability"] == name]
        observed = any(item["status"] == OBSERVED for item in capability_operations)
        capabilities.append(
            {
                "name": name,
                "category": _category_for(name),
                "operation_count": len(capability_operations),
                "operations": [item["operation"] for item in capability_operations],
                "status": OBSERVED if observed else NOT_OBSERVED,
                "disposition": OBSERVED if observed else KEEP_REVIEW_REQUIRED,
                "source_evidence": {
                    "implementation_reference_count": sum(
                        len(item["source_evidence"]["implementation_references"])
                        for item in capability_operations
                    ),
                    "static_literal_reference_count": sum(
                        len(item["source_evidence"]["static_literal_references"])
                        for item in capability_operations
                    ),
                },
            }
        )

    discovery = _discovery_measurements(registry)
    return {
        "schema_version": SCHEMA_VERSION,
        "synthetic_clock": SYNTHETIC_NOW.isoformat().replace("+00:00", "Z"),
        "coverage": {
            "dependency": SOURCE_COVERAGE,
            "notes": [
                "Static literal references cover src and console/app only.",
                "Agent and MCP dynamic dispatch is not fully captured.",
                "Direct service, CLI, and Monitor callgraph consumers, including those "
                "inside src, are not fully captured.",
                "Registration and catalogue metadata literals are not invocation or "
                "dependency proof.",
                "Status is runtime usage evidence; static references do not imply invocation.",
                "Observed usage date span is not continuous coverage; undated aggregates "
                "cannot establish an observation window. Missing days are not proof of no use.",
            ],
        },
        "inventory": {
            "capability_count": len(capabilities),
            "operation_count": len(operations),
            "capabilities": capabilities,
            "operations": operations,
        },
        "surface": surface,
        "discovery": discovery,
        "hypothetical_7": hypothetical,
        "usage": usage,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--usage",
        type=Path,
        help="Optional aggregate usage JSON exported by the Console API.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    root = _project_root()
    report = _build_report(root, args.usage)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
