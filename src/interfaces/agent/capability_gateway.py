"""Bounded Agent read/proposal gateway over the transport-neutral compact registry.

This module intentionally does not create an MCP server or expose the private
``tp_*`` names as MCP tools. The gateway searches operation-level descriptors,
checks the read/proposal allow-lists a second time, and delegates to the
registry's closed Pydantic validation/dispatch path.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from application.ports.agent_tool_gateway import (
    AgentCapabilityDescriptor,
    AgentToolDescriptor,
    AgentToolGateway,
    AgentToolReceipt,
    AgentToolResult,
)
from application.services.review_item_service import ReviewItemService
from interfaces.mcp.tools.compact import (
    READ_DURABLE,
    CapabilityNotFoundError,
    CompactCapabilityRegistry,
    CompactOperationDescriptor,
)

DEFAULT_RESULT_MAX_BYTES = 16 * 1024
DEFAULT_SEARCH_LIMIT = 3
MAX_SEARCH_LIMIT = 8
MIN_RESULT_MAX_BYTES = 32
SEARCH_MODES = frozenset({"read", "propose", "prepare_action"})
_PROPOSAL_OPERATIONS = frozenset(
    {
        ("research_judgment_propose", "research_state"),
        ("research_judgment_propose", "thesis_revision"),
    }
)
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "error",
    "exception",
    "header",
    "password",
    "proxy",
    "response",
    "secret",
    "token",
    "traceback",
    "url",
)

# Compact operation descriptions are intentionally English-only registry
# metadata.  Keep conversational aliases here so Chinese requests route
# deterministically without adding translated prose to every public schema.
_SEARCH_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("持仓", ("account_get", "positions", "portfolio_analyze", "exposure")),
    ("仓位", ("account_get", "positions", "portfolio_analyze", "exposure")),
    ("组合", ("portfolio_analyze", "account_get", "positions", "exposure")),
    ("账户", ("account_get", "positions", "transactions")),
    ("交易记录", ("account_get", "transactions")),
    ("自选", ("watchlist_get", "groups", "items")),
    ("关注", ("watchlist_get", "items")),
    ("监控", ("monitor_read", "dashboard", "definitions", "runs", "events")),
    ("告警", ("monitor_read", "dashboard", "events", "runs")),
    ("提醒", ("monitor_read", "dashboard", "events")),
    ("黄金", ("monitor_read", "market_data_get", "quote")),
    ("价格", ("market_data_get", "quote", "quotes")),
    ("行情", ("market_data_get", "quote", "bars")),
    ("技术", ("technical_get_snapshot", "technical_render_chart")),
    ("图表", ("technical_render_chart",)),
    ("风险", ("portfolio_risk_get", "check", "policy")),
    ("研究标的", ("investment_case_read", "query", "context")),
    ("研究档案", ("investment_case_read", "query", "context")),
    ("研究", ("investment_case_read", "research_memory_get", "search", "timeline")),
    ("催化", ("research_memory_get", "agenda")),
    ("健康", ("system_health",)),
    ("数据质量", ("system_health",)),
    ("财报", ("a_share_get_facts", "financials", "us_company_get")),
    ("公告", ("us_company_get", "filings", "company_updates")),
    ("新闻", ("us_company_get", "live_news")),
    (
        "审阅",
        ("decision_workbench_review_queue", "open_items", "summary", "subject"),
    ),
    (
        "复核",
        ("decision_workbench_review_queue", "open_items", "summary", "subject"),
    ),
    (
        "待处理",
        ("decision_workbench_review_queue", "open_items", "summary"),
    ),
    (
        "工作台",
        ("decision_workbench_review_queue", "open_items", "summary", "subject"),
    ),
    (
        "队列",
        ("decision_workbench_review_queue", "open_items", "summary", "subject"),
    ),
    ("review queue", ("decision_workbench_review_queue", "open_items", "summary")),
    ("review", ("decision_workbench_review_queue", "open_items", "summary")),
)

_REVIEW_QUEUE_CAPABILITY = "decision_workbench_review_queue"
_REVIEW_QUEUE_OPERATIONS = ("open_items", "summary", "subject")
_REVIEW_ACTION_OPERATIONS = ("acknowledge", "resolve")
_REVIEW_ADJACENT: tuple[tuple[str, str | None], ...] = (
    (_REVIEW_QUEUE_CAPABILITY, "open_items"),
    (_REVIEW_QUEUE_CAPABILITY, "summary"),
    ("research_memory_get", "agenda"),
    ("monitor_read", "dashboard"),
)
_ROUTING_SENSITIVE_TERMS = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "cookie",
        "proxy",
    }
)


class AgentCapabilityAccessDeniedError(PermissionError):
    """The operation exists but is not auto-readable by Agent-A."""


class AgentToolResultError(RuntimeError):
    """Safe typed error raised only when result compaction cannot proceed."""


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"__bytes__": f"{len(value)} bytes"}
    return str(value)


def _safe_string(value: str, *, max_chars: int = 1024) -> str:
    # Source URLs and exception bodies are never copied into Agent receipts or
    # result projections.  Keep a deterministic marker instead.
    sanitized = _URL.sub("[REDACTED_URL]", value)
    if len(sanitized) <= max_chars:
        return sanitized
    return f"{sanitized[:max_chars]}…[TRUNCATED {len(sanitized) - max_chars} chars]"


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _safe_error_value(value: object, *, depth: int) -> object:
    if isinstance(value, Mapping):
        return {
            key: _safe_projection(value[key], depth=depth + 1)
            for key in ("code", "retryable", "http_status", "status_code")
            if key in value
        }
    if isinstance(value, (list, tuple)):
        return [_safe_error_value(item, depth=depth + 1) for item in value[:64]]
    code = _safe_code(value)
    return code if code is not None else "[REDACTED]"


def _safe_projection(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return {"_truncated": True, "reason": "MAX_DEPTH"}
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)
            if key.lower() in {"error", "errors", "error_code", "error_codes"}:
                projected[key] = _safe_error_value(value[raw_key], depth=depth + 1)
            elif _sensitive_key(key):
                projected[key] = "[REDACTED]"
            else:
                projected[key] = _safe_projection(value[raw_key], depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple, set, frozenset)):
        # Sets are sorted by their safe JSON representation to keep the output
        # stable across processes.
        values = list(value)
        if isinstance(value, (set, frozenset)):
            values.sort(key=lambda item: json.dumps(item, default=_json_default, sort_keys=True))
        limited = values[:128]
        result = [_safe_projection(item, depth=depth + 1) for item in limited]
        if len(values) > len(limited):
            result.append({"_truncated": True, "omitted_items": len(values) - len(limited)})
        return result
    if isinstance(value, str):
        return _safe_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_projection(_json_default(value), depth=depth + 1)


def _encode(value: Any) -> bytes:
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _code_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    codes: list[str] = []
    for item in value:
        raw = item.get("code") if isinstance(item, Mapping) else item
        code = _safe_code(raw)
        if code is not None and code not in codes:
            codes.append(code)
    return codes


def _compact_quote_batch(projected: object, *, original: bytes, max_bytes: int) -> object | None:
    """Keep decision-useful quote facts when a large Agent batch exceeds its cap."""
    if not isinstance(projected, Mapping):
        return None
    data = projected.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
        return None
    marker: dict[str, Any] = {
        "_truncated": True,
        "compaction": "quote_batch_v1",
        "size_bytes": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    for key in ("ok", "degraded", "freshness", "as_of", "fetched_at"):
        if key in projected:
            marker[key] = projected[key]
    compact_items: list[dict[str, Any]] = []
    marker["data"] = {
        key: data[key]
        for key in ("total_requested", "succeeded", "failed")
        if key in data
    }
    marker_data = marker["data"]
    assert isinstance(marker_data, dict)
    marker_data["previous_close_basis_by_asset"] = {
        "equity_etf_index": "previous_completed_regular_session_close",
        "future": "previous_completed_daily_bar_close",
    }
    marker_data["items"] = compact_items
    for raw_item in data["items"]:
        if not isinstance(raw_item, Mapping):
            continue
        result = raw_item.get("result")
        if not isinstance(result, Mapping):
            continue
        quote = result.get("data")
        quote_facts = (
            {
                key: quote[key]
                for key in (
                    "display_price",
                    "previous_close",
                    "quote_at",
                    "session",
                    "price_basis",
                )
                if key in quote
            }
            if isinstance(quote, Mapping)
            else None
        )
        if (
            isinstance(quote, Mapping)
            and isinstance(quote_facts, dict)
            and "display_price" not in quote_facts
            and "last" in quote
        ):
            quote_facts["last"] = quote["last"]
        sources = result.get("sources")
        source_codes = [
            f"{source.get('role', 'unknown')}:{source.get('name', 'unknown')}"
            for source in sources
            if isinstance(source, Mapping)
        ] if isinstance(sources, list) else []
        compact_item = {
            "instrument_id": raw_item.get("instrument_id"),
            "result": {
                key: result[key]
                for key in ("ok", "freshness")
                if key in result
            },
        }
        compact_result = compact_item["result"]
        assert isinstance(compact_result, dict)
        compact_result["data"] = quote_facts
        if source_codes:
            compact_result["source_codes"] = source_codes
        warning_codes = _code_list(result.get("warnings"))
        if warning_codes:
            compact_result["warning_codes"] = warning_codes
        error_codes = _code_list(result.get("errors"))
        if error_codes:
            compact_result["error_codes"] = error_codes
        compact_items.append(compact_item)
        if len(_encode(marker)) > max_bytes:
            compact_items.pop()
            marker_data["omitted_items"] = len(data["items"]) - len(compact_items)
            break
    return marker if compact_items and len(_encode(marker)) <= max_bytes else None


_COMPACTION_ENVELOPE_KEYS = (
    "ok",
    "request_id",
    "as_of",
    "fetched_at",
    "freshness",
    "degraded",
)
def _compact_warning_list(value: object) -> list[object]:
    if not isinstance(value, (list, tuple)):
        return []
    compacted: list[object] = []
    for item in value[:64]:
        if isinstance(item, Mapping):
            entry: dict[str, object] = {}
            for key in ("code", "severity", "retryable", "http_status", "status_code"):
                safe = item.get(key)
                if key == "code":
                    safe = _safe_code(safe)
                if safe is not None:
                    entry[key] = safe
            if entry:
                compacted.append(entry)
        else:
            code = _safe_code(item)
            if code is not None:
                compacted.append(code)
    if len(value) > len(compacted):
        compacted.append({"_truncated": True, "omitted_items": len(value) - len(compacted)})
    return compacted


def _compact_source_list(value: object) -> list[object]:
    if not isinstance(value, (list, tuple)):
        return []
    compacted: list[object] = []
    for item in value[:32]:
        if not isinstance(item, Mapping):
            continue
        entry: dict[str, object] = {}
        for key in ("name", "role", "provider", "basis"):
            safe = item.get(key)
            if isinstance(safe, str) and safe:
                entry[key] = _safe_string(safe, max_chars=128)
        if entry:
            compacted.append(entry)
    if len(value) > len(compacted):
        compacted.append({"_truncated": True, "omitted_items": len(value) - len(compacted)})
    return compacted


def _compact_operation_value(value: object, *, list_limit: int, depth: int = 0) -> object:
    """Bound nested operation data while retaining scalar decision facts."""

    if depth >= 5:
        return {"_truncated": True, "reason": "MAX_DEPTH"}
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)
            item = value[raw_key]
            if isinstance(item, (str, int, float, bool)) or item is None:
                # Keep all scalar top-level facts, and only bounded strings at depth.
                result[key] = _safe_string(item, max_chars=512) if isinstance(item, str) else item
            elif isinstance(item, Mapping):
                result[key] = _compact_operation_value(item, list_limit=list_limit, depth=depth + 1)
            elif isinstance(item, (list, tuple)):
                values = [
                    _compact_operation_value(entry, list_limit=list_limit, depth=depth + 1)
                    for entry in item[:list_limit]
                ]
                if len(item) > len(values):
                    values.append({"_truncated": True, "omitted_items": len(item) - len(values)})
                result[key] = values
            else:
                result[key] = _safe_projection(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        values = [
            _compact_operation_value(entry, list_limit=list_limit, depth=depth + 1)
            for entry in value[:list_limit]
        ]
        if len(value) > len(values):
            values.append({"_truncated": True, "omitted_items": len(value) - len(values)})
        return values
    if isinstance(value, str):
        return _safe_string(value, max_chars=512)
    return value


def _compact_operation_result(
    projected: object,
    *,
    original: bytes,
    max_bytes: int,
    capability: str,
    operation: str,
) -> object | None:
    """Compact high-value read operations without dropping envelope provenance."""

    if not isinstance(projected, Mapping) or not isinstance(
        projected.get("data"), (Mapping, list, tuple)
    ):
        return None
    for list_limit in (64, 32, 16, 8, 4, 2, 1, 0):
        marker: dict[str, object] = {
            "_truncated": True,
            "compaction": f"{capability}_{operation}_v1",
            "size_bytes": len(original),
            "sha256": hashlib.sha256(original).hexdigest(),
        }
        for key in _COMPACTION_ENVELOPE_KEYS:
            if key in projected:
                marker[key] = projected[key]
        if "sources" in projected:
            marker["sources"] = _compact_source_list(projected["sources"])
        for key in ("warnings", "errors"):
            if key in projected:
                marker[key] = _compact_warning_list(projected[key])
        data = projected["data"]
        marker["data"] = _compact_operation_value(data, list_limit=list_limit)
        if len(_encode(marker)) <= max_bytes:
            return marker
    # Keep the durable envelope even when a very small cap cannot fit data.
    marker = {"_truncated": True, "size_bytes": len(original)}
    for key in ("ok", "as_of", "fetched_at", "freshness", "degraded"):
        if key in projected:
            marker[key] = projected[key]
    if len(_encode(marker)) <= max_bytes:
        return marker
    return None


def compact_tool_result(
    value: Any,
    *,
    max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
    capability: str | None = None,
    operation: str | None = None,
) -> Any:
    """Create a deterministic, secret-safe result projection within a byte cap."""

    if max_bytes < MIN_RESULT_MAX_BYTES:
        raise ValueError(f"max_bytes must be at least {MIN_RESULT_MAX_BYTES}")
    projected = _safe_projection(value)
    encoded = _encode(projected)
    if len(encoded) <= max_bytes:
        return projected
    if capability == "market_data_get" and operation == "quotes":
        compact_quotes = _compact_quote_batch(projected, original=encoded, max_bytes=max_bytes)
        if compact_quotes is not None:
            return compact_quotes
    if (
        (capability == "monitor_read" and operation in {"dashboard", "runs"})
        or (capability == "portfolio_analyze" and operation == "exposure")
        or (capability == "research_memory_get" and operation in {"timeline", "search", "agenda"})
        or (
            capability == "us_company_get"
            and operation in {"filings", "live_news", "company_updates"}
        )
        or capability == _REVIEW_QUEUE_CAPABILITY
    ):
        compact_operation = _compact_operation_result(
            projected,
            original=encoded,
            max_bytes=max_bytes,
            capability=capability,
            operation=operation or "direct",
        )
        if compact_operation is not None:
            return compact_operation
    # A digest lets the caller correlate a bounded result without persisting an
    # unbounded provider payload, URL, header, or exception body.
    marker = {
        "_truncated": True,
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    if len(_encode(marker)) <= max_bytes:
        return marker
    compact_marker = {"_truncated": True, "size_bytes": len(encoded)}
    if len(_encode(compact_marker)) <= max_bytes:
        return compact_marker
    return {"_truncated": True}


# Short aliases make the compaction contract convenient for runtime callers.
compact_result = compact_tool_result
compact_value = compact_tool_result


def _serialized_size(value: Any) -> int:
    return len(_encode(value))


def _safe_code(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value):
        return value
    return None


def _safe_request_id(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return None


def _review_queue_schema(operation: str) -> dict[str, Any]:
    """Return an exact private schema for one durable Review Queue read."""

    properties: dict[str, Any] = {
        "operation": {"type": "string", "const": operation},
    }
    required = ["operation"]
    if operation in {"open_items", "subject"}:
        properties["subject_id"] = {"type": ["string", "null"], "maxLength": 128}
    if operation in {"open_items", "subject"}:
        properties["limit"] = {"type": "integer", "minimum": 1, "maximum": 100}
    if operation == "subject":
        properties["subject_id"] = {"type": "string", "minLength": 1, "maxLength": 128}
        required.append("subject_id")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _review_action_schema(operation: str) -> dict[str, Any]:
    """Exact schema exposed only during ``prepare_action`` discovery."""

    properties: dict[str, Any] = {
        "operation": {"type": "string", "const": operation},
        "review_item_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "expected_version": {"type": "integer", "minimum": 1},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
        "authorization_note": {"type": "string", "minLength": 1, "maxLength": 4_000},
        "actor": {"type": "string", "const": "user"},
    }
    required = [
        "operation",
        "review_item_id",
        "expected_version",
        "idempotency_key",
        "authorization_note",
        "actor",
    ]
    if operation == "resolve":
        properties["resolution_note"] = {"type": "string", "minLength": 1, "maxLength": 2_000}
        properties["resolution_ref"] = {"type": ["string", "null"], "maxLength": 256}
        required.append("resolution_note")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _routing_token(value: str) -> str | None:
    """Keep only bounded vocabulary tokens; never persist arbitrary query text."""

    lowered = value.casefold()
    if lowered in _ROUTING_SENSITIVE_TERMS or any(
        marker in lowered for marker in ("password", "secret", "token", "apikey", "api_key")
    ):
        return None
    if value in {alias for alias, _expanded in _SEARCH_ALIASES}:
        return value[:32]
    if re.fullmatch(r"[a-z0-9_:-]{1,64}", value):
        return value
    return None


def _routing_metadata(
    *,
    query: str,
    reason: str,
    matched_terms: Sequence[str],
    adjacent: Sequence[tuple[str, str | None]] = (),
    hints: Sequence[str] = (),
) -> dict[str, Any]:
    normalized = " ".join(str(query).casefold().split())
    vocabulary_values: list[str] = []
    for raw_token in matched_terms:
        token = _routing_token(str(raw_token))
        if token is not None and token not in vocabulary_values:
            vocabulary_values.append(token)
    vocabulary = tuple(vocabulary_values[:16])
    adjacent_values = [
        {
            "capability": capability,
            "operation": operation,
        }
        for capability, operation in adjacent[:8]
    ]
    value: dict[str, Any] = {
        "reason": reason,
        "query_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "matched_terms": list(vocabulary),
        "adjacent": adjacent_values,
    }
    if hints:
        value["hints"] = [str(item)[:160] for item in hints[:8]]
    return value


def _schema_field_terms(schema: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect bounded property names for deterministic schema-field routing."""

    fields: list[str] = []
    stack: list[object] = [schema]
    while stack and len(fields) < 64:
        current = stack.pop()
        if not isinstance(current, Mapping):
            continue
        properties = current.get("properties")
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                name = str(key)
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", name) and name not in fields:
                    fields.append(name)
                stack.append(child)
        for key in ("items", "additionalProperties", "$defs"):
            child = current.get(key)
            if isinstance(child, Mapping):
                stack.append(child)
    return tuple(fields)


def replace_descriptor(
    descriptor: AgentToolDescriptor,
    *,
    routing: Mapping[str, Any],
) -> AgentToolDescriptor:
    """Copy a descriptor while isolating bounded routing metadata."""

    return replace(descriptor, routing=deepcopy(dict(routing)))


def _receipt_from_result(
    *,
    descriptor: CompactOperationDescriptor,
    original: Any,
    compacted: Any,
) -> AgentToolReceipt:
    request_id: str | None = None
    degraded = False
    source_codes: list[str] = []
    warning_codes: list[str] = []
    error_code: str | None = None
    if isinstance(original, Mapping):
        request_id = _safe_request_id(original.get("request_id"))
        degraded = bool(original.get("degraded", False))
        raw_sources = original.get("sources", ())
        if isinstance(raw_sources, Sequence) and not isinstance(raw_sources, (str, bytes)):
            for source in raw_sources:
                if not isinstance(source, Mapping):
                    continue
                name = source.get("name")
                role = source.get("role")
                if not isinstance(name, str) or not name.strip():
                    continue
                label = f"{role}:{name}" if isinstance(role, str) and role else name
                label = label.strip()[:128]
                if label not in source_codes:
                    source_codes.append(label)
        raw_warnings = original.get("warnings", ())
        if isinstance(raw_warnings, Sequence) and not isinstance(raw_warnings, (str, bytes)):
            for warning in raw_warnings:
                raw_code = warning.get("code") if isinstance(warning, Mapping) else warning
                code = _safe_code(raw_code)
                if code is not None and code not in warning_codes:
                    warning_codes.append(code)
        error_code = _safe_code(original.get("error_code"))
        if error_code is None and isinstance(original.get("error"), Mapping):
            error_code = _safe_code(original["error"].get("code"))
        if error_code is None:
            raw_errors = original.get("errors", ())
            if isinstance(raw_errors, Sequence) and not isinstance(raw_errors, (str, bytes)):
                for error in raw_errors:
                    raw_code = error.get("code") if isinstance(error, Mapping) else error
                    error_code = _safe_code(raw_code)
                    if error_code is not None:
                        break
    return AgentToolReceipt(
        capability=descriptor.capability,
        operation=descriptor.operation,
        request_id=request_id,
        effect=descriptor.policy.effect.value,
        degraded=degraded,
        source_codes=tuple(sorted(source_codes)),
        warning_codes=tuple(sorted(warning_codes)),
        error_code=error_code,
        result_size_bytes=_serialized_size(compacted),
        result_truncated=isinstance(compacted, Mapping) and bool(compacted.get("_truncated")),
    )


class AgentCapabilityGateway(AgentToolGateway):
    """Agent-A read/search implementation over :class:`CompactCapabilityRegistry`."""

    def __init__(
        self,
        registry: CompactCapabilityRegistry,
        *,
        result_max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
        search_limit: int = MAX_SEARCH_LIMIT,
        action_allowlist: Sequence[tuple[str, str]] | None = None,
        review_item_service: ReviewItemService | None = None,
        clock: Any | None = None,
    ) -> None:
        if result_max_bytes < MIN_RESULT_MAX_BYTES:
            raise ValueError(f"result_max_bytes must be at least {MIN_RESULT_MAX_BYTES}")
        if search_limit < 1 or search_limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"search_limit must be between 1 and {MAX_SEARCH_LIMIT}")
        self._registry = registry
        self._result_max_bytes = result_max_bytes
        self._search_limit = search_limit
        self._action_allowlist: frozenset[tuple[str, str]] = frozenset(action_allowlist or ())
        self._review_item_service = review_item_service
        self._clock = clock

    @property
    def registry(self) -> CompactCapabilityRegistry:
        return self._registry

    def descriptors(self) -> tuple[AgentToolDescriptor, ...]:
        values = [self._to_descriptor(item) for item in self._registry.operation_descriptors()]
        if self._review_item_service is not None:
            values.extend(
                self._review_descriptor(operation, mode="read")
                for operation in _REVIEW_QUEUE_OPERATIONS
            )
        return tuple(values)

    def set_action_allowlist(self, values: Sequence[tuple[str, str]] | None) -> None:
        """Inject the pending-action operation allowlist without importing its service.

        The runtime wires this from the channel-neutral pending gateway when one
        is configured.  Keeping the list outside the registry prevents a write
        descriptor from becoming an Agent-A read capability by accident.
        """

        self._action_allowlist = frozenset(values or ())

    def descriptor(
        self,
        capability: str,
        operation: str | None = None,
    ) -> AgentToolDescriptor | None:
        """Return one exact descriptor for safe argument-hint generation."""

        if capability == _REVIEW_QUEUE_CAPABILITY:
            if operation in _REVIEW_QUEUE_OPERATIONS and self._review_item_service is not None:
                return self._review_descriptor(operation, mode="read")
            return None
        try:
            return self._to_descriptor(self._registry.find_operation(capability, operation))
        except CapabilityNotFoundError:
            return None

    def _to_descriptor(self, item: CompactOperationDescriptor) -> AgentToolDescriptor:
        return AgentToolDescriptor(
            capability=item.capability,
            operation=item.operation,
            description=item.description,
            schema=deepcopy(item.schema),
            effect=item.policy.effect.value,
            confirmation_required=item.confirmation_required,
            auto_allowed=item.auto_allowed,
            direct=item.direct,
        )

    def _review_descriptor(self, operation: str, *, mode: str) -> AgentToolDescriptor:
        if mode == "prepare_action":
            return AgentToolDescriptor(
                capability=_REVIEW_QUEUE_CAPABILITY,
                operation=operation,
                description=(
                    "Prepare an explicit user-confirmed Review Queue transition; "
                    "this schema never executes automatically."
                ),
                schema=_review_action_schema(operation),
                effect="MANAGE",
                confirmation_required=True,
                auto_allowed=False,
                direct=False,
            )
        description = {
            "open_items": "Read open durable Decision Workbench Review Queue items.",
            "summary": (
                "Read durable Review Queue metrics without reconciliation or Provider calls."
            ),
            "subject": "Read Review Queue items and metrics scoped to one Research Subject.",
        }[operation]
        return AgentToolDescriptor(
            capability=_REVIEW_QUEUE_CAPABILITY,
            operation=operation,
            description=description,
            schema=_review_queue_schema(operation),
            effect="READ_DURABLE",
            confirmation_required=False,
            auto_allowed=True,
            direct=False,
        )

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        *,
        mode: str = "read",
    ) -> tuple[AgentToolDescriptor, ...]:
        """Return exact read or pending-action operation descriptors.

        ``prepare_action`` never invokes an operation; it only exposes schemas
        already present in the injected pending-action allowlist.
        """

        if limit < 1:
            return ()
        if mode not in SEARCH_MODES:
            return ()
        bounded_limit = min(limit, self._search_limit, MAX_SEARCH_LIMIT)
        normalized = " ".join(str(query).lower().split())
        ascii_terms = tuple(
            item for item in re.split(r"[^a-z0-9_]+", normalized) if item
        )
        alias_terms = tuple(
            term
            for alias, expanded in _SEARCH_ALIASES
            if alias in normalized
            for term in expanded
        )
        terms = tuple(dict.fromkeys((*ascii_terms, *alias_terms)))
        candidates: list[CompactOperationDescriptor] = []
        review_candidates: list[AgentToolDescriptor] = []
        if mode == "read":
            candidates = [
                item
                for item in self._registry.operation_descriptors()
                if item.auto_allowed
            ]
            if self._review_item_service is not None:
                # The Review Queue is a Console-only durable capability.  It
                # intentionally never enters the public 27-tool registry.
                review_candidates = [
                    self._review_descriptor(operation, mode="read")
                    for operation in _REVIEW_QUEUE_OPERATIONS
                ]
            else:
                review_candidates = []
        elif mode == "propose":
            candidates = [
                item
                for item in self._registry.operation_descriptors()
                if item.operation is not None
                and (item.capability, item.operation) in _PROPOSAL_OPERATIONS
            ]
            review_candidates = []
        else:
            candidates = [
                item
                for item in self._registry.operation_descriptors()
                if item.operation is not None
                and (item.capability, item.operation) in self._action_allowlist
                and not item.auto_allowed
            ]
            review_candidates = [
                self._review_descriptor(operation, mode="prepare_action")
                for operation in _REVIEW_ACTION_OPERATIONS
                if (_REVIEW_QUEUE_CAPABILITY, operation) in self._action_allowlist
            ]
        if terms:
            candidates = [
                item
                for item in candidates
                if any(
                    term in " ".join(
                        part
                        for part in (
                            item.capability,
                            item.operation or "",
                            item.description,
                            *_schema_field_terms(item.schema),
                        )
                        if part
                    ).lower()
                    for term in terms
                )
            ]
            review_candidates = [
                item
                for item in review_candidates
                if any(
                    term in " ".join(
                        part
                        for part in (
                            item.capability,
                            item.operation or "",
                            item.description,
                            *_schema_field_terms(item.schema),
                        )
                        if part
                    ).lower()
                    for term in terms
                )
            ]
            review_operation_terms = set(terms).intersection(
                _REVIEW_QUEUE_OPERATIONS
                if mode == "read"
                else _REVIEW_ACTION_OPERATIONS
            )
            if review_operation_terms:
                review_candidates = [
                    item for item in review_candidates if item.operation in review_operation_terms
                ]
        elif normalized:
            # A nonblank query with no understood term must not silently return
            # unrelated shortest-schema capabilities.
            candidates = []
            review_candidates = []

        # ``CompactOperationDescriptor`` and ``AgentToolDescriptor`` are kept
        # separate so the private Review Queue can remain outside the MCP
        # inventory.  Normalize both to one deterministic candidate shape.
        candidate_descriptors: list[AgentToolDescriptor] = []
        candidate_descriptors.extend(self._to_descriptor(item) for item in candidates)
        candidate_descriptors.extend(review_candidates)
        if mode == "propose":
            candidate_descriptors = [
                replace(item, confirmation_required=False, auto_allowed=True)
                for item in candidate_descriptors
            ]

        if not candidate_descriptors and normalized:
            adjacent_targets: list[tuple[str, str | None]] = []
            # A known alias with no exact schema still returns the closest
            # safe operation descriptors plus bounded missing-field hints.
            for alias, expanded in _SEARCH_ALIASES:
                if alias in normalized:
                    adjacent_targets = [
                        (item.capability, item.operation)
                        for item in self._registry.operation_descriptors()
                        if item.operation in expanded or item.capability in expanded
                    ]
                    if not adjacent_targets and _REVIEW_QUEUE_CAPABILITY in expanded:
                        adjacent_targets = list(_REVIEW_ADJACENT)
                    break
            adjacent_descriptors: list[AgentToolDescriptor] = []
            for capability, operation in adjacent_targets:
                if (
                    mode == "prepare_action"
                    and (capability, operation) not in self._action_allowlist
                ):
                    continue
                if mode == "propose" and (capability, operation) not in _PROPOSAL_OPERATIONS:
                    continue
                descriptor = (
                    self._review_descriptor(operation, mode="prepare_action")
                    if mode == "prepare_action"
                    and capability == _REVIEW_QUEUE_CAPABILITY
                    and operation in _REVIEW_ACTION_OPERATIONS
                    and (capability, operation) in self._action_allowlist
                    else self.descriptor(capability, operation)
                )
                if descriptor is None:
                    continue
                adjacent_descriptors.append(descriptor)
                if len(adjacent_descriptors) >= bounded_limit:
                    break
            routing = _routing_metadata(
                query=query,
                reason="adjacent_match" if adjacent_descriptors else "no_match",
                matched_terms=terms,
                adjacent=adjacent_targets,
                hints=(
                    "Specify the exact operation and required identifier fields.",
                    "Use a bounded subject_id or instrument_id when the capability is scoped.",
                ),
            )
            return tuple(replace_descriptor(item, routing=routing) for item in adjacent_descriptors)

        def score(item: AgentToolDescriptor) -> tuple[int, int, str, str]:
            haystack = " ".join(
                part
                for part in (
                    item.capability,
                    item.operation or "",
                    item.description,
                    *_schema_field_terms(item.schema),
                )
                if part
            ).lower()
            capability = item.capability.lower()
            operation = (item.operation or "").lower()
            if not terms:
                relevance = 0
            else:
                relevance = sum(
                    100 if term == capability else 60 if term == operation else 20
                    for term in terms
                    if term in haystack
                )
            return (-relevance, len(haystack), item.capability, item.operation or "")

        candidate_descriptors.sort(key=score)
        reason = "exact_match" if terms else "default"
        routing = _routing_metadata(
            query=query,
            reason=reason,
            matched_terms=terms,
            adjacent=_REVIEW_ADJACENT,
        )
        return tuple(
            replace_descriptor(item, routing=routing)
            for item in candidate_descriptors[:bounded_limit]
        )

    def search_audit(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        *,
        mode: str = "read",
    ) -> dict[str, Any]:
        """Return bounded routing metadata for one capability search.

        The raw query is deliberately omitted.  Callers that persist a
        ``tp_capability_search`` receipt should store this object alongside
        the returned descriptor list.
        """

        descriptors = self.search(query, limit, mode=mode)
        routing_values = [dict(item.routing) for item in descriptors if item.routing]
        if routing_values:
            first = routing_values[0]
            return {
                "reason": first.get("reason", "no_match"),
                "query_sha256": first.get("query_sha256"),
                "matched_terms": list(first.get("matched_terms", ()))[:16],
                "adjacent": list(first.get("adjacent", ()))[:8],
                "result_count": len(descriptors),
            }
        normalized = " ".join(str(query).casefold().split())
        return {
            "reason": "no_match" if normalized else "default",
            "query_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "matched_terms": [],
            "adjacent": [],
            "result_count": 0,
        }

    async def _read_review_queue(
        self,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        service = self._review_item_service
        if service is None:
            raise AgentCapabilityAccessDeniedError("Review Queue capability is unavailable")
        if operation not in _REVIEW_QUEUE_OPERATIONS:
            raise CapabilityNotFoundError(f"{_REVIEW_QUEUE_CAPABILITY}:{operation}")
        allowed_keys = {
            "summary": {"operation"},
            "open_items": {"operation", "subject_id", "limit"},
            "subject": {"operation", "subject_id", "limit"},
        }[operation]
        if any(key not in allowed_keys for key in arguments):
            raise ValueError("Review Queue arguments do not match the exact operation schema")
        if "operation" in arguments and arguments["operation"] != operation:
            raise ValueError("Review Queue operation discriminator is invalid")
        subject_id = arguments.get("subject_id")
        if subject_id is not None and not isinstance(subject_id, str):
            raise ValueError("subject_id must be text")
        limit = arguments.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if operation == "summary":
            data = service.metrics(subject_id=subject_id).model_dump(mode="json")
        elif operation == "open_items":
            items = service.list_open(subject_id=subject_id, limit=limit)
            data = {"items": [item.model_dump(mode="json") for item in items]}
        else:
            if not isinstance(subject_id, str) or not subject_id.strip():
                raise ValueError("subject_id is required")
            items = service.list_open(subject_id=subject_id, limit=limit)
            data = {
                "subject_id": subject_id,
                "items": [item.model_dump(mode="json") for item in items],
                "metrics": service.metrics(subject_id=subject_id).model_dump(mode="json"),
            }
        now = self._clock.now() if self._clock is not None else datetime.now().astimezone()
        raw = {
            "ok": True,
            "request_id": f"review_queue_{operation}",
            "as_of": now.isoformat(),
            "fetched_at": now.isoformat(),
            "freshness": "durable",
            "degraded": False,
            "sources": [{"name": "review_queue", "role": "PRIMARY", "basis": "durable_only"}],
            "warnings": [],
            "errors": [],
            "data": data,
        }
        compacted = compact_tool_result(
            raw,
            max_bytes=self._result_max_bytes,
            capability=_REVIEW_QUEUE_CAPABILITY,
            operation=operation,
        )
        descriptor = self._review_descriptor(operation, mode="read")
        return AgentToolResult(
            result=compacted,
            receipt=_receipt_from_result(
                descriptor=CompactOperationDescriptor(
                    capability=descriptor.capability,
                    operation=descriptor.operation,
                    description=descriptor.description,
                    schema=descriptor.schema,
                    policy=READ_DURABLE,
                    direct=False,
                ),
                original=raw,
                compacted=compacted,
            ),
        )

    async def read(
        self,
        capability: str,
        operation: str | None,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        """Policy-check, exact-validate, and execute one read operation."""

        if capability == _REVIEW_QUEUE_CAPABILITY:
            return await self._read_review_queue(operation or "", arguments)
        try:
            descriptor = self._registry.find_operation(capability, operation)
        except CapabilityNotFoundError:
            raise
        if not descriptor.auto_allowed:
            raise AgentCapabilityAccessDeniedError(
                f"Agent-A read is not allowed for {capability}:{operation or ''}".rstrip(":")
            )
        # ``invoke_validated`` does not inject or check a confirmation.  This
        # is important for technical_render_chart: its public MCP policy is
        # unchanged, while Agent-A reaches the explicit internal read path.
        raw_result = await self._registry.invoke_validated(
            capability,
            descriptor.operation,
            dict(arguments),
            enforce_confirmation=False,
        )
        compacted = compact_tool_result(
            raw_result,
            max_bytes=self._result_max_bytes,
            capability=descriptor.capability,
            operation=descriptor.operation,
        )
        return AgentToolResult(
            result=compacted,
            receipt=_receipt_from_result(
                descriptor=descriptor,
                original=raw_result,
                compacted=compacted,
            ),
        )

    async def propose(
        self,
        capability: str,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AgentToolResult:
        """Create one bounded proposal; final domain state remains unchanged."""

        if (capability, operation) not in _PROPOSAL_OPERATIONS:
            raise AgentCapabilityAccessDeniedError(
                f"Agent proposal is not allowed for {capability}:{operation}"
            )
        descriptor = self._registry.find_operation(capability, operation)
        payload = arguments.get("payload")
        if not isinstance(payload, Mapping):
            raise AgentCapabilityAccessDeniedError("Agent proposal payload is invalid")
        kind = payload.get("kind")
        if operation == "thesis_revision" and kind != "thesis_revision":
            raise AgentCapabilityAccessDeniedError("Agent Thesis proposal kind is invalid")
        if operation == "research_state" and kind not in {"watchlist_item", "trade_plan"}:
            raise AgentCapabilityAccessDeniedError("Agent Research proposal kind is invalid")
        raw_result = await self._registry.invoke_validated(
            capability,
            descriptor.operation,
            dict(arguments),
            enforce_confirmation=False,
        )
        compacted = compact_tool_result(
            raw_result,
            max_bytes=self._result_max_bytes,
            capability=descriptor.capability,
            operation=descriptor.operation,
        )
        return AgentToolResult(
            result=compacted,
            receipt=_receipt_from_result(
                descriptor=descriptor,
                original=raw_result,
                compacted=compacted,
            ),
        )


# Friendly aliases for adapters that name this boundary simply "CapabilityGateway".
CapabilityGateway = AgentCapabilityGateway
AgentGateway = AgentCapabilityGateway
CapabilityDescriptor = AgentCapabilityDescriptor


def create_agent_capability_gateway(
    registry: CompactCapabilityRegistry,
    *,
    result_max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
    search_limit: int = MAX_SEARCH_LIMIT,
    action_allowlist: Sequence[tuple[str, str]] | None = None,
) -> AgentCapabilityGateway:
    return AgentCapabilityGateway(
        registry,
        result_max_bytes=result_max_bytes,
        search_limit=search_limit,
        action_allowlist=action_allowlist,
    )


build_agent_capability_gateway = create_agent_capability_gateway


__all__ = [
    "AgentCapabilityAccessDeniedError",
    "AgentCapabilityGateway",
    "AgentCapabilityDescriptor",
    "AgentGateway",
    "CapabilityGateway",
    "CapabilityDescriptor",
    "DEFAULT_RESULT_MAX_BYTES",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "MIN_RESULT_MAX_BYTES",
    "build_agent_capability_gateway",
    "compact_result",
    "compact_tool_result",
    "compact_value",
    "create_agent_capability_gateway",
]
